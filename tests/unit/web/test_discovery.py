"""Unit tests for `remo_cli.web.discovery.DiscoveryService` (T025).

Covers FR-004 (hot reload, no restart)/FR-005 (concurrency/timeout knobs,
cache TTL, manual refresh) from specs/010-web-session-interface/tasks.md.

`get_capabilities`/`list_sessions` are mocked at the `remo_cli.web.discovery`
module level (the names discovery.py imports directly), so no real SSH
transport is exercised here — that's covered by the integration test
(T024, tests/integration/test_remo_host_e2e.py).
"""

from __future__ import annotations

import time

import pytest

from remo_cli.core.remo_host_client import ProjectEntry, SshTransportError
from remo_cli.models.capability import RemoteCapability
from remo_cli.models.discovery import InstanceStatus
from remo_cli.models.session_target import DevcontainerRunning, ZellijState
from remo_cli.web import discovery as discovery_module
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import DiscoveryService

pytestmark = pytest.mark.usefixtures("tmp_config_dir")


def _write_registry(tmp_config_dir, hosts: list[tuple[str, str]]) -> None:
    """Write known_hosts lines for `[(type, name), ...]` under tmp_config_dir.

    tmp_config_dir (from tests/conftest.py) already points REMO_HOME at a
    writable temp directory, so this is a safe way to control the registry
    a DiscoveryService's read-only accessor will see. Each host gets a
    distinct 127.0.0.x IP so mocks can distinguish hosts by inspecting the
    ssh target string (`user@host`) they were called with.
    """
    lines = [
        f"{type_}:{name}:127.0.0.{i + 1}:remo" for i, (type_, name) in enumerate(hosts)
    ]
    (tmp_config_dir / "known_hosts").write_text("\n".join(lines) + "\n")


def _capability() -> RemoteCapability:
    return RemoteCapability(
        protocol_version=1,
        host_tools_version="2.1.0",
        projects_root="/home/remo/projects",
    )


def _entries(*names: str) -> list[ProjectEntry]:
    return [
        ProjectEntry(
            name=name,
            has_devcontainer=False,
            zellij_state=ZellijState.ACTIVE,
            devcontainer_running=DevcontainerRunning.UNKNOWN,
        )
        for name in names
    ]


# ---------------------------------------------------------------------------
# Concurrency / timeout knobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrency_bounds_wall_clock(tmp_config_dir, monkeypatch):
    """4 hosts x ~80ms each, concurrency=2 -> wall clock << 4x serial time."""
    num_hosts = 4
    delay = 0.08
    hosts = [("incus", f"host{i}") for i in range(num_hosts)]
    _write_registry(tmp_config_dir, hosts)

    def _slow_get_capabilities(ssh_argv_prefix, *, timeout=None, **kwargs):
        time.sleep(delay)
        return _capability()

    monkeypatch.setattr(discovery_module, "get_capabilities", _slow_get_capabilities)
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    settings = WebSettings(discovery_concurrency=2, discovery_timeout_s=5.0)
    service = DiscoveryService(settings)

    start = time.monotonic()
    await service.refresh()
    elapsed = time.monotonic() - start

    serial_time = num_hosts * delay
    # Generous tolerance: proves bounded concurrency without being flaky.
    assert elapsed < serial_time * 0.75, (
        f"expected concurrency to bound wall-clock well under serial "
        f"({serial_time:.3f}s), got {elapsed:.3f}s"
    )

    snapshots = service.get_snapshot()
    assert len(snapshots) == num_hosts
    assert all(s.status is InstanceStatus.OK for s in snapshots)


# ---------------------------------------------------------------------------
# Per-host failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_host_failure_isolation(tmp_config_dir, monkeypatch):
    hosts = [("incus", "good1"), ("incus", "bad"), ("incus", "good2")]
    _write_registry(tmp_config_dir, hosts)

    def _get_capabilities(ssh_argv_prefix, *, timeout=None, **kwargs):
        target = ssh_argv_prefix[-1]
        if "127.0.0.2" in target:  # "bad" host's IP, per _write_registry
            raise SshTransportError("Connection refused", returncode=255)
        return _capability()

    monkeypatch.setattr(discovery_module, "get_capabilities", _get_capabilities)
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    service = DiscoveryService(WebSettings(discovery_concurrency=4, discovery_timeout_s=5.0))
    await service.refresh()

    snapshots = {s.instance_name: s for s in service.get_snapshot()}
    assert len(snapshots) == 3

    bad = snapshots["bad"]
    assert bad.status is not InstanceStatus.OK
    assert bad.error is not None
    assert bad.error.code
    assert bad.targets == []

    for name in ("good1", "good2"):
        good = snapshots[name]
        assert good.status is InstanceStatus.OK
        assert good.error is None
        assert len(good.targets) == 1


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cache_ttl_skips_refresh_when_fresh(tmp_config_dir, monkeypatch):
    _write_registry(tmp_config_dir, [("incus", "host0")])

    call_count = {"n": 0}

    def _get_capabilities(ssh_argv_prefix, *, timeout=None, **kwargs):
        call_count["n"] += 1
        return _capability()

    monkeypatch.setattr(discovery_module, "get_capabilities", _get_capabilities)
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    service = DiscoveryService(WebSettings(discovery_cache_ttl_s=60.0, discovery_timeout_s=5.0))

    await service.refresh(force=False)
    assert call_count["n"] == 1
    first_refreshed_at = service.last_refreshed_at

    # Second TTL-gated refresh within the TTL window: no new discovery calls.
    await service.refresh(force=False)
    assert call_count["n"] == 1
    assert service.last_refreshed_at == first_refreshed_at

    # Simulate TTL expiry by making later reads of `time.monotonic()` look
    # like a large amount of time has passed. Capture the *real* monotonic
    # function first -- `discovery_module.time` is the same `time` module
    # object as the stdlib one, so patching its `monotonic` attribute here
    # affects every caller (including asyncio internals), and a naive
    # `lambda: time.monotonic() + N` would recurse into itself once patched.
    real_monotonic = time.monotonic
    monkeypatch.setattr(
        discovery_module.time, "monotonic", lambda: real_monotonic() + 3600.0
    )
    await service.refresh(force=False)
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_manual_refresh_bypasses_ttl(tmp_config_dir, monkeypatch):
    _write_registry(tmp_config_dir, [("incus", "host0")])

    call_count = {"n": 0}

    def _get_capabilities(ssh_argv_prefix, *, timeout=None, **kwargs):
        call_count["n"] += 1
        return _capability()

    monkeypatch.setattr(discovery_module, "get_capabilities", _get_capabilities)
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    service = DiscoveryService(WebSettings(discovery_cache_ttl_s=3600.0, discovery_timeout_s=5.0))

    # Default force=True: every explicit call re-runs discovery, regardless
    # of how fresh the cache still is.
    await service.refresh()
    await service.refresh()
    await service.refresh()
    assert call_count["n"] == 3


# ---------------------------------------------------------------------------
# Registry hot reload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_hot_reload_without_restart(tmp_config_dir, monkeypatch):
    monkeypatch.setattr(
        discovery_module, "get_capabilities", lambda *a, **k: _capability()
    )
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    _write_registry(tmp_config_dir, [("incus", "alpha")])
    service = DiscoveryService(WebSettings(discovery_timeout_s=5.0))

    await service.refresh()
    names_after_first = {s.instance_name for s in service.get_snapshot()}
    assert names_after_first == {"alpha"}

    # Registry changes on disk (host removed, new host added) -- no restart,
    # no new DiscoveryService instance.
    _write_registry(tmp_config_dir, [("incus", "beta")])
    await service.refresh()

    names_after_second = {s.instance_name for s in service.get_snapshot()}
    assert names_after_second == {"beta"}


@pytest.mark.asyncio
async def test_find_target_and_get_targets(tmp_config_dir, monkeypatch):
    _write_registry(tmp_config_dir, [("incus", "host0")])
    monkeypatch.setattr(
        discovery_module, "get_capabilities", lambda *a, **k: _capability()
    )
    monkeypatch.setattr(
        discovery_module, "list_sessions", lambda *a, **k: _entries("proj1", "proj2")
    )

    service = DiscoveryService(WebSettings(discovery_timeout_s=5.0))
    assert service.get_targets() == []
    assert service.find_target("nonexistent") is None

    await service.refresh()

    targets = service.get_targets()
    assert len(targets) == 2
    for target in targets:
        assert service.find_target(target.id) is target
