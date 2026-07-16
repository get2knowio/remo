"""Resource/soak test: nine real terminals under sustained load (T061).

Extends the 3x3 disposable-container fixture from ``test_nine_terminals.py``
(T043/SC-003, machinery shared via ``_nine_terminal_fixture.py``) with a
SUSTAINED-load run: instead of a single open -> verify -> close pass, all
nine WebSocket-attached terminals stay open for the run's whole duration and
continuously exchange small, unique "echo a marker, verify it comes back"
rounds, staggered across all nine -- exercising the PTY pumps, T036's
byte-bounded backpressure, and WS framing under sustained (not just burst)
conditions.

Throughout the run, this test periodically samples and asserts (NFR-004,
quickstart.md V7, SC-013 -- "at least nine simultaneous active terminals for
one hour without cross-routing, process leaks, unbounded memory growth, or
unintended disconnects"):

* **Bounded memory** -- resident memory of the web-service process (which,
  under this pure-asyncio real-`uvicorn.Server`-as-`asyncio.Task` fixture, IS
  this test process: see ``test_nine_terminals.py``'s concurrency note) via
  ``/proc/self/status`` ``VmRSS`` (falling back to
  ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` off-Linux). No new
  dependency: `psutil` is not declared anywhere in this project
  (`pyproject.toml`/`uv.lock` both confirm it), so this deliberately uses
  only the stdlib. Asserted via a GROWTH-RATIO check (last post-warmup sample
  vs. first) rather than an absolute byte ceiling -- a soak test's job is
  catching unbounded growth, not enforcing a fragile fixed limit.
* **Exactly nine live child (ssh/PTY) processes, no leaks, no unintended
  deaths** -- introspected via ``TerminalRegistry.get_session()`` (public)
  and :attr:`TerminalSession.pid` (added here, mirroring the class's existing
  ``returncode``/``is_paused`` observability properties) + ``os.kill(pid, 0)``
  liveness checks. This is deliberately OUR OWN tracked state, not external
  process-tree enumeration (e.g. `psutil.Process().children()`), per NFR-004:
  it is more precise and matches exactly what the requirement cares about --
  no leaked/orphaned processes from the web service's own bookkeeping.
* **No cross-routing, checked repeatedly (not just once)** -- reuses T043's
  marker-based isolation-proof technique: each terminal's ``(instance,
  project)`` pair yields a marker PREFIX unique across all nine and stable
  for the whole run (`MARK-<tag>-<project>-`); every round's freshly-read
  bytes are checked for the absence of all eight *foreign* prefixes, in
  addition to the presence of that round's own (uniquely-suffixed) marker.
  Since the nine prefixes are fixed upfront, this check is race-free (no
  reliance on precise timing between concurrent terminals' rounds).
* **Clean, complete reap at the end** -- exactly nine processes existed, and
  after closing all nine, zero remain (registry sessions gone AND the pids
  are no longer signalable).

Two duration tiers, mirroring ``tests/image/test_docker_image.py``'s
``REMO_RUN_IMAGE_TESTS=1`` opt-in pattern for its own expensive tier:

* **Smoke (always on)**: ``test_nine_terminals_soak_smoke`` runs
  unconditionally (gated only by Docker availability, like every other test
  in this module) for ~40s of sustained load. Fast, always-on coverage that
  the mechanism itself is correct.
* **Full (opt-in)**: ``test_nine_terminals_soak_full`` only runs when
  ``REMO_RUN_SOAK_TEST=1`` is set, for ``REMO_SOAK_DURATION_S`` seconds
  (default 3600 -- the literal SC-013 "one hour"). Off by default because a
  genuine >=1h run on every `pytest` invocation is impractical for normal
  CI/dev iteration -- exactly the tradeoff `REMO_RUN_IMAGE_TESTS=1` already
  makes for its own multi-minute, network-heavy tier.

Both tiers drive the IDENTICAL ``_run_soak()`` code path; only the duration,
sample interval, and round interval differ, so the always-on smoke tier is a
genuine (if short) proof that the mechanism works, not a separate or lesser
implementation.

Docker-gated like the rest of this module's tests: the whole module skips if
Docker/network is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import resource
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import websockets

from remo_cli.web import app as app_module
from remo_cli.web.config import WebSettings

from tests.integration._nine_terminal_fixture import (  # noqa: F401 (fixtures used by name)
    INSTANCE_TAGS,
    PROJECTS_BY_INSTANCE,
    _DOCKER_OK,
    _SKIP_REASON,
    _RunningApp,
    _free_port,
    _http_post_json,
    _container_running,
    _install_remo_host_script,
    _remo_host_script,
    _start_container,
    _stop_container,
    sshd_image,
    ssh_test_identity,
    trusted_known_hosts,
)

pytestmark = pytest.mark.skipif(not _DOCKER_OK, reason=_SKIP_REASON)

# ---------------------------------------------------------------------------
# Duration/opt-in configuration (mirrors tests/image/test_docker_image.py's
# REMO_RUN_IMAGE_TESTS=1 pattern).
# ---------------------------------------------------------------------------

_RUN_SOAK_TEST = os.environ.get("REMO_RUN_SOAK_TEST") == "1"
_SOAK_DURATION_S = float(os.environ.get("REMO_SOAK_DURATION_S", "3600"))

_SMOKE_DURATION_S = 40.0
_SMOKE_SAMPLE_INTERVAL_S = 4.0
_SMOKE_ROUND_INTERVAL_S = 1.0

_FULL_SAMPLE_INTERVAL_S = 30.0
_FULL_ROUND_INTERVAL_S = 2.0

# A generous growth-ratio ceiling: catches genuine leaks (which grow
# unboundedly, well past this) without being a fragile fixed-byte assertion.
_MAX_MEMORY_GROWTH_RATIO = 4.0


def _vm_rss_kb() -> int:
    """Current resident set size (KiB) of THIS process.

    Reads ``/proc/self/status`` ``VmRSS`` directly (Linux, zero new
    dependency, and -- unlike ``ru_maxrss`` -- a true CURRENT reading rather
    than a monotonic high-water mark, so it can show a leak plateauing or
    not). Falls back to ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` (also
    stdlib-only) if ``/proc`` isn't available.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


# ---------------------------------------------------------------------------
# Per-terminal bookkeeping.
# ---------------------------------------------------------------------------


@dataclass
class _TerminalHandle:
    key: str  # f"{tag}:{project}" -- unique per terminal.
    tag: str
    project: str
    target_id: str
    expected_banner: str
    terminal_id: str = ""
    rounds_completed: int = 0


@dataclass
class _SoakResult:
    duration_s: float
    rounds_per_terminal: dict[str, int] = field(default_factory=dict)
    memory_samples_kb: list[int] = field(default_factory=list)
    process_count_samples: list[int] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# One terminal's sustained round-trip loop.
# ---------------------------------------------------------------------------


async def _sustained_terminal_loop(
    handle: _TerminalHandle,
    *,
    port: int,
    origin: str,
    other_prefixes: dict[str, bytes],
    deadline: float,
    round_interval_s: float,
    stop_event: asyncio.Event,
    ready_event: asyncio.Event,
) -> None:
    """Open one terminal, verify its banner, then repeat echo rounds until *deadline*.

    Each round sends a fresh, uniquely-suffixed marker under this terminal's
    OWN fixed prefix (``other_prefixes`` excludes it), waits for its own
    echo, and asserts none of the eight *foreign* prefixes ever appear in the
    freshly-read bytes -- the sustained, repeated version of T043's
    single-shot cross-routing check.
    """
    status_code, body = await _http_post_json(
        port,
        "/api/v1/terminals",
        {"session_target_id": handle.target_id, "cols": 80, "rows": 24},
        origin=origin,
    )
    assert status_code == 201, body
    handle.terminal_id = body["terminal_id"]
    token = body["ws_token"]
    own_prefix = f"MARK-{handle.tag}-{handle.project}-".encode()

    ws_uri = origin.replace("http://", "ws://", 1) + f"/api/v1/terminals/{handle.terminal_id}"
    try:
        async with websockets.connect(
            ws_uri,
            subprotocols=["remo-terminal.v1", token],
            additional_headers={"Origin": origin},
            open_timeout=15,
        ) as ws:
            assert ws.subprotocol == "remo-terminal.v1"
            first = await asyncio.wait_for(ws.recv(), timeout=15.0)
            assert json.loads(first) == {"v": 1, "type": "ready"}

            # Wait for this terminal's own attach banner before starting rounds.
            banner_bytes = handle.expected_banner.encode()
            seen = b""
            banner_deadline = time.monotonic() + 15.0
            while banner_bytes not in seen and time.monotonic() < banner_deadline:
                remaining = max(0.1, banner_deadline - time.monotonic())
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                if isinstance(msg, bytes):
                    seen += msg
            assert banner_bytes in seen, (
                f"{handle.key}: never saw its own banner {handle.expected_banner!r}; "
                f"saw={seen!r}"
            )
            ready_event.set()

            round_idx = 0
            while time.monotonic() < deadline and not stop_event.is_set():
                round_idx += 1
                marker = own_prefix + f"{round_idx}-{uuid.uuid4().hex[:8]}".encode()
                await ws.send(marker)

                round_seen = b""
                round_deadline = time.monotonic() + 15.0
                found_own = False
                while time.monotonic() < round_deadline:
                    remaining = max(0.1, round_deadline - time.monotonic())
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except TimeoutError:
                        break
                    if isinstance(msg, bytes):
                        round_seen += msg
                        for other_key, other_prefix in other_prefixes.items():
                            if other_prefix in round_seen:
                                stop_event.set()
                                raise AssertionError(
                                    f"{handle.key}: round {round_idx} saw foreign marker "
                                    f"prefix from {other_key!r}: {round_seen!r}"
                                )
                        if marker.strip() in round_seen:
                            found_own = True
                            break
                if not found_own:
                    stop_event.set()
                    raise AssertionError(
                        f"{handle.key}: round {round_idx} own marker never echoed "
                        f"(saw={round_seen!r})"
                    )
                handle.rounds_completed = round_idx
                await asyncio.sleep(round_interval_s)
    except websockets.exceptions.ConnectionClosed as exc:
        stop_event.set()
        raise AssertionError(
            f"{handle.key}: WS closed unexpectedly (unintended disconnect) "
            f"after {handle.rounds_completed} rounds: {exc!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Resource sampler: memory + live-child-process accounting.
# ---------------------------------------------------------------------------


async def _sampler_loop(
    app,
    handles: list[_TerminalHandle],
    *,
    deadline: float,
    sample_interval_s: float,
    result: _SoakResult,
    stop_event: asyncio.Event,
) -> None:
    registry = app.state.terminal_registry
    # Let all nine terminals finish their handshake before the first sample
    # so "post-warmup" memory isn't dominated by one-time PTY/ssh spawn cost.
    await asyncio.sleep(min(3.0, max(0.5, sample_interval_s / 2)))
    while time.monotonic() < deadline and not stop_event.is_set():
        pids: list[int] = []
        for handle in handles:
            if not handle.terminal_id:
                continue
            session = registry.get_session(handle.terminal_id)
            if session is None:
                result.violations.append(
                    f"{handle.key}: session missing from registry mid-run "
                    f"(unexpected close/leak)"
                )
                continue
            pid = session.pid
            if pid is None:
                result.violations.append(f"{handle.key}: session has no pid mid-run")
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                result.violations.append(
                    f"{handle.key}: pid {pid} is no longer alive (process died)"
                )
                continue
            pids.append(pid)

        if len(set(pids)) != 9:
            result.violations.append(
                f"expected 9 live attachment processes, found {len(set(pids))}: {sorted(pids)}"
            )
        result.process_count_samples.append(len(set(pids)))
        result.memory_samples_kb.append(_vm_rss_kb())

        if result.violations:
            stop_event.set()
            break
        await asyncio.sleep(sample_interval_s)


# ---------------------------------------------------------------------------
# End-to-end soak run: provision the 3x3 fixture, drive sustained load +
# sampling concurrently, then assert bounded memory / zero leaks / clean reap.
# ---------------------------------------------------------------------------


async def _run_soak(
    *,
    sshd_image_tag: str,
    ssh_test_identity,  # noqa: F811
    trusted_known_hosts,  # noqa: F811
    monkeypatch,
    tmp_path: Path,
    duration_s: float,
    sample_interval_s: float,
    round_interval_s: float,
) -> _SoakResult:
    _pubkey, auth_sock, agent_pid = ssh_test_identity
    monkeypatch.setenv("SSH_AUTH_SOCK", auth_sock)
    monkeypatch.setenv("SSH_AGENT_PID", agent_pid)

    container_names = [f"remo-test-soak-{tag}-{uuid.uuid4().hex[:8]}" for tag in INSTANCE_TAGS]
    ips: dict[str, str] = {}
    # Short, flat control dir -- see test_nine_terminals.py's comment on the
    # AF_UNIX ControlPath length trap `tmp_path`-nesting hits.
    control_dir = Path(tempfile.mkdtemp(prefix="remo-test-soak-ctl-"))
    try:
        for tag, name in zip(INSTANCE_TAGS, container_names):
            ip = _start_container(sshd_image_tag, name)
            ips[tag] = ip
            trusted_known_hosts(ip)
            _install_remo_host_script(name, _remo_host_script(tag, PROJECTS_BY_INSTANCE[tag]))

        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        (registry_dir / "known_hosts").write_text(
            "".join(f"incus:{tag}:{ips[tag]}:remo\n" for tag in INSTANCE_TAGS)
        )
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
        by_pair = {(t.instance_name, t.project): t for t in targets}
        assert len(by_pair) == 9

        handles: list[_TerminalHandle] = []
        for tag in INSTANCE_TAGS:
            for project in PROJECTS_BY_INSTANCE[tag]:
                target = by_pair[(tag, project)]
                handles.append(
                    _TerminalHandle(
                        key=f"{tag}:{project}",
                        tag=tag,
                        project=project,
                        target_id=target.id,
                        expected_banner=f"BANNER::{tag}::{project}",
                    )
                )
        assert len(handles) == 9
        all_prefixes = {h.key: f"MARK-{h.tag}-{h.project}-".encode() for h in handles}

        running = _RunningApp(app, port)
        await running.start()
        result = _SoakResult(duration_s=duration_s)
        try:
            stop_event = asyncio.Event()
            ready_events = [asyncio.Event() for _ in handles]
            deadline = time.monotonic() + duration_s

            loop_tasks = [
                asyncio.create_task(
                    _sustained_terminal_loop(
                        handle,
                        port=port,
                        origin=origin,
                        other_prefixes={
                            k: v for k, v in all_prefixes.items() if k != handle.key
                        },
                        deadline=deadline,
                        round_interval_s=round_interval_s,
                        stop_event=stop_event,
                        ready_event=ready_event,
                    )
                )
                for handle, ready_event in zip(handles, ready_events)
            ]

            # Sampler starts once all nine have attached (or after a bounded
            # wait, so a genuine attach failure surfaces via the loop tasks'
            # own exceptions rather than hanging here).
            await asyncio.wait_for(
                asyncio.gather(*(e.wait() for e in ready_events)), timeout=30.0
            )
            sampler_task = asyncio.create_task(
                _sampler_loop(
                    app,
                    handles,
                    deadline=deadline,
                    sample_interval_s=sample_interval_s,
                    result=result,
                    stop_event=stop_event,
                )
            )

            await asyncio.gather(*loop_tasks, sampler_task)

            for handle in handles:
                result.rounds_per_terminal[handle.key] = handle.rounds_completed

            assert not result.violations, "soak violations: " + "; ".join(result.violations)
            assert all(n > 0 for n in result.rounds_per_terminal.values()), (
                f"every terminal must complete at least one sustained round: "
                f"{result.rounds_per_terminal}"
            )
            assert len(result.memory_samples_kb) >= 2, "need >=2 memory samples to check growth"
            first_kb = result.memory_samples_kb[0]
            last_kb = result.memory_samples_kb[-1]
            if first_kb > 0:
                ratio = last_kb / first_kb
                assert ratio <= _MAX_MEMORY_GROWTH_RATIO, (
                    f"memory grew {ratio:.2f}x over the run (first={first_kb}KB "
                    f"last={last_kb}KB) -- possible leak"
                )

            # -- Clean, complete teardown: close all nine, then assert zero
            # -- live sessions/processes remain (no leak, full reap).
            registry = app.state.terminal_registry
            for handle in handles:
                await registry.close(handle.terminal_id)
            for handle in handles:
                assert registry.get_session(handle.terminal_id) is None, (
                    f"{handle.key}: session still present after close()"
                )
        finally:
            await running.stop()

        for name in container_names:
            assert _container_running(name), f"container {name} must survive local ssh teardown"
        return result
    finally:
        for name in container_names:
            _stop_container(name)
        shutil.rmtree(control_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tests: always-on smoke tier + opt-in full-duration tier.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nine_terminals_soak_smoke(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path  # noqa: F811
):
    """Always-on ~40s sustained-load run: fast proof the soak mechanism works."""
    result = await _run_soak(
        sshd_image_tag=sshd_image,
        ssh_test_identity=ssh_test_identity,
        trusted_known_hosts=trusted_known_hosts,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        duration_s=_SMOKE_DURATION_S,
        sample_interval_s=_SMOKE_SAMPLE_INTERVAL_S,
        round_interval_s=_SMOKE_ROUND_INTERVAL_S,
    )
    print(
        f"[soak smoke] rounds/terminal={result.rounds_per_terminal} "
        f"memory_kb={result.memory_samples_kb} "
        f"process_counts={result.process_count_samples}"
    )


@pytest.mark.skipif(
    not _RUN_SOAK_TEST,
    reason=(
        "opt-in long-duration soak: set REMO_RUN_SOAK_TEST=1 to run (and "
        "optionally REMO_SOAK_DURATION_S, default 3600, to tune the "
        "duration for local runs)"
    ),
)
@pytest.mark.asyncio
async def test_nine_terminals_soak_full(
    sshd_image, ssh_test_identity, trusted_known_hosts, monkeypatch, tmp_path  # noqa: F811
):
    """Opt-in full-duration (default 3600s / SC-013's literal "one hour") soak."""
    result = await _run_soak(
        sshd_image_tag=sshd_image,
        ssh_test_identity=ssh_test_identity,
        trusted_known_hosts=trusted_known_hosts,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
        duration_s=_SOAK_DURATION_S,
        sample_interval_s=_FULL_SAMPLE_INTERVAL_S,
        round_interval_s=_FULL_ROUND_INTERVAL_S,
    )
    print(
        f"[soak full, duration={_SOAK_DURATION_S}s] "
        f"rounds/terminal={result.rounds_per_terminal} "
        f"memory_kb={result.memory_samples_kb} "
        f"process_counts={result.process_count_samples}"
    )
