"""Incus container provider business logic for remo.

Manages the lifecycle of Incus containers: create, destroy, and update
(re-configure dev tools).  All functions are pure business logic with no
Click imports; CLI argument handling lives in the ``cli`` layer.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from datetime import datetime, timezone

from remo_cli.core.ansible_runner import (
    build_configure_extra_vars,
    run_playbook,
    run_resize_playbook as _run_resize_shared,
)
from remo_cli.core.config import (
    INCUS_MANAGED_CONFIG_KEY,
    INCUS_MANAGED_CONFIG_VALUE,
)
from remo_cli.core.errors import (
    OperationFailedError,
    PreconditionError,
)
from remo_cli.core.known_hosts import (
    get_known_hosts,
    guard_not_added_ssh_host,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import Column, confirm, print_error, print_info, print_warning, render_host_table
from remo_cli.core.reconcile import DiscoveredHost, ProbeError, ProbeResult, SyncScope, run_sync
from remo_cli.core.snapshot import validate_name as validate_snapshot_name
from remo_cli.core.validation import parse_volume_size, validate_name
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lookup_incus_host(name: str) -> tuple[str, str]:
    """Find the Incus host and host-user for *name* in the registry.

    Returns ``(host, user)`` where *host* defaults to ``"localhost"`` and
    *user* defaults to ``""`` when no matching entry is found.
    """
    for entry in get_known_hosts(type_filter="incus"):
        # name is in format: host/container
        if "/" in entry.name and entry.name.endswith(f"/{name}"):
            host = entry.name.split("/", maxsplit=1)[0]
            user = entry.instance_id  # host user stored in instance_id field
            return host, user
    return "localhost", ""


def _resolve_container_ip(
    name: str,
    host: str,
    user: str,
) -> str:
    """Determine the container's IP address.

    For remote hosts we first try the hostname stored in the known-hosts
    registry (which may be a Tailscale MagicDNS name reachable over the
    overlay network).  If nothing is stored, fall back to querying the Incus
    host via SSH (or locally) for the container's ``eth0`` address.
    """
    container_ip = ""

    # For remote hosts, prefer the known_hosts hostname.
    if host != "localhost":
        for entry in get_known_hosts(type_filter="incus"):
            if entry.name == f"{host}/{name}":
                if entry.host:
                    container_ip = entry.host
                break

    if container_ip:
        return container_ip

    # Fall back to querying the Incus host for the container's eth0 IP.
    if host == "localhost":
        try:
            raw = subprocess.run(
                ["incus", "list", name, "-f", "csv", "-c", "4"],
                capture_output=True,
                text=True,
            )
            container_ip = _extract_eth0_ip(raw.stdout)
        except FileNotFoundError:
            pass
    else:
        ssh_target = f"{user}@{host}" if user else host
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=10", ssh_target,
                 f"incus list '{name}' -f csv -c 4"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # Soft-fail: a transient SSH hiccup must not abort a sync or
                # crash create/update. Callers treat "" as "unknown", not
                # "gone" -- merge_entry preserves the previously recorded
                # address instead of overwriting it.
                print_warning(f"SSH to '{ssh_target}' failed: {result.stderr.strip()}")
                if not user:
                    print_warning(
                        f"Try specifying --user, e.g.: remo incus update --host {host} "
                        f"--user <username> {name}"
                    )
                return ""
            container_ip = _extract_eth0_ip(result.stdout)
        except FileNotFoundError:
            print_warning("ssh command not found")
            return ""

    return container_ip


def _extract_eth0_ip(incus_output: str) -> str:
    """Extract the first IPv4 address on ``eth0`` from ``incus list`` CSV output."""
    for line in incus_output.splitlines():
        if "eth0" in line:
            match = re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", line)
            if match:
                return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# Managed marker (feature 013-managed-instance-tags)
# ---------------------------------------------------------------------------


def _apply_managed_marker(host: str, user: str, name: str) -> tuple[bool, str]:
    """Apply the remo managed marker to Incus container *name* (host-side).

    Runs ``incus config set <name> user.remo=true`` on the Incus host (or
    locally when ``host == "localhost"``). Setting an already-present identical
    key is a no-op, so this is idempotent (FR-002). Returns ``(ok, err)`` where
    *err* is a short message on failure — callers warn but do not fail the whole
    command on this alone (FR-005).
    """
    cmd = (
        f"incus config set {shlex.quote(name)} "
        f"{INCUS_MANAGED_CONFIG_KEY}={INCUS_MANAGED_CONFIG_VALUE}"
    )
    result = _ssh_run_on_incus_host(host, user, cmd)
    if result.returncode != 0:
        return False, (result.stderr.strip() or result.stdout.strip())
    return True, ""


def _list_containers_with_marker(host: str, user: str) -> list[tuple[str, bool]]:
    """Return ``[(name, marked), ...]`` for every container on *host*.

    Uses a single bulk query ``incus list -f csv -c n,<marker-key>`` (FR-013):
    the second CSV column holds the marker value, so no per-container round-trip
    is needed. Raises :class:`OperationFailedError` if the ``incus list`` call
    fails so the caller can surface it.
    """
    cmd = f"incus list -f csv -c n,{INCUS_MANAGED_CONFIG_KEY}"
    result = _ssh_run_on_incus_host(host, user, cmd)
    if result.returncode != 0:
        raise OperationFailedError(
            f"incus list failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    rows: list[tuple[str, bool]] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        cname = parts[0].strip()
        if not cname:
            continue
        marker = parts[1].strip() if len(parts) > 1 else ""
        rows.append((cname, marker == INCUS_MANAGED_CONFIG_VALUE))
    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _run_resize_playbook(
    *,
    name: str,
    host: str,
    user: str,
    volume_size: str = "",
    cores: int = 0,
    memory: int = 0,
    verbose: bool = False,
) -> None:
    """Run incus_resize.yml against the Incus host.

    Pass any combination of *volume_size*, *cores*, and *memory*; the
    playbook adjusts only the axes whose value is set. Raises
    :class:`OperationFailedError` on a nonzero ansible-playbook rc.
    """
    extra_vars: list[str] = ["-e", f"container_name={name}"]
    if volume_size:
        extra_vars.extend(["-e", f"volume_size={volume_size}"])
    if cores:
        extra_vars.extend(["-e", f"cores={cores}"])
    if memory:
        extra_vars.extend(["-e", f"memory={memory}"])

    if host and host != "localhost":
        extra_vars.extend(["-i", f"{host},"])
        extra_vars.extend(["-e", "target_hosts=all"])
        if user:
            extra_vars.extend(["-e", f"incus_host_user={user}"])

    _run_resize_shared("incus_resize.yml", extra_vars, verbose=verbose)


def create(
    name: str,
    host: str = "localhost",
    user: str = "",
    domain: str = "",
    image: str = "",
    volume_size: str = "",
    cores: int = 0,
    memory: int = 0,
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    use_ip: bool = False,
    verbose: bool = False,
) -> None:
    """Create a new Incus container and configure it with dev tools.

    Raises :class:`OperationFailedError` on a nonzero ansible-playbook rc.
    """
    validate_name(name, "container name")
    volume_size = parse_volume_size(volume_size)

    print_info(f"Creating Incus container '{name}'...")

    extra_vars: list[str] = ["-e", f"container_name={name}"]

    if domain:
        extra_vars.extend(["-e", f"container_domain={domain}"])
    if image:
        extra_vars.extend(["-e", f"container_image={image}"])

    if host != "localhost":
        extra_vars.extend(["-i", f"{host},"])
        extra_vars.extend(["-e", "target_hosts=all"])
        if user:
            extra_vars.extend(["-e", f"incus_host_user={user}"])

    extra_vars.extend(build_configure_extra_vars(tools_only, tools_skip))

    # Clear any stale registry entry so _resolve_container_ip queries
    # the Incus host for the fresh IP instead of returning cached values.
    remove_known_host("incus", f"{host}/{name}")

    rc = run_playbook("incus_site.yml", extra_vars, verbose=verbose)

    if rc != 0:
        raise OperationFailedError(
            f"Failed to create Incus container '{name}' (playbook rc={rc})."
        )

    if use_ip:
        container_host = _resolve_container_ip(name, host, user) or name
    else:
        container_host = name
    save_known_host(
        KnownHost(
            type="incus",
            name=f"{host}/{name}",
            host=container_host,
            user="remo",
            instance_id=user,
            access_mode="direct",
        )
    )

    # FR-001: mark the container as remo-managed so a default `sync` picks
    # it up. FR-005: a marking failure warns but does not fail create.
    ok, err = _apply_managed_marker(host, user, name)
    if not ok:
        print_warning(
            f"Container '{name}' was created, but could not be marked as "
            f"remo-managed on Incus host '{host}' "
            f"({_host_access_desc(host, user)}, needed for "
            f"`incus config set`): {err}\n"
            f"  The container is fine. Until the marker is set, a default "
            f"`remo incus sync` will skip it; use `--all` or re-run "
            f"`remo incus update` to include it."
        )

    if volume_size or cores or memory:
        try:
            _run_resize_playbook(
                name=name,
                host=host,
                user=user,
                volume_size=volume_size,
                cores=cores,
                memory=memory,
                verbose=verbose,
            )
        except OperationFailedError as e:
            raise OperationFailedError(f"Container '{name}' was created but resizing failed: {e}") from e


def teardown(
    entry: KnownHost,
    *,
    verbose: bool = False,
    remove_storage: bool = False,
    **_ignored: object,
) -> None:
    """Destroy the Incus container backing *entry* (Protocol Part A).

    Provider-destruction only (R-A3): the guard, snapshot pre-cleanup,
    confirmation prompt, and registry removal all now live in
    ``core.lifecycle.run_destroy``, which calls this as its one
    provider-specific step. The generated CLI's ``destroy`` command also
    forwards its ``--host``/``--user`` destroy-options through as keyword
    arguments; they're accepted-but-ignored here (absorbed by
    ``**_ignored``) since the resolved *entry* is the sole source of truth
    for where the container lives (R-A2) — ``host`` doesn't affect where
    the playbook runs, and ``user`` is a stale hint superseded by
    ``entry.instance_id``.
    """
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    user = entry.instance_id  # host user stored in instance_id field

    if remove_storage:
        print_warning(
            "WARNING: --remove-storage will delete host mount directories — all data on bound mounts will be lost!"
        )

    extra_vars: list[str] = [
        "-e", f"container_name={container}",
        "-e", f"preserve_data={'false' if remove_storage else 'true'}",
    ]

    if incus_host != "localhost":
        extra_vars.extend(["-i", f"{incus_host},"])
        extra_vars.extend(["-e", "target_hosts=all"])
        if user:
            extra_vars.extend(["-e", f"incus_host_user={user}"])

    rc = run_playbook("incus_teardown.yml", extra_vars, verbose=verbose)
    if rc != 0:
        raise OperationFailedError(
            f"Failed to destroy Incus container '{container}' (playbook rc={rc})."
        )


def update(
    name: str,
    host: str = "",
    user: str = "",
    volume_size: str = "",
    cores: int = 0,
    memory: int = 0,
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> None:
    """Re-configure dev tools on an existing Incus container.

    When any of *volume_size*, *cores*, or *memory* is provided, apply
    those resource changes (via incus config set / device override)
    before running the dev-tools configure playbook.

    Raises :class:`PreconditionError` if the container's IP could not be
    resolved, or :class:`OperationFailedError` on a nonzero
    ansible-playbook rc.
    """
    validate_name(name, "container name")
    guard_not_added_ssh_host(name, "incus")  # FR-012
    volume_size = parse_volume_size(volume_size)

    # If --host not specified, look up container in known_hosts.
    if not host:
        host, looked_up_user = _lookup_incus_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    # FR-004: `update` doubles as the backfill path — ensure the managed marker
    # is present (idempotent). FR-005: warn on failure but do not fail update.
    ok, err = _apply_managed_marker(host, user, name)
    if not ok:
        print_warning(
            f"Could not mark container '{name}' as remo-managed on Incus host "
            f"'{host}' ({_host_access_desc(host, user)}, needed for "
            f"`incus config set`): {err}\n"
            f"  This is a host-side bookkeeping step only — the update itself "
            f"continues. Until the marker is set, a default `remo incus sync` "
            f"will skip it; use `remo incus sync --all`."
        )

    if volume_size or cores or memory:
        bits: list[str] = []
        if volume_size:
            bits.append(f"size={volume_size}GiB")
        if cores:
            bits.append(f"cores={cores}")
        if memory:
            bits.append(f"memory={memory}MiB")
        location = f" on {host}" if host and host != "localhost" else ""
        print_info(f"Updating resources on '{name}' ({', '.join(bits)}){location}...")
        _run_resize_playbook(
            name=name,
            host=host,
            user=user,
            volume_size=volume_size,
            cores=cores,
            memory=memory,
            verbose=verbose,
        )

    print_info(f"Looking up container '{name}'...")

    container_ip = _resolve_container_ip(name, host, user)

    if not container_ip:
        ssh_target = f"{user}@{host}" if user else host
        raise PreconditionError(
            f"Could not find IP for container '{name}'. Container may not "
            f"exist, may be stopped, or may not have an IP yet. Check with: "
            f"ssh {ssh_target} 'incus list {name}'"
        )

    print_info(f"Found container at {container_ip}")
    print_info(f"Configuring container '{name}'...")

    extra_vars: list[str] = ["-e", f"container_ip={container_ip}"]

    extra_vars.extend(build_configure_extra_vars(tools_only, tools_skip))

    rc = run_playbook("incus_configure.yml", extra_vars, verbose=verbose)
    if rc != 0:
        raise OperationFailedError(
            f"Failed to configure tools on container '{name}' (playbook rc={rc})."
        )


def update_entry(entry: KnownHost, *, verbose: bool = False) -> None:
    """Re-apply tool configuration to an existing container (Protocol Part A).

    Entry-based wrapper around :func:`update`: parses the host-scoped
    ``entry.name`` (``"<incus_host>/<container>"``) and the Incus-host SSH
    user carried in ``entry.instance_id`` (R-A2 — callers never parse
    names). ``update`` now raises directly on failure (R-A1), so this is a
    thin adapter.
    """
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    update(name=container, host=incus_host, user=entry.instance_id, verbose=verbose)


def _split_host_container(entry: KnownHost) -> tuple[str, str]:
    if "/" in entry.name:
        host, container = entry.name.split("/", maxsplit=1)
        return host, container
    return "", entry.name


_LIST_COLUMNS = (
    Column("CONTAINER", lambda e: _split_host_container(e)[1]),
    Column("INCUS HOST", lambda e: _split_host_container(e)[0]),
    Column("SSH HOST", lambda e: e.host),
    Column("SSH COMMAND", lambda e: f"ssh {e.user}@{e.host}"),
)


def list_hosts() -> None:
    """Print a formatted table of all registered Incus containers.

    Reads from the known-hosts registry and displays CONTAINER, INCUS HOST,
    SSH HOST, and SSH COMMAND columns.  If no Incus entries exist, prints a
    hint about creating one with ``remo incus create``.
    """
    entries = get_known_hosts(type_filter="incus")
    render_host_table(
        entries,
        _LIST_COLUMNS,
        empty_message="No Incus containers registered.\nCreate one with: remo incus create <name>",
    )


def info(name: str, host: str = "", user: str = "") -> None:
    """Print detailed information about an Incus container.

    Runs ``incus list <name> --format=json`` (locally or via SSH on the
    Incus host) and reports state, IP, CPU limit, memory limit, and root
    disk size. Raises :class:`OperationFailedError` if the query/parse
    fails, or :class:`PreconditionError` if the container could not be
    located.
    """
    validate_name(name, "container name")

    if not host:
        host, looked_up_user = _lookup_incus_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    if not host:
        host = "localhost"

    incus_cmd = f"incus list '{name}' --format=json"
    if host == "localhost":
        result = subprocess.run(
            ["incus", "list", name, "--format=json"],
            capture_output=True,
            text=True,
        )
    else:
        ssh_target = f"{user}@{host}" if user else host
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", ssh_target, incus_cmd],
            capture_output=True,
            text=True,
        )

    if result.returncode != 0:
        raise OperationFailedError(
            f"Failed to query container '{name}' on '{host}': {result.stderr.strip()}"
        )

    try:
        containers = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise OperationFailedError(f"Could not parse incus output for '{name}'.") from e

    if not containers:
        raise PreconditionError(f"Container '{name}' was not found on Incus host '{host}'.")

    container = containers[0]
    state = container.get("status", "unknown")
    expanded_config = container.get("expanded_config") or {}
    expanded_devices = container.get("expanded_devices") or {}

    cpu_limit = expanded_config.get("limits.cpu", "")
    memory_limit = expanded_config.get("limits.memory", "")
    root_device = expanded_devices.get("root") or {}
    root_size = root_device.get("size", "")
    root_pool = root_device.get("pool", "")

    container_ip = ""
    network = (container.get("state") or {}).get("network") or {}
    eth0 = network.get("eth0") or {}
    for addr in eth0.get("addresses", []):
        if addr.get("family") == "inet":
            container_ip = addr.get("address", "")
            break

    print("")
    print(f"  Name:       {name}")
    print(f"  Incus host: {host}")
    print(f"  State:      {state}")
    print(f"  IP:         {container_ip or '(unavailable)'}")
    print(f"  Cores:      {cpu_limit or '(profile default)'}")
    print(f"  Memory:     {memory_limit or '(profile default)'}")
    print(f"  Root size:  {root_size or '(profile default)'}{f' ({root_pool})' if root_pool else ''}")
    print("")


def _probe(scope: SyncScope, user: str, use_ip: bool, include_all: bool) -> ProbeResult:
    """Provider-probe for :func:`sync` (contracts/provider-probe.md "Incus").

    Returns every container on *scope.host* -- marked and unmarked alike;
    the marker only decides eligibility for addition, never what is even
    seen (FR-044). Read-only: issues one bulk listing query and, when
    *use_ip* is set, one IP lookup per container. Never writes/mutates the
    provider.
    """
    try:
        rows = _list_containers_with_marker(scope.host, user)
    except OperationFailedError as exc:
        raise ProbeError(f"Failed to list containers on '{scope.host}': {exc}") from exc

    warnings: list[str] = []
    hosts: list[DiscoveredHost] = []
    for cname, marked in rows:
        if use_ip:
            ip = _resolve_container_ip(cname, scope.host, user)
            if ip:
                container_host = ip
            else:
                # Soft IP-lookup failure: leave entry.host empty so
                # merge_entry preserves the previously recorded address
                # instead of overwriting it with the bare container name.
                container_host = ""
                warnings.append(
                    f"Could not resolve IP for '{cname}', keeping previously "
                    "recorded address"
                )
        else:
            container_host = cname

        entry = KnownHost(
            type="incus",
            name=f"{scope.host}/{cname}",
            host=container_host,
            user="remo",
            instance_id=user,
            access_mode="direct",
        )
        hosts.append(DiscoveredHost(entry=entry, marked=marked))

    return ProbeResult(
        hosts=hosts,
        complete=True,  # incus list never paginates
        adoption_criteria="every container on this Incus host",
        warnings=warnings,
    )


def sync(
    host: str = "localhost",
    user: str = "",
    use_ip: bool = False,
    include_all: bool = False,
    auto_confirm: bool = False,
    dry_run: bool = False,
) -> int:
    """Reconcile the registry's Incus entries for *host* against reality.

    Delegates diffing, consent, and the single atomic write to
    :func:`remo_cli.core.reconcile.run_sync`; this function's only job is
    the probe closure above. By default only marker-bearing containers are
    eligible for addition (FR-006); ``include_all=True`` widens that to
    every container (FR-007). Removals require a complete enumeration and
    consent (``auto_confirm`` or an interactive confirm), never happen on
    ``dry_run``, and an unmarked host that still exists is never removed
    (FR-022) -- there is no "later sync drops it again".

    Returns the process exit code (see ``core/reconcile.py`` EXIT_*).
    """
    scope = SyncScope(type="incus", host=host)
    return run_sync(
        scope,
        lambda: _probe(scope, user=user, use_ip=use_ip, include_all=include_all),
        auto_confirm=auto_confirm,
        dry_run=dry_run,
        include_all=include_all,
    )


def bootstrap(
    host: str = "localhost",
    user: str = "",
    network_type: str = "",
    verbose: bool = False,
) -> None:
    """Initialize an Incus host by running the bootstrap playbook.

    Configures storage pools, networking, and other prerequisites so the
    host is ready to create containers.

    Raises :class:`OperationFailedError` on a nonzero ansible-playbook rc.
    """
    extra_vars: list[str] = []

    if host != "localhost":
        extra_vars.extend(["-i", f"{host},"])
        extra_vars.extend(["-e", "target_hosts=all"])
        if user:
            extra_vars.extend(["-e", f"ansible_user={user}"])
    else:
        # On localhost with sudo, ansible_user is root; allow overriding
        # incus_user so the correct user gets added to the incus-admin group.
        if user:
            extra_vars.extend(["-e", f"incus_user={user}"])

    if network_type:
        extra_vars.extend(["-e", f"incus_network_type={network_type}"])

    if verbose:
        extra_vars.extend(["-e", "incus_bootstrap_verbosity=detailed"])

    rc = run_playbook("incus_bootstrap.yml", extra_vars, verbose=verbose)
    if rc != 0:
        raise OperationFailedError(f"Failed to bootstrap Incus host (playbook rc={rc}).")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _host_access_desc(host: str, user: str) -> str:
    """Describe how :func:`_ssh_run_on_incus_host` reaches *host*, for warnings.

    Mirrors that helper's localhost branch: host-side work on ``localhost``
    runs as a local subprocess, so a warning must not claim an SSH hop that
    never happened. An empty *user* means "let ssh_config decide" and prints
    as a bare host, not ``@host``.
    """
    if host == "localhost":
        return "locally"
    return f"over ssh {user}@{host}" if user else f"over ssh {host}"


def _ssh_run_on_incus_host(
    host: str, user: str, command: str
) -> subprocess.CompletedProcess[str]:
    """Run *command* on the Incus host (or locally when ``host == 'localhost'``).

    Returns the :class:`subprocess.CompletedProcess`; callers inspect
    ``returncode``, ``stdout``, ``stderr``. ConnectTimeout=10s applies to
    remote invocations only.
    """
    if host == "localhost":
        return subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
        )
    ssh_target = f"{user}@{host}" if user else host
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", ssh_target, command],
        capture_output=True,
        text=True,
    )


def _list_snapshots_for_container(
    host: str, container: str, user: str
) -> list[Snapshot]:
    """Return the snapshots of *container* on the Incus host.

    Queries ``incus query /1.0/instances/<container>/snapshots?recursion=1``
    over SSH (or locally) and parses the JSON response. Returns an empty
    list when the container has no snapshots. Raises
    :class:`OperationFailedError` if the Incus call itself fails so the
    caller can surface the error per FR-011.
    """
    quoted = shlex.quote(container)
    cmd = f"incus query /1.0/instances/{quoted}/snapshots?recursion=1"
    result = _ssh_run_on_incus_host(host, user, cmd)
    if result.returncode != 0:
        raise OperationFailedError(
            f"incus query failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as e:
        raise OperationFailedError(f"incus query returned unparseable JSON: {e}") from e

    snapshots: list[Snapshot] = []
    for item in items:
        # Incus snapshot name in the API is "<container>/<snap>"; we want
        # just the snap part for the user-facing name.
        full = item.get("name", "")
        _, _, snap_name = full.partition("/")
        created_raw = item.get("created_at") or ""
        created_at = _parse_incus_timestamp(created_raw)
        size_bytes = item.get("size") if isinstance(item.get("size"), int) else None
        snapshots.append(
            Snapshot(
                provider="incus",
                instance_name=container,
                name=snap_name or full,
                backend_id=full,
                created_at=created_at,
                size_bytes=size_bytes,
                description=item.get("description") or "",
                status=SnapshotStatus.AVAILABLE,
            )
        )
    return snapshots


def _parse_incus_timestamp(s: str) -> datetime:
    """Parse an Incus ISO-8601 timestamp; return epoch on failure."""
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    # Incus uses RFC 3339 with optional fractional seconds and a 'Z' suffix.
    cleaned = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def snapshot_create_legacy(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    description: str = "",
) -> int:
    """Create a snapshot of *container* on the Incus host.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_create` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.

    Returns 0 on success, 1 on provider failure or duplicate-name conflict
    (per FR-006). The snapshot name must already have been validated via
    :func:`core.snapshot.validate_name` by the CLI layer.
    """
    guard_not_added_ssh_host(container, "incus")  # FR-012
    validate_snapshot_name(snap_name)  # belt-and-suspenders

    try:
        existing = _list_snapshots_for_container(host, container, user)
    except OperationFailedError as e:
        print_error(str(e))
        return 1

    if any(s.name == snap_name for s in existing):
        print_error(
            f"Snapshot '{snap_name}' already exists for incus instance '{container}'."
        )
        return 1

    # `incus snapshot create` does not accept --description (the description
    # is only settable via the REST API on the snapshot resource, not via the
    # CLI flag). Run create first, then PATCH the description if supplied.
    create_cmd = (
        f"incus snapshot create {shlex.quote(container)} "
        f"{shlex.quote(snap_name)}"
    )
    result = _ssh_run_on_incus_host(host, user, create_cmd)
    if result.returncode != 0:
        print_error(
            f"incus snapshot create failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return 1

    if description:
        # PATCH /1.0/instances/<ct>/snapshots/<snap> {"description": ...}
        # Use `incus query` so we don't take a dependency on curl/jq inside
        # the container host.
        body = json.dumps({"description": description})
        patch_cmd = (
            f"incus query --wait -X PATCH "
            f"/1.0/instances/{shlex.quote(container)}/snapshots/"
            f"{shlex.quote(snap_name)} --data {shlex.quote(body)}"
        )
        patch_result = _ssh_run_on_incus_host(host, user, patch_cmd)
        if patch_result.returncode != 0:
            # The snapshot itself was created; only the description failed.
            # Warn but don't fail the whole operation.
            print_warning(
                f"Snapshot created but failed to set description: "
                f"{patch_result.stderr.strip() or patch_result.stdout.strip()}"
            )

    print_info(
        f"Created snapshot '{snap_name}' for incus instance '{container}'."
    )
    return 0


def _get_container_status(host: str, user: str, container: str) -> str:
    """Return ``"Running"``, ``"Stopped"``, or ``""`` if status can't be read."""
    quoted = shlex.quote(container)
    result = _ssh_run_on_incus_host(
        host, user, f"incus info {quoted} --format json"
    )
    if result.returncode != 0:
        return ""
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ""
    return info.get("status", "")


def snapshot_restore_legacy(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Restore *container* to *snap_name*.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_restore` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.

    Validates that the snapshot exists and is :attr:`SnapshotStatus.AVAILABLE`
    (always true on Incus once present). Confirms with the user unless
    *auto_confirm* is True. Orchestrates stop → restore → start so the
    container ends up reachable in whatever state it was before (FR-013).
    Returns 0 on success, 1 on any failure.
    """
    guard_not_added_ssh_host(container, "incus")  # FR-012
    try:
        existing = _list_snapshots_for_container(host, container, user)
    except OperationFailedError as e:
        print_error(str(e))
        return 1

    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for incus instance '{container}'."
        )
        return 1

    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; "
            f"run `remo incus snapshot list {container}` to check status."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Restore '{snap_name}' to {container}? "
            f"Container will be stopped during rollback.",
            default=False,
        ):
            print_info("Aborted.")
            return 1

    # Orchestrate stop → restore → start
    pre_status = _get_container_status(host, user, container)
    was_running = pre_status == "Running"

    if was_running:
        stop = _ssh_run_on_incus_host(
            host, user, f"incus stop {shlex.quote(container)}"
        )
        if stop.returncode != 0:
            print_error(
                f"Failed to stop container before restore: "
                f"{stop.stderr.strip() or stop.stdout.strip()}"
            )
            return 1

    restore = _ssh_run_on_incus_host(
        host,
        user,
        f"incus snapshot restore {shlex.quote(container)} {shlex.quote(snap_name)}",
    )
    if restore.returncode != 0:
        print_error(
            f"incus snapshot restore failed (rc={restore.returncode}): "
            f"{restore.stderr.strip() or restore.stdout.strip()}"
        )
        # Try to leave the container in the pre-restore state.
        if was_running:
            _ssh_run_on_incus_host(
                host, user, f"incus start {shlex.quote(container)}"
            )
        return 1

    if was_running:
        start = _ssh_run_on_incus_host(
            host, user, f"incus start {shlex.quote(container)}"
        )
        if start.returncode != 0:
            print_error(
                f"Container restored but failed to start: "
                f"{start.stderr.strip() or start.stdout.strip()}"
            )
            return 1

    print_info(
        f"Restored '{snap_name}' to {container}. "
        f"You can reconnect with: remo shell {container}"
    )
    return 0


def snapshot_delete_legacy(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Delete a snapshot of *container*.

    Legacy rc-returning, multi-kwarg signature retained for internal reuse.
    Called by :func:`snapshot_delete` below, the Protocol Part A
    (entry-based, exception-raising) wrapper the generated CLI actually
    invokes.
    """
    guard_not_added_ssh_host(container, "incus")  # FR-012
    try:
        existing = _list_snapshots_for_container(host, container, user)
    except OperationFailedError as e:
        print_error(str(e))
        return 1

    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for incus instance '{container}'."
        )
        return 1
    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; "
            f"run `remo incus snapshot list {container}` to check status."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Delete snapshot '{snap_name}' of {container}?", default=False
        ):
            print_info("Aborted.")
            return 1

    result = _ssh_run_on_incus_host(
        host,
        user,
        f"incus snapshot delete {shlex.quote(container)} {shlex.quote(snap_name)}",
    )
    if result.returncode != 0:
        print_error(
            f"incus snapshot delete failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return 1

    print_info(f"Deleted snapshot '{snap_name}' of {container}.")
    return 0


# ---------------------------------------------------------------------------
# Protocol Part A — entry-based wrappers (contracts/provider-protocol.md)
# ---------------------------------------------------------------------------
#
# These are the Protocol-conformant public surface (``core/provider_protocol.py``
# ``Provider``): they take a resolved registry entry, do all host/container
# name-parsing internally (R-A2), and raise a taxonomy error instead of
# returning an rc (R-A1). They delegate to the legacy rc-returning,
# multi-kwarg functions above, which do their own "host/container"
# name-parsing and remain purely for internal reuse — the generated CLI
# (``cli/providers/factory.py``) only ever calls the Protocol wrappers below.


def snapshot_create(entry: KnownHost, snapshot_name: str, *, description: str = "") -> None:
    """Create a snapshot of the container backing *entry* (Protocol Part A)."""
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    rc = snapshot_create_legacy(
        container=container,
        host=incus_host,
        user=entry.instance_id,
        snap_name=snapshot_name,
        description=description,
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to create snapshot '{snapshot_name}' on '{entry.name}'."
        )


def snapshot_restore(entry: KnownHost, snapshot_name: str) -> None:
    """Restore the container backing *entry* to *snapshot_name* (Protocol Part A).

    Entry-based Protocol callers have no CLI prompt available, so this
    always confirms (``auto_confirm=True``); the interactive confirmation
    lives only in the legacy CLI-facing path.
    """
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    rc = snapshot_restore_legacy(
        container=container,
        host=incus_host,
        user=entry.instance_id,
        snap_name=snapshot_name,
        auto_confirm=True,
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to restore snapshot '{snapshot_name}' on '{entry.name}'."
        )


def snapshot_delete(entry: KnownHost, snapshot_name: str) -> None:
    """Delete a snapshot of the container backing *entry* (Protocol Part A).

    Always confirms (``auto_confirm=True``) — see :func:`snapshot_restore`.
    """
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    rc = snapshot_delete_legacy(
        container=container,
        host=incus_host,
        user=entry.instance_id,
        snap_name=snapshot_name,
        auto_confirm=True,
    )
    if rc != 0:
        raise OperationFailedError(
            f"Failed to delete snapshot '{snapshot_name}' on '{entry.name}'."
        )


def snapshot_list(entry: KnownHost) -> list[Snapshot]:
    """List snapshots of the container backing *entry* (Protocol Part A, R-A5).

    Public on every provider — eliminates the Incus/Proxmox private
    reach-ins the CLI layer previously needed. ``_list_snapshots_for_container``
    already raises :class:`OperationFailedError` on failure, so no
    translation is needed here.
    """
    incus_host, sep, container = entry.name.partition("/")
    if not sep:
        incus_host, container = "localhost", entry.name
    return _list_snapshots_for_container(
        host=incus_host, container=container, user=entry.instance_id
    )
