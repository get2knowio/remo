"""Unit tests for `remo_cli.web.discovery.DiscoveryService` (T025).

Covers FR-004 (hot reload, no restart)/FR-005 (concurrency/timeout knobs,
cache TTL, manual refresh) from specs/010-web-session-interface/tasks.md.

`get_capabilities`/`list_sessions` are mocked at the `remo_cli.web.discovery`
module level (the names discovery.py imports directly), so no real SSH
transport is exercised here — that's covered by the integration test
(T024, tests/integration/test_remo_host_e2e.py).
"""

from __future__ import annotations

import threading
import time

import pytest

from remo_cli.core.remo_host_client import ProjectEntry, SshTransportError
from remo_cli.models.capability import RemoteCapability
from remo_cli.models.discovery import InstanceStatus
from remo_cli.models.session_target import DevcontainerRunning, ZellijState
from remo_cli.web import discovery as discovery_module
from remo_cli.web.config import WebSettings
from remo_cli.models.host import KnownHost
from remo_cli.web.discovery import DiscoveryService, _snapshot, configure_remediation

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
async def test_concurrency_is_bounded_by_the_setting(tmp_config_dir, monkeypatch):
    """`discovery_concurrency` caps how many probes run at once — measured.

    This used to time the whole refresh and assert it beat 75% of the serial
    duration. That inferred concurrency from a stopwatch, so a loaded machine
    (a busy CI runner, or a laptop mid-release) failed it with nothing wrong:
    it flaked during the 4.0.1 release gate on a commit that touched no Python
    at all.

    Counting the probes actually in flight tests the real property instead, and
    a stricter one — the old assertion passed for ANY concurrency above ~1.4x,
    including an unbounded fan-out that ignored the setting entirely.
    """
    concurrency = 2
    num_hosts = 4
    hosts = [("incus", f"host{i}") for i in range(num_hosts)]
    _write_registry(tmp_config_dir, hosts)

    lock = threading.Lock()
    in_flight = 0
    peak = 0
    # A rendezvous for exactly `concurrency` probes. It is what proves the
    # probes genuinely OVERLAP: a serial implementation can never assemble a
    # party, so it trips the timeout instead of quietly passing. With 4 hosts
    # and a bound of 2 the barrier fills twice, and resets itself in between.
    barrier = threading.Barrier(concurrency)

    def _instrumented_get_capabilities(ssh_argv_prefix, *, timeout=None, **kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        try:
            barrier.wait(timeout=2)
        except threading.BrokenBarrierError:
            # Nobody else arrived: concurrency is lower than configured. Let the
            # assertions below report that, rather than hanging the suite.
            pass
        # Keep hold of the slot briefly. Without this the rendezvousing pair
        # returns so fast that an implementation ignoring the bound gets its
        # extra probes in AFTER the count drops, and the peak reads as 2 when 4
        # were really running (verified: the assertion missed a Semaphore(999)
        # mutant until this dwell was added). The dependency is one-directional
        # and therefore safe — a slower machine makes the overlap easier to
        # observe, never harder, so this can under-report a bug but cannot
        # invent one.
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return _capability()

    monkeypatch.setattr(discovery_module, "get_capabilities", _instrumented_get_capabilities)
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    # discovery_timeout_s must comfortably exceed the barrier's own timeout.
    # At 5s each they raced: a serialized implementation left probe 1 parked at
    # the barrier until wait_for fired, and wait_for RELEASES THE SEMAPHORE
    # while the executor thread keeps running — so probe 2 overlapped a probe
    # that was only still there because of the timeout, and the peak read 2 on
    # an implementation with a bound of 1.
    settings = WebSettings(discovery_concurrency=concurrency, discovery_timeout_s=30.0)
    service = DiscoveryService(settings)

    await service.refresh()

    assert peak == concurrency, (
        f"expected at most {concurrency} probes in flight at once (and at least "
        f"that many overlapping), observed a peak of {peak} across {num_hosts} hosts"
    )


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


@pytest.mark.asyncio
async def test_evict_drops_one_snapshot_and_its_targets(tmp_config_dir, monkeypatch):
    # The registry-admin remove path prunes ONE instance without the SSH cost
    # of a full refresh: its snapshot and flattened targets disappear, other
    # instances' stay.
    _write_registry(tmp_config_dir, [("incus", "host0"), ("incus", "host1")])
    monkeypatch.setattr(discovery_module, "get_capabilities", lambda *a, **k: _capability())
    monkeypatch.setattr(discovery_module, "list_sessions", lambda *a, **k: _entries("proj1"))

    service = DiscoveryService(WebSettings(discovery_timeout_s=5.0))
    await service.refresh()
    snapshots = {s.instance_name: s for s in service.get_snapshot()}
    assert set(snapshots) == {"host0", "host1"}

    await service.evict(snapshots["host0"].instance_id)
    assert {s.instance_name for s in service.get_snapshot()} == {"host1"}
    for target in service.get_targets():
        assert target.instance_name == "host1"

    # Unknown id: a clean no-op.
    await service.evict("not-a-real-id")
    assert {s.instance_name for s in service.get_snapshot()} == {"host1"}


# ---------------------------------------------------------------------------
# `no_remo_host` remediation
# ---------------------------------------------------------------------------


class TestConfigureRemediation:
    """The remediation must name a command that exists for *this* host.

    It used to read "re-run configure" for every instance. For a `remo add`
    host that pointed at nothing — provisioning an added host was out of scope
    until `remo configure` — so the console told the operator to run a command
    the CLI would reject as unknown.
    """

    def _host(self, type_: str, name: str) -> KnownHost:
        return KnownHost(
            type=type_,
            name=name,
            host="10.0.0.5",
            user="remo",
            instance_id="",
            access_mode="direct",
        )

    def test_added_ssh_host_gets_the_provider_neutral_verb(self):
        remediation = configure_remediation(self._host("ssh", "mbp"))
        assert "remo configure mbp" in remediation

    def test_provider_host_keeps_its_own_upgrade_verb(self):
        # Routing a provider host through the generic play would configure it
        # with the wrong one (no SSM ProxyCommand, no cloud-key bootstrap).
        remediation = configure_remediation(self._host("hetzner", "web1"))
        assert "remo hetzner upgrade web1" in remediation
        assert "remo configure" not in remediation

    def test_host_scoped_name_is_shortened_for_the_cli(self):
        # incus/proxmox register as "node/container" but their CLI takes the
        # container part alone, so the full name would not be accepted.
        remediation = configure_remediation(self._host("incus", "node1/dev"))
        assert "remo incus upgrade dev" in remediation


class TestSnapshotDoesNotPublishTheKeyPath:
    """The snapshot's `region` reaches the browser and is rendered as a badge.

    For an added host the registry's `region` slot holds the operator's PRIVATE
    KEY PATH, so copying it verbatim published that path to everyone with
    console access. Covering the model property alone is not enough — this
    pins the call site, which is what a revert would touch.
    """

    def test_added_host_snapshot_carries_no_key_path(self):
        host = KnownHost(
            type="ssh",
            name="mbp",
            host="10.0.0.5",
            user="remo",
            instance_id="2222",
            access_mode="direct",
            region="/home/me/.ssh/id_ed25519",
        )

        snap = _snapshot("iid", host, InstanceStatus.OK)

        assert snap.region == ""
        assert "id_ed25519" not in snap.region

    def test_a_real_region_still_reaches_the_console(self):
        host = KnownHost(
            type="aws", name="dev", host="h", user="remo", region="us-west-2"
        )
        assert _snapshot("iid", host, InstanceStatus.OK).region == "us-west-2"
