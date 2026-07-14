"""E2E fixture + test: nine real terminals, no cross-routing (T043, SC-003).

The "motivating example" scenario from spec.md User Story 3 / quickstart.md
V4 ("Open all nine, no cross-routing"): three addressable SSH targets, three
projects discovered on each (one project name -- ``shared`` -- deliberately
repeated across all three instances), nine real `POST /terminals` -> WS
upgrade -> PTY/ssh attachments opened concurrently, each fed a unique input
marker, each asserted to receive back ONLY its own attach banner + marker and
NEVER another terminal's (SC-003: "never cross-routed ... even when project
names repeat across instances").

Builds directly on the two prior integration tests' proven techniques:

* The disposable-SSH-target Docker fixture (Alpine+OpenSSH, throwaway
  ssh-agent identity, scoped ``~/.ssh/known_hosts`` trust with a
  ``finally``-restore, an injected fake ``remo-host`` script) from
  ``test_remo_host_e2e.py`` -- duplicated here (matching
  ``test_terminal_attach.py``'s precedent of duplicating rather than
  cross-importing test internals) and scaled to THREE containers.
* The single-terminal REST->WS->byte-roundtrip->disconnect flow from
  ``test_terminal_attach.py`` -- scaled to nine terminals opened concurrently
  against a REAL ``DiscoveryService`` populated by real SSH discovery
  (option (a) from the task: a real registry file naming the three disposable
  containers, real ``refresh()``, no injected/stubbed targets).

Concurrency note: this test drives all nine terminals over a REAL loopback
TCP server (`uvicorn.Server` run as an `asyncio.Task` on the test's own event
loop) using a minimal hand-rolled async HTTP POST (REST) + the `websockets`
client library (WS), all pure-asyncio -- NOT `fastapi.testclient.TestClient`.
An earlier version of
this test drove nine concurrent `TestClient` WS sessions from nine
`asyncio.to_thread` worker threads sharing TestClient's single background
`anyio` blocking portal; that reproducibly hung (verified via `ps`: zero ssh
child processes and zero accepted connections on any container's sshd despite
the app-level "ready" control frame already having been sent), while the
IDENTICAL production `TerminalSession`/`build_attach_argv` code driven by
nine plain `asyncio.gather`-ed coroutines (no threads at all, see
`test_ws_terminal_attach_roundtrip_over_real_ssh`'s single-terminal precedent
scaled up) reliably completed in about a second. That symptom matches
`asyncio.create_subprocess_exec`'s `fork()` racing against unrelated
concurrent activity on OTHER OS threads inside the same process (the classic
multi-threaded-fork hazard) -- something nine real threads hammering a shared
cross-thread portal can trigger and nine plain asyncio tasks on one thread
cannot. Running a real server + real async clients sidesteps that class of
bug entirely and is arguably a more faithful stand-in for nine real browser
tabs than nine threads sharing one in-process ASGI transport.

Docker-gated: if Docker/network is unavailable the whole module skips
(matching both prior tests' precedent), but the real path is attempted
first.

Dependency note: the REST leg uses a hand-rolled minimal async HTTP/1.1 POST
over `asyncio.open_connection` rather than `httpx.AsyncClient` -- `httpx` is
NOT a declared project dependency (`uv tree` confirms it isn't in
`uv.lock`; it only happens to be present in some dev sandboxes as an
unrelated transitive install some `fastapi.testclient.TestClient` users pull
in out of band). This test only depends on `uvicorn`/`websockets`, both
already declared under the `web` extra.

Fixture note: the disposable-container / real-server / http-post / ws-probe
machinery below is shared with the resource-soak test (T061,
``test_nine_terminals_soak.py``) via ``_nine_terminal_fixture.py`` -- see
that module's docstring. Only this test's own orchestration (provision three
containers -> real ``DiscoveryService.refresh()`` -> build nine probes -> run
them once concurrently -> assert no cross-routing -> assert a clean reap)
lives here.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings
from remo_cli.web.models import TerminalState

from tests.integration._nine_terminal_fixture import (  # noqa: F401 (fixtures used by name)
    INSTANCE_TAGS,
    PROJECTS_BY_INSTANCE,
    _DOCKER_OK,
    _SKIP_REASON,
    _RunningApp,
    _container_running,
    _free_port,
    _install_remo_host_script,
    _open_probe_and_close,
    _remo_host_script,
    _start_container,
    _stop_container,
    sshd_image,
    ssh_test_identity,
    trusted_known_hosts,
)

pytestmark = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nine_terminals_no_cross_routing(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path  # noqa: F811
):
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)

    container_names = [f"remo-test-nine-{tag}-{uuid.uuid4().hex[:8]}" for tag in INSTANCE_TAGS]
    ips: dict[str, str] = {}
    # A short, flat control dir -- NOT `tmp_path`-nested. `ssh`'s
    # ControlPath (`<dir>/remo-%r@%h-%p`) is an AF_UNIX socket path, capped
    # at ~104-108 bytes on Linux; pytest's `tmp_path` (nested under
    # `/tmp/pytest-of-<user>/pytest-<N>/<test-name>.../`) combined with this
    # test's long function name reliably exceeds that limit, making every
    # multiplexed `ssh` attach fail fast with
    # "too long for Unix domain socket" (a real bug this test found the hard
    # way: that failure's control/error frame was previously mishandled as a
    # silent infinite hang rather than a clean assertion -- see
    # `_open_probe_and_close`'s `ConnectionClosed` handling above).
    control_dir = Path(tempfile.mkdtemp(prefix="remo-test-nine-ctl-"))
    try:
        # -- Stand up all three containers + install their (differently
        # -- tagged, but structurally identical) fake remo-host scripts.
        for tag, name in zip(INSTANCE_TAGS, container_names):
            ip = _start_container(sshd_image, name)
            ips[tag] = ip
            trusted_known_hosts(ip)
            _install_remo_host_script(name, _remo_host_script(tag, PROJECTS_BY_INSTANCE[tag]))

        # -- Real registry pointing at all three disposable containers
        # -- (discovery option (a): real DiscoveryService.refresh() over
        # -- real ssh, no injected/stubbed targets).
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        registry_lines = [
            f"incus:{tag}:{ips[tag]}:remo\n" for tag in INSTANCE_TAGS
        ]
        (registry_dir / "known_hosts").write_text("".join(registry_lines))
        monkeypatch.setenv("REMO_HOME", str(registry_dir))

        port = _free_port()
        origin = f"http://127.0.0.1:{port}"
        settings = WebSettings(
            allowed_hosts=["127.0.0.1"],
            allowed_origins=[origin],
            ssh_control_dir=str(control_dir),
            discovery_timeout_s=15.0,
            discovery_concurrency=8,
            terminal_cap_global=32,
            terminal_cap_per_client=16,
        )
        app = app_module.create_app(settings)

        await app.state.discovery_service.refresh()
        targets = app.state.discovery_service.get_targets()
        assert len(targets) == 9, f"expected nine discovered targets, got {targets!r}"

        # Exactly one target per (instance, project); "shared" appears once
        # per instance (three times total) -- the repeated-name case.
        by_pair = {(t.instance_name, t.project): t for t in targets}
        assert len(by_pair) == 9
        shared_targets = [t for t in targets if t.project == "shared"]
        assert len(shared_targets) == 3
        assert {t.instance_name for t in shared_targets} == set(INSTANCE_TAGS)
        # Distinct opaque ids even though the project name literally repeats.
        assert len({t.id for t in shared_targets}) == 3

        # -- Build the nine (target, expected banner, marker) probes.
        probes: list[tuple[str, str, str]] = []
        for tag in INSTANCE_TAGS:
            for project in PROJECTS_BY_INSTANCE[tag]:
                target = by_pair[(tag, project)]
                expected_banner = f"BANNER::{tag}::{project}"
                marker = f"MARK-{tag}-{project}-{uuid.uuid4().hex[:10]}"
                probes.append((target.id, expected_banner, marker))
        assert len(probes) == 9

        running = _RunningApp(app, port)
        await running.start()
        try:
            # -- Open all nine, roughly concurrently (US3 scenario 1 "Open
            # -- all"): nine plain coroutines over real TCP sockets,
            # -- dispatched together via asyncio.gather.
            results = await asyncio.gather(
                *(
                    _open_probe_and_close(port, origin, target_id, banner, marker)
                    for target_id, banner, marker in probes
                )
            )

            # -- Core cross-routing assertion: every terminal's own banner
            # -- and marker are present in ITS stream, and NO OTHER
            # -- terminal's banner/marker ever leaked into it -- including
            # -- the three "shared"-project terminals, which are only
            # -- distinguishable by which socket delivered which banner.
            all_banners = [b for _tid, b, _m in probes]
            all_markers = [m for _tid, _b, m in probes]
            terminal_ids: list[str] = []
            for idx, ((terminal_id, seen), (_target_id, own_banner, own_marker)) in enumerate(
                zip(results, probes)
            ):
                terminal_ids.append(terminal_id)
                assert own_banner.encode() in seen
                assert own_marker.encode() in seen
                for other_banner in all_banners:
                    if other_banner != own_banner:
                        assert other_banner.encode() not in seen, (
                            f"probe {idx} ({own_banner!r}) leaked foreign banner "
                            f"{other_banner!r}"
                        )
                for other_marker in all_markers:
                    if other_marker != own_marker:
                        assert other_marker.encode() not in seen, (
                            f"probe {idx} ({own_banner!r}) leaked foreign marker "
                            f"{other_marker!r}"
                        )
            assert len(set(terminal_ids)) == 9, "expected nine distinct terminal ids"

            # -- Clean reap: all nine local attachments end up disconnected,
            # -- with no live session left in the registry (FR-019/FR-023),
            # -- while every container's own sshd (PID 1) survives untouched.
            registry = app.state.terminal_registry
            deadline = time.monotonic() + 10.0
            pending = set(terminal_ids)
            while pending and time.monotonic() < deadline:
                for terminal_id in list(pending):
                    att = registry.get(terminal_id)
                    if att is not None and att.state == TerminalState.DISCONNECTED:
                        pending.discard(terminal_id)
                if pending:
                    await asyncio.sleep(0.05)
            assert not pending, f"terminals never reaped to disconnected: {pending}"
            for terminal_id in terminal_ids:
                assert registry.get_session(terminal_id) is None
        finally:
            await running.stop()

        for name in container_names:
            assert _container_running(name), (
                f"container {name} must survive local ssh teardown"
            )
    finally:
        for name in container_names:
            _stop_container(name)
        shutil.rmtree(control_dir, ignore_errors=True)
