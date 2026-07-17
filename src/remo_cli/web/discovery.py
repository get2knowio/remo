"""Concurrent per-instance session discovery.

Reads the read-only registry (:func:`~remo_cli.core.config.get_known_hosts_path_readonly`),
runs `remo-host capabilities`/`sessions list` against every registered
instance concurrently (bounded by `WebSettings.discovery_concurrency`, each
call bounded by `WebSettings.discovery_timeout_s`), and maintains an
in-memory TTL cache of the resulting `DiscoverySnapshot` per instance plus a
flattened `SessionTarget` index (see data-model.md and R1/R10).

`remo_host_client`'s functions are synchronous (`subprocess.run`-based); this
module runs them in the default thread-pool executor via
`loop.run_in_executor` rather than rewriting that client as async (FR-005).

Host-failure isolation (FR-006/US1 scenario 2): a single instance's failure
is caught and mapped to a typed, non-``ok`` `DiscoverySnapshot` — it never
prevents other instances' results from being produced.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone

from remo_cli.core.config import get_known_hosts_path_readonly
from remo_cli.core.remo_host_client import (
    IncompatibleProtocolError,
    MalformedResponseError,
    PayloadTooLargeError,
    ProjectEntry,
    RemoHostCommandError,
    RemoteCapability,
    SshTransportError,
    get_capabilities,
    list_sessions,
)
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus, TypedError
from remo_cli.models.host import KnownHost
from remo_cli.models.session_target import SessionTarget, derive_session_target_id
from remo_cli.web.config import WebSettings

__all__ = ["DiscoveryService", "derive_instance_id"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive_instance_id(host: KnownHost) -> str:
    """Derive a stable, opaque public ID for a `(type, name)` instance.

    Mirrors `models.session_target.derive_session_target_id`'s approach (a
    plain SHA-256 digest is sufficient — opacity, not secrecy, is the
    requirement here; real authorization happens via the discovery cache
    lookup, not via guessing resistance of the id itself).
    """
    stable = f"{host.type}\x1f{host.name}".encode()
    return hashlib.sha256(stable).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Read-only registry access (R10)
# ---------------------------------------------------------------------------


def _read_known_hosts_readonly() -> list[KnownHost]:
    """Read the registry via the read-only-safe path (no ``mkdir`` side effect).

    `core.known_hosts.get_known_hosts()` is built on `get_known_hosts_path()`,
    which goes through `get_remo_home()` and therefore *creates* the config
    directory as a side effect — unsafe against a read-only-mounted registry
    (R10). This re-implements the same tolerant line-parsing
    (`KnownHost.from_line`, skipping blank/unparseable lines, never raising)
    directly against `get_known_hosts_path_readonly()` instead.
    """
    path = get_known_hosts_path_readonly()
    try:
        if not path.exists():
            return []
        raw_lines = path.read_text().splitlines()
    except OSError:
        # `Path.exists()`/`read_text()` raise on EACCES (only ENOENT-ish
        # errors are swallowed). An unreadable registry -- e.g. bind-mounted
        # from a host directory this uid cannot traverse -- must not escape
        # as a traceback from every caller, `remo web check` included.
        # Degrade to "no instances"; `health._check_registry` separately
        # reports the mount as unreadable, with remediation.
        return []

    hosts: list[KnownHost] = []
    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            hosts.append(KnownHost.from_line(line))
        except ValueError:
            continue
    return hosts


# ---------------------------------------------------------------------------
# Per-instance discovery
# ---------------------------------------------------------------------------


def _discover_one_sync(
    host: KnownHost, settings: WebSettings
) -> tuple[RemoteCapability, list[ProjectEntry]]:
    """Blocking discovery of one instance; run in a worker thread."""
    # identity_file/known_hosts_file resolve to the service identity only in
    # adopted mode (R6); in mounted/unconfigured/broken mode both properties
    # return None and the argv is byte-identical to before (FR-005/FR-023).
    ssh_argv_prefix = build_ssh_base_cmd(
        host,
        control_dir=settings.ssh_control_dir,
        identity_file=settings.ssh_identity_file,
        known_hosts_file=settings.ssh_known_hosts_file,
    )
    capability = get_capabilities(ssh_argv_prefix, timeout=settings.discovery_timeout_s)
    entries = list_sessions(ssh_argv_prefix, timeout=settings.discovery_timeout_s)
    return capability, entries


def _snapshot(
    instance_id: str,
    host: KnownHost,
    status: InstanceStatus,
    *,
    capability: RemoteCapability | None = None,
    targets: list[SessionTarget] | None = None,
    error: TypedError | None = None,
) -> DiscoverySnapshot:
    return DiscoverySnapshot(
        instance_id=instance_id,
        instance_type=host.type,
        instance_name=host.name,
        status=status,
        capability=capability,
        targets=targets or [],
        error=error,
        refreshed_at=_now_iso(),
        region=host.region or "",
    )


def _looks_like_missing_remo_host(exc: RemoHostCommandError) -> bool:
    """True when a non-SSH command failure looks like "remo-host not found".

    A missing `remo-host` binary typically surfaces as the remote shell's own
    "command not found" exit (127) rather than a documented `remo-host` exit
    code (2/3/4/5) — that case isn't in `_EXIT_CODE_REASONS`, so it lands
    here as `RemoHostExitReason.UNKNOWN`. Detect it from the exit code and,
    defensively, from common shell wording in stderr.
    """
    if exc.returncode == 127:
        return True
    stderr_lower = exc.stderr.lower()
    return "command not found" in stderr_lower or "no such file or directory" in stderr_lower


def _classify_ssh_transport(exc: SshTransportError) -> tuple[InstanceStatus, str, bool, str]:
    """Map an `SshTransportError` to `(status, error_code, retryable, remediation)`."""
    message = str(exc).lower()
    if "timed out" in message or "timeout" in message:
        return (
            InstanceStatus.TIMEOUT,
            "timeout",
            True,
            "Check instance is reachable and not overloaded; retry.",
        )
    auth_markers = (
        "permission denied",
        "authentication failed",
        "host key verification failed",
        "publickey",
    )
    if any(marker in message for marker in auth_markers):
        return (
            InstanceStatus.AUTH_FAILED,
            "auth_failed",
            False,
            "Check SSH credentials/identity for this instance.",
        )
    return (
        InstanceStatus.UNREACHABLE,
        "unreachable",
        True,
        "Check instance is running / reachable.",
    )


async def _discover_one(
    host: KnownHost, settings: WebSettings, semaphore: asyncio.Semaphore
) -> DiscoverySnapshot:
    """Discover one instance, bounded by *semaphore* and `discovery_timeout_s`.

    Never raises: every failure mode (SSH transport, protocol incompatibility,
    malformed/oversized payload, missing `remo-host`, timeout, or any other
    unexpected error) is caught here and mapped to a typed, non-``ok``
    `DiscoverySnapshot` so one host's failure never cancels/propagates into
    the batch (FR-006).
    """
    instance_id = derive_instance_id(host)

    async with semaphore:
        loop = asyncio.get_running_loop()
        try:
            capability, entries = await asyncio.wait_for(
                loop.run_in_executor(None, _discover_one_sync, host, settings),
                timeout=settings.discovery_timeout_s,
            )
        except TimeoutError:
            return _snapshot(
                instance_id,
                host,
                InstanceStatus.TIMEOUT,
                error=TypedError(
                    code="timeout",
                    message=f"Discovery timed out after {settings.discovery_timeout_s:.0f}s",
                    retryable=True,
                    remediation="Check instance is reachable and not overloaded; retry.",
                ),
            )
        except IncompatibleProtocolError as exc:
            return _snapshot(
                instance_id,
                host,
                InstanceStatus.INCOMPATIBLE_PROTOCOL,
                error=TypedError(
                    code="incompatible_protocol",
                    message=str(exc),
                    retryable=False,
                    remediation="Update this instance's Remo host tools (re-run configure).",
                ),
            )
        except (MalformedResponseError, PayloadTooLargeError) as exc:
            return _snapshot(
                instance_id,
                host,
                InstanceStatus.MALFORMED,
                error=TypedError(
                    code="malformed",
                    message=str(exc),
                    retryable=False,
                    remediation=(
                        "remo-host on this instance returned an unexpected response. "
                        "Update this instance's Remo host tools (re-run configure)."
                    ),
                ),
            )
        except RemoHostCommandError as exc:
            if _looks_like_missing_remo_host(exc):
                return _snapshot(
                    instance_id,
                    host,
                    InstanceStatus.NO_REMO_HOST,
                    error=TypedError(
                        code="no_remo_host",
                        message="remo-host not installed",
                        retryable=False,
                        remediation="Update this instance's Remo host tools (re-run configure).",
                    ),
                )
            return _snapshot(
                instance_id,
                host,
                InstanceStatus.MALFORMED,
                error=TypedError(
                    code="malformed",
                    message=str(exc),
                    retryable=False,
                    remediation=(
                        "remo-host on this instance returned an unexpected error. "
                        "Update this instance's Remo host tools (re-run configure)."
                    ),
                ),
            )
        except SshTransportError as exc:
            status, code, retryable, remediation = _classify_ssh_transport(exc)
            return _snapshot(
                instance_id,
                host,
                status,
                error=TypedError(
                    code=code, message=str(exc), retryable=retryable, remediation=remediation
                ),
            )
        except Exception as exc:  # noqa: BLE001 - host-failure isolation (FR-006):
            # any unanticipated failure for this host must not take down the
            # rest of the discovery batch.
            return _snapshot(
                instance_id,
                host,
                InstanceStatus.UNREACHABLE,
                error=TypedError(
                    code="unreachable",
                    message=str(exc),
                    retryable=True,
                    remediation="Check instance is running / reachable.",
                ),
            )

        targets = [
            SessionTarget(
                id=derive_session_target_id(host.type, host.name, entry.name),
                instance_type=host.type,
                instance_name=host.name,
                project=entry.name,
                has_devcontainer=entry.has_devcontainer,
                zellij_state=entry.zellij_state,
                devcontainer_running=entry.devcontainer_running,
                discovered_at=_now_iso(),
                git_tracked=entry.git_tracked,
                git_dirty=entry.git_dirty,
                git_ahead=entry.git_ahead,
                git_behind=entry.git_behind,
            )
            for entry in entries
        ]
        return _snapshot(
            instance_id, host, InstanceStatus.OK, capability=capability, targets=targets
        )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class DiscoveryService:
    """Owns the in-memory discovery cache and drives concurrent refresh cycles.

    All cache reads (`get_snapshot`, `get_targets`, `find_target`,
    `last_refreshed_at`) are synchronous, non-blocking, and never perform I/O
    — they only ever return whatever the most recent `refresh()` produced
    (empty/unset before the first refresh). `refresh()` is the only method
    that talks to instances; it re-reads the registry fresh every time it
    actually runs (hot reload, R10) and updates the cache **per-instance as
    each completes**, not all at once at the end (incremental delivery,
    FR-035).
    """

    def __init__(self, settings: WebSettings | None = None) -> None:
        self._settings = settings or WebSettings()
        self._lock = asyncio.Lock()
        self._snapshots: dict[str, DiscoverySnapshot] = {}
        self._targets_by_id: dict[str, SessionTarget] = {}
        self._last_refreshed_at: str | None = None
        self._last_refresh_monotonic: float | None = None

    # -- cache reads (sync, non-blocking) ---------------------------------

    def get_snapshot(self) -> list[DiscoverySnapshot]:
        """Return the cached `DiscoverySnapshot` list (one per instance)."""
        return list(self._snapshots.values())

    def get_targets(self) -> list[SessionTarget]:
        """Return the flattened `SessionTarget[]` across ``ok`` instances."""
        return list(self._targets_by_id.values())

    def find_target(self, target_id: str) -> SessionTarget | None:
        """Look up a `SessionTarget` by its opaque id in the current cache."""
        return self._targets_by_id.get(target_id)

    def find_host(self, instance_type: str, instance_name: str) -> KnownHost | None:
        """Resolve a `(instance_type, instance_name)` pair to its full `KnownHost`.

        Neither `DiscoverySnapshot` nor `SessionTarget` carries the original
        `KnownHost` (only the `(type, name)` strings), but opening a terminal
        needs the full record (`.host`, `.user`, `.access_mode`, ...) to build
        the SSH command. Rather than duplicate registry-reading logic elsewhere,
        this re-reads the read-only registry with the same tolerant parsing as
        `_read_known_hosts_readonly()` and returns the first `(type, name)`
        match, or ``None`` if the instance is no longer registered.
        """
        for host in _read_known_hosts_readonly():
            if host.type == instance_type and host.name == instance_name:
                return host
        return None

    @property
    def last_refreshed_at(self) -> str | None:
        return self._last_refreshed_at

    def _is_fresh(self) -> bool:
        if self._last_refresh_monotonic is None:
            return False
        elapsed = time.monotonic() - self._last_refresh_monotonic
        return elapsed < self._settings.discovery_cache_ttl_s

    # -- refresh (async, the only method that performs I/O) ---------------

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        """Re-read the registry and re-run discovery.

        *instance_id*, when given, limits the run to that single instance
        (`POST /discovery/refresh` with a body); ``None`` refreshes every
        registered instance. *force* (default ``True``) makes an explicit/
        manual call always run regardless of cache freshness, per the
        contract ("re-runs concurrent discovery ... regardless of TTL").
        Callers that want TTL-gated auto-refresh (e.g. a future interval
        scheduler, T052) can pass ``force=False`` to make this a no-op when
        the cache is still fresh.
        """
        if not force and instance_id is None and self._is_fresh():
            return

        hosts = _read_known_hosts_readonly()
        if instance_id is not None:
            hosts = [h for h in hosts if derive_instance_id(h) == instance_id]
            if not hosts:
                # Unknown/no-longer-registered instance id: nothing to do.
                return

        semaphore = asyncio.Semaphore(max(1, self._settings.discovery_concurrency))

        async def _run_and_store(host: KnownHost) -> None:
            snapshot = await _discover_one(host, self._settings, semaphore)
            async with self._lock:
                self._snapshots[snapshot.instance_id] = snapshot
                self._rebuild_target_index()

        # return_exceptions=True is defense-in-depth: _discover_one already
        # catches everything itself (host-failure isolation, FR-006), but a
        # bug there must still never cancel siblings.
        await asyncio.gather(*(_run_and_store(host) for host in hosts), return_exceptions=True)

        async with self._lock:
            if instance_id is None:
                # Full refresh: drop snapshots for instances no longer in
                # the registry (hot reload — a removed host disappears).
                current_ids = {derive_instance_id(h) for h in hosts}
                for stale_id in [sid for sid in self._snapshots if sid not in current_ids]:
                    del self._snapshots[stale_id]
                self._rebuild_target_index()
            self._last_refreshed_at = _now_iso()
            self._last_refresh_monotonic = time.monotonic()

    def _rebuild_target_index(self) -> None:
        """Recompute the flattened target-by-id index from current snapshots.

        Caller must hold ``self._lock``.
        """
        index: dict[str, SessionTarget] = {}
        for snapshot in self._snapshots.values():
            if snapshot.status is InstanceStatus.OK:
                for target in snapshot.targets:
                    index[target.id] = target
        self._targets_by_id = index
