"""Hetzner Cloud provider business logic for remo.

Manages the lifecycle of Hetzner Cloud VMs: create, destroy, and update
(re-configure dev tools).  All functions are pure business logic with no
Click imports; CLI argument handling lives in the ``cli`` layer.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from remo_cli.core.ansible_runner import build_configure_extra_vars, run_playbook
from remo_cli.core.errors import (
    OperationFailedError,
    PreconditionError,
    ProviderError,
    UserAbortedError,
)
from remo_cli.core.known_hosts import (
    get_known_hosts,
    guard_not_added_ssh_host,
    save_known_host,
)
from remo_cli.core.output import (
    Column,
    confirm,
    print_error,
    print_info,
    print_success,
    print_warning,
    render_host_table,
)
from remo_cli.core.reconcile import (
    DiscoveredHost,
    ProbeError,
    ProbeResult,
    SyncScope,
    run_sync,
)
from remo_cli.core.snapshot import validate_name as validate_snapshot_name
from remo_cli.core.validation import parse_volume_size, validate_name
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _query_hetzner_server_ip(server_name: str) -> str:
    """Query the Hetzner API for the IPv4 address of *server_name*.

    Uses ``HETZNER_API_TOKEN`` from the environment.  Returns an empty string
    when the token is missing, the API call fails, or no matching server is
    found.
    """
    token = os.environ.get("HETZNER_API_TOKEN", "")
    if not token:
        return ""

    url = f"https://api.hetzner.cloud/v1/servers?name={server_name}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        servers = data.get("servers", [])
        if servers:
            return (
                servers[0]
                .get("public_net", {})
                .get("ipv4", {})
                .get("ip", "")
            )
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
        pass

    return ""


def _lookup_hetzner_host(server_name: str) -> str:
    """Return the registered host (IP) for *server_name*, or empty string."""
    for entry in get_known_hosts(type_filter="hetzner"):
        if entry.name == server_name:
            return entry.host
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create(
    name: str = "",
    server_type: str = "",
    location: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> None:
    """Create a new Hetzner Cloud VM and configure it with dev tools.

    Raises :class:`OperationFailedError` if the playbook fails.
    """
    if name:
        validate_name(name, "server name")
    volume_size = parse_volume_size(volume_size)

    print_info("Creating Hetzner VM...")

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"hetzner_server_name={name}"])
    if server_type:
        extra_vars.extend(["-e", f"hetzner_server_type={server_type}"])
    if location:
        extra_vars.extend(["-e", f"hetzner_location={location}"])
    if volume_size:
        extra_vars.extend(["-e", f"hetzner_volume_size={volume_size}"])

    extra_vars.extend(build_configure_extra_vars(tools_only, tools_skip))

    rc = run_playbook("hetzner_site.yml", extra_vars, verbose=verbose)

    if rc != 0:
        raise OperationFailedError(f"Failed to create Hetzner VM (playbook rc={rc}).")

    # Save to known_hosts on success.
    server_name = name or "remo"
    server_ip = _query_hetzner_server_ip(server_name)

    if server_ip:
        save_known_host(
            KnownHost(
                type="hetzner",
                name=server_name,
                host=server_ip,
                user="remo",
            )
        )

    # Print post-create summary.
    print("")
    print_success("==================================================")
    print_success("  Hetzner server created successfully!")
    print_success("==================================================")
    print("")
    print(f"  Name:      {server_name}")
    print(f"  Type:      {server_type or 'cx22'}")
    print(f"  Location:  {location or 'hel1'}")
    print(f"  IP:        {server_ip or 'N/A'}")
    print(f"  Storage:   {volume_size or '10'} GB persistent volume")
    print("")
    print("  Connect:  remo shell")
    print_success("==================================================")
    print("")


def teardown(entry: KnownHost, *, verbose: bool = False, remove_volume: bool = False) -> None:
    """Destroy *entry*'s Hetzner Cloud VM (Protocol Part A: destruction only).

    Guard, snapshot pre-cleanup, confirmation, and registry removal are the
    shared template's job (``core/lifecycle.run_destroy``, R-A3) -- this
    performs only the provider-specific teardown step.

    Hetzner is FLAT (name_format) -- ``entry.name`` IS the server name
    directly, no host/container parsing needed (R-A2).

    Raises :class:`OperationFailedError` if the playbook fails.
    """
    server_name = entry.name

    extra_vars: list[str] = [
        "-e", f"hetzner_server_name={server_name}",
        "-e", f"remove_volume={'true' if remove_volume else 'false'}",
    ]

    rc = run_playbook("hetzner_teardown.yml", extra_vars, verbose=verbose)

    if rc != 0:
        raise OperationFailedError(f"Failed to destroy Hetzner VM '{server_name}' (playbook rc={rc}).")


def update(
    name: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> None:
    """Re-configure dev tools on an existing Hetzner VM.

    When *volume_size* is provided, grow the persistent volume and the
    filesystem first (idempotent — no-op when sizes match).

    Raises :class:`PreconditionError` if the server is not registered, or
    :class:`OperationFailedError` if a playbook fails.
    """
    if name:
        validate_name(name, "server name")
    volume_size = parse_volume_size(volume_size)

    server_name = name or "remo"
    guard_not_added_ssh_host(server_name, "hetzner")  # FR-012

    # `update` doubles as the backfill path for the remo managed label
    # (T058) -- API-only, so it runs before/independent of the SSH-reachable
    # steps below and is not skipped by a later playbook failure. Warn on
    # failure but do not fail the whole update (FR-005 parity).
    ok, err = _apply_managed_label(server_name)
    if not ok:
        print_warning(
            f"Could not mark server '{server_name}' as remo-managed ({err}); "
            f"it may not be picked up by a default `remo hetzner sync`."
        )

    # Get server address from known_hosts.
    server_host = _lookup_hetzner_host(server_name)
    if not server_host:
        raise PreconditionError(
            f"Server '{server_name}' not found in known_hosts. "
            f"Run 'remo hetzner sync' or 'remo hetzner create' first."
        )

    if volume_size:
        print_info(f"Resizing Hetzner volume for '{server_name}' to {volume_size}GB...")
        resize_vars: list[str] = [
            "-e", f"hetzner_server_name={server_name}",
            "-e", f"volume_size={volume_size}",
        ]
        rc = run_playbook("hetzner_resize.yml", resize_vars, verbose=verbose)
        if rc != 0:
            raise OperationFailedError(
                f"Failed to resize Hetzner volume for '{server_name}' (playbook rc={rc})."
            )

    print_info(f"Updating Hetzner VM '{server_name}' at {server_host}...")

    extra_vars: list[str] = [
        "-i", f"{server_host},",
        "-e", "ansible_user=remo",
    ]

    extra_vars.extend(build_configure_extra_vars(tools_only, tools_skip))

    rc = run_playbook(
        "hetzner_configure.yml",
        extra_vars,
        verbose=verbose,
    )
    if rc != 0:
        raise OperationFailedError(f"Failed to update tools on '{server_name}' (playbook rc={rc}).")


def update_entry(entry: KnownHost, *, verbose: bool = False) -> None:
    """Re-apply tool configuration to an existing VM (Protocol Part A)."""
    update(name=entry.name, verbose=verbose)


_LIST_COLUMNS = (
    Column("NAME", lambda e: e.name, width=25),
    Column("HOST", lambda e: e.host, width=25),
    Column("SSH COMMAND", lambda e: f"ssh {e.user}@{e.host}"),
)


def list_hosts() -> None:
    """Print a formatted table of all registered Hetzner VMs."""
    entries = get_known_hosts(type_filter="hetzner")
    render_host_table(
        entries,
        _LIST_COLUMNS,
        empty_message="No Hetzner VMs registered.\nCreate one with: remo hetzner create",
    )


def info(name: str = "") -> None:
    """Print detailed information about a Hetzner Cloud server.

    Queries the Hetzner API for the server (type, status, IP) and its
    paired ``<name>-home`` volume (size). Requires ``HETZNER_API_TOKEN``.

    Raises :class:`PreconditionError` if the token is missing or the server
    is not found, or :class:`OperationFailedError` if the API request fails.
    """
    token = os.environ.get("HETZNER_API_TOKEN", "")
    if not token:
        raise PreconditionError("HETZNER_API_TOKEN is not set.")

    server_name = name or "remo"

    server_url = f"https://api.hetzner.cloud/v1/servers?name={server_name}"
    server_req = urllib.request.Request(
        server_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(server_req, timeout=15) as resp:
            server_data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise OperationFailedError(f"Hetzner API request failed: {e}") from e

    servers = server_data.get("servers", [])
    if not servers:
        raise PreconditionError(f"No Hetzner server found with name '{server_name}'.")

    server = servers[0]
    server_type = server.get("server_type") or {}
    public_net = server.get("public_net") or {}
    ipv4 = (public_net.get("ipv4") or {}).get("ip", "")
    location = (server.get("datacenter") or {}).get("location", {}).get("name", "")

    volume_name = f"{server_name}-home"
    volume_url = f"https://api.hetzner.cloud/v1/volumes?name={volume_name}"
    volume_req = urllib.request.Request(
        volume_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    volume_size = ""
    try:
        with urllib.request.urlopen(volume_req, timeout=15) as resp:
            volume_data = json.loads(resp.read().decode())
        volumes = volume_data.get("volumes", [])
        if volumes:
            volume_size = f"{volumes[0].get('size', '?')} GB"
    except urllib.error.URLError:
        # Volume lookup is best-effort; don't fail the whole info call.
        pass

    print("")
    print(f"  Name:          {server.get('name', server_name)}")
    print(f"  Server ID:     {server.get('id', '?')}")
    print(f"  State:         {server.get('status', 'unknown')}")
    print(f"  Type:          {server_type.get('name', '?')}")
    print(f"  Location:      {location or '?'}")
    print(f"  Public IPv4:   {ipv4 or '(unavailable)'}")
    print(f"  Cores:         {server_type.get('cores', '?')}")
    print(f"  Memory:        {server_type.get('memory', '?')} GB")
    print(f"  Server disk:   {server_type.get('disk', '?')} GB (ephemeral; tied to instance)")
    print(f"  Volume:        {volume_size or '(none attached)'} ({volume_name})")
    print("")


def _hetzner_api_paged(path: str, key: str) -> tuple[list[dict], bool]:
    """Walk every page of a Hetzner list endpoint, accumulating ``response[key]``.

    Hetzner's list endpoints default to ``per_page=25`` (max 50) and report
    ``meta.pagination.next_page`` (``None`` once exhausted); nothing in this
    module used to read that field, so ``sync`` silently truncated at 25.

    Returns ``(items, complete)`` -- ``complete`` is True only if the walk
    reached a page with ``next_page is None``. A failure on the *first*
    page means we could not ask at all, so it propagates (the caller turns
    that into :class:`ProbeError`); a failure on a *later* page means the
    enumeration is partial, so it is swallowed here and reported as
    ``complete=False`` alongside whatever was already gathered.
    """
    items: list[dict] = []
    page = 1
    separator = "&" if "?" in path else "?"
    while True:
        query = f"{path}{separator}page={page}&per_page=50"
        try:
            response = _hetzner_api("GET", query)
        except ProviderError:
            if page == 1:
                raise
            return items, False
        items.extend(response.get(key, []))
        next_page = response.get("meta", {}).get("pagination", {}).get("next_page")
        if next_page is None:
            return items, True
        page = next_page


def _probe(scope: SyncScope, include_all: bool) -> ProbeResult:
    """Enumerate every Hetzner server in the project (FR-044: never filtered
    server-side by the ``remo`` label -- an unlabelled-but-live server must
    still be seen, or it would look absent and get proposed for deletion).
    """
    del include_all  # eligibility widening happens in build_plan, not here
    try:
        servers, complete = _hetzner_api_paged("/servers", "servers")
    except ProviderError as exc:
        raise ProbeError(str(exc)) from exc

    hosts: list[DiscoveredHost] = []
    for server in servers:
        name = server.get("name", "")
        if not name:
            continue
        # Defensive `or {}` at each hop: an IPv6-only server reports
        # public_net.ipv4 as null (key present, value None), so a plain
        # chained .get() would raise AttributeError and crash the sync.
        public_net = server.get("public_net") or {}
        ipv4 = public_net.get("ipv4") or {}
        ip = ipv4.get("ip", "") or ""
        labels = server.get("labels", {}) or {}
        # R6's chosen convention is the single-key label {"remo": "true"};
        # matching on the exact value (rather than mere key presence) keeps
        # this in lockstep with what create/update write.
        marked = labels.get("remo") == "true"
        entry = KnownHost(type="hetzner", name=name, host=ip, user="remo")
        # FR-019: only non-running states are ever annotated in render_plan's
        # output, so a normally-running server must report state="" here --
        # otherwise every healthy server would print as "(running)".
        status = server.get("status", "")
        state = "" if status == "running" else status
        hosts.append(DiscoveredHost(entry=entry, marked=marked, state=state))

    return ProbeResult(
        hosts=hosts,
        complete=complete,
        incomplete_reason="" if complete else "pagination did not complete",
        adoption_criteria="every server in this Hetzner project",
    )


def sync(
    include_all: bool = False, auto_confirm: bool = False, dry_run: bool = False
) -> int:
    """Discover Hetzner Cloud servers and reconcile the registry.

    Enumerates every server in the project via the paginated API (never
    filtered by the ``remo`` label server-side), classifies each by the
    presence of that label, and reconciles the result against the registry
    through the shared reconcile engine. Returns the process exit code.
    """
    scope = SyncScope(type="hetzner")
    return run_sync(
        scope,
        lambda: _probe(scope, include_all=include_all),
        auto_confirm=auto_confirm,
        dry_run=dry_run,
        include_all=include_all,
    )


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


_HETZNER_API = "https://api.hetzner.cloud/v1"


def _hetzner_api(
    method: str, path: str, body: dict | None = None, timeout: int = 30
) -> dict:
    """Call the Hetzner Cloud REST API and return the decoded JSON body.

    Raises :class:`PreconditionError` if ``HETZNER_API_TOKEN`` is not set, or
    :class:`OperationFailedError` on non-2xx responses or transport errors,
    so callers can surface them.
    """
    token = os.environ.get("HETZNER_API_TOKEN", "")
    if not token:
        raise PreconditionError(
            "HETZNER_API_TOKEN is not set; cannot reach the Hetzner Cloud API."
        )

    url = f"{_HETZNER_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json" if data else "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            err_msg = err_body.get("error", {}).get("message", str(e))
        except (ValueError, OSError):
            err_msg = str(e)
        raise OperationFailedError(
            f"Hetzner API {method} {path} failed: {e.code} {err_msg}"
        ) from None
    except urllib.error.URLError as e:
        raise OperationFailedError(f"Hetzner API {method} {path} failed: {e}") from None


def _get_server_by_name(server_name: str) -> dict:
    """Return the Hetzner server record for *server_name*.

    Raises :class:`PreconditionError` if no matching server exists.
    """
    qs = urllib.parse.urlencode({"name": server_name})
    payload = _hetzner_api("GET", f"/servers?{qs}")
    servers = payload.get("servers", [])
    if not servers:
        raise PreconditionError(f"No Hetzner server found named '{server_name}'.")
    return servers[0]


# ---------------------------------------------------------------------------
# Managed label backfill (016-sync-reconcile, R6/T057)
# ---------------------------------------------------------------------------


def _apply_managed_label(server_name: str) -> tuple[bool, str]:
    """Backfill the ``remo: "true"`` label onto an existing server (host-side).

    `create` now applies the label via Ansible at creation time, but a server
    created before this change (or otherwise unlabelled) needs a retroactive
    path. Both ``hetzner.hcloud.server`` and a raw ``PUT /servers/{id}`` treat
    the supplied label map as authoritative and replace it wholesale, so this
    reads the current map first and merges rather than overwriting it --
    a naive backfill would destroy the user's own labels (FR-034).

    Already-labelled is a no-op with no API write (FR-033). Returns
    ``(ok, err)`` -- never raises, never exits -- matching
    ``_apply_managed_marker`` in the other providers; callers warn but do not
    fail the whole command on this alone.
    """
    try:
        server = _get_server_by_name(server_name)
    except ProviderError as e:
        return False, str(e)

    labels = server.get("labels", {}) or {}
    if labels.get("remo") == "true":
        return True, ""

    merged = {**labels, "remo": "true"}
    try:
        _hetzner_api("PUT", f"/servers/{server.get('id', 0)}", {"labels": merged})
    except ProviderError as e:
        return False, str(e)
    return True, ""


def _parse_hetzner_timestamp(s: str) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    cleaned = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _hetzner_state_to_status(state: str) -> SnapshotStatus:
    if state in {"creating"}:
        return SnapshotStatus.PENDING
    if state == "available":
        return SnapshotStatus.AVAILABLE
    return SnapshotStatus.FAILED


def _list_snapshots_for_server(
    server_id: int, server_name: str
) -> list[Snapshot]:
    """Return remo-managed snapshot images created from *server_id*.

    Scoping by ``remo-source-server-id`` satisfies FR-027; the additional
    ``remo=true`` label satisfies FR-026.
    """
    selector = f"remo=true,remo-source-server-id={server_id}"
    qs = urllib.parse.urlencode(
        {"type": "snapshot", "label_selector": selector}
    )
    payload = _hetzner_api("GET", f"/images?{qs}")
    snapshots: list[Snapshot] = []
    for img in payload.get("images", []):
        labels = img.get("labels", {}) or {}
        user_name = labels.get("remo-snapshot-name") or img.get("description", "")
        size_gb = img.get("image_size") or img.get("disk_size") or 0
        size_bytes = int(size_gb * (1024**3)) if size_gb else None
        snapshots.append(
            Snapshot(
                provider="hetzner",
                instance_name=server_name,
                name=user_name,
                backend_id=str(img.get("id", "")),
                created_at=_parse_hetzner_timestamp(img.get("created", "")),
                size_bytes=size_bytes,
                description=img.get("description", "") or "",
                status=_hetzner_state_to_status(img.get("status", "")),
            )
        )
    return snapshots


def snapshot_create_legacy(
    server_name: str, snap_name: str, description: str = ""
) -> int:
    """Create a Hetzner Cloud snapshot of *server_name*.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_create` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.

    Returns 0 once the provider accepts the request (no polling — per FR-004).
    """
    guard_not_added_ssh_host(server_name, "hetzner")  # FR-012
    validate_snapshot_name(snap_name)

    try:
        server = _get_server_by_name(server_name)
    except ProviderError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    if any(s.name == snap_name for s in existing):
        print_error(
            f"Snapshot '{snap_name}' already exists for hetzner instance "
            f"'{server_name}'."
        )
        return 1

    body = {
        "type": "snapshot",
        "description": description or f"remo snapshot of {server_name}",
        "labels": {
            "remo": "true",
            "remo-snapshot-name": snap_name,
            "remo-source-server-id": str(server_id),
        },
    }
    try:
        _hetzner_api("POST", f"/servers/{server_id}/actions/create_image", body)
    except ProviderError as e:
        print_error(str(e))
        return 1

    print_info(
        f"Snapshot '{snap_name}' creation started for {server_name}. "
        f"This will take several minutes. "
        f"Run `remo hetzner snapshot list {server_name}` to check status."
    )
    return 0


def _wait_for_action(action_id: int, timeout: int = 600) -> bool:
    """Poll a Hetzner action until ``status`` is ``success``.

    Returns True on success, False on timeout/error. Sleeps 5s between polls.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = _hetzner_api("GET", f"/actions/{action_id}")
        except ProviderError:
            return False
        status = payload.get("action", {}).get("status", "")
        if status == "success":
            return True
        if status in {"error"}:
            return False
        time.sleep(5)
    return False


def snapshot_restore_legacy(
    server_name: str, snap_name: str, auto_confirm: bool = False
) -> int:
    """Rebuild *server_name* from snapshot *snap_name*.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_restore` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.

    Hetzner's rebuild is atomic from the user's perspective: server ID and
    IP are preserved (FR-013). We poll the rebuild action until success.
    Returns 0 on success, 1 on any failure.
    """
    guard_not_added_ssh_host(server_name, "hetzner")  # FR-012
    try:
        server = _get_server_by_name(server_name)
    except ProviderError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for hetzner instance '{server_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo hetzner snapshot list {server_name}` for status."
        )
        return 1
    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; cannot restore."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Restore '{snap_name}' to {server_name}? "
            f"Server will be rebuilt from the snapshot image — "
            f"typically 1-2 minutes of downtime.",
            default=False,
        ):
            raise UserAbortedError("Aborted.")

    try:
        payload = _hetzner_api(
            "POST",
            f"/servers/{server_id}/actions/rebuild",
            {"image": int(target.backend_id)},
        )
    except ProviderError as e:
        print_error(str(e))
        return 1

    action_id = payload.get("action", {}).get("id", 0)
    if not _wait_for_action(action_id):
        print_error(
            f"Rebuild action {action_id} did not complete successfully; "
            f"check the Hetzner Cloud console for details."
        )
        return 1

    print_info(
        f"Restored '{snap_name}' to {server_name}. "
        f"You can reconnect with: remo shell {server_name}"
    )
    return 0


def snapshot_list_legacy(server_name: str) -> list[Snapshot]:
    """Return remo-managed snapshots for *server_name*.

    Legacy signature retained for internal reuse. Called by
    :func:`snapshot_list` below, the Protocol Part A (entry-based,
    exception-raising) wrapper the generated CLI actually invokes.

    Raises :class:`PreconditionError` if the server cannot be found, or
    :class:`OperationFailedError` if the underlying Hetzner API call fails.
    """
    server = _get_server_by_name(server_name)
    return _list_snapshots_for_server(server.get("id", 0), server_name)


def snapshot_delete_legacy(
    server_name: str, snap_name: str, auto_confirm: bool = False
) -> int:
    """Delete the remo-managed Hetzner snapshot image *snap_name*.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_delete` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.
    """
    guard_not_added_ssh_host(server_name, "hetzner")  # FR-012
    try:
        server = _get_server_by_name(server_name)
    except ProviderError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for hetzner instance '{server_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo hetzner snapshot list {server_name}` for status."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Delete snapshot '{snap_name}' of {server_name}?",
            default=False,
        ):
            raise UserAbortedError("Aborted.")

    try:
        _hetzner_api("DELETE", f"/images/{target.backend_id}")
    except ProviderError as e:
        print_error(str(e))
        return 1

    print_info(f"Deleted snapshot '{snap_name}' of {server_name}.")
    return 0


# ---------------------------------------------------------------------------
# Entry-based snapshot verbs (contracts/provider-protocol.md Part A)
#
# Hetzner is FLAT (name_format) -- entry.name IS the server name directly,
# no host/container parsing needed (R-A2). These wrap the legacy
# rc-returning helpers above and convert failure into OperationFailedError
# (R-A1); snapshot_list_legacy already raises a typed ProviderError directly,
# so snapshot_list just propagates it.
# ---------------------------------------------------------------------------


def snapshot_create(entry: KnownHost, snapshot_name: str, *, description: str = "") -> None:
    """Create a snapshot of *entry*'s server."""
    rc = snapshot_create_legacy(
        server_name=entry.name, snap_name=snapshot_name, description=description
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to create snapshot '{snapshot_name}' for '{entry.name}' (rc={rc})."
        )


def snapshot_restore(entry: KnownHost, snapshot_name: str) -> None:
    """Restore *entry*'s server to *snapshot_name*."""
    rc = snapshot_restore_legacy(
        server_name=entry.name, snap_name=snapshot_name, auto_confirm=True
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to restore snapshot '{snapshot_name}' for '{entry.name}' (rc={rc})."
        )


def snapshot_delete(entry: KnownHost, snapshot_name: str) -> None:
    """Delete *snapshot_name* from *entry*'s server."""
    rc = snapshot_delete_legacy(
        server_name=entry.name, snap_name=snapshot_name, auto_confirm=True
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to delete snapshot '{snapshot_name}' for '{entry.name}' (rc={rc})."
        )


def snapshot_list(entry: KnownHost) -> list[Snapshot]:
    """List snapshots of *entry*'s server (R-A5: public on every provider).

    ``snapshot_list_legacy`` already raises a typed :class:`ProviderError`
    (``PreconditionError`` if not found, ``OperationFailedError`` on an API
    failure), so it propagates unchanged here.
    """
    return snapshot_list_legacy(server_name=entry.name)
