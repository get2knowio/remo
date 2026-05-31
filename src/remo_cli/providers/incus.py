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
import sys
from datetime import datetime, timezone

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_prefix,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import (
    confirm,
    print_broker_reconciliation,
    print_error,
    print_info,
    print_warning,
)
from remo_cli.core.snapshot import (
    handle_destroy_snapshot_cleanup,
    validate_name as validate_snapshot_name,
)
from remo_cli.core.ssh import detect_timezone
from remo_cli.core.validation import build_tool_args, parse_volume_size, validate_name
from remo_cli.core.version import get_current_version
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
                print_error(f"SSH to '{ssh_target}' failed: {result.stderr.strip()}")
                if not user:
                    print_warning(
                        f"Try specifying --user, e.g.: remo incus update --host {host} "
                        f"--user <username> {name}"
                    )
                sys.exit(1)
            container_ip = _extract_eth0_ip(result.stdout)
        except FileNotFoundError:
            print_error("ssh command not found")
            sys.exit(1)

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
) -> int:
    """Run incus_resize.yml against the Incus host.

    Pass any combination of *volume_size*, *cores*, and *memory*; the
    playbook adjusts only the axes whose value is set. Returns the
    ansible-playbook exit code (0 on success, including no-op).
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

    return run_playbook("incus_resize.yml", extra_vars, verbose=verbose)


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
) -> int:
    """Create a new Incus container and configure it with dev tools.

    Returns the ansible-playbook exit code (0 on success).
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

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    # Clear any stale registry entry so _resolve_container_ip queries
    # the Incus host for the fresh IP instead of returning cached values.
    remove_known_host("incus", f"{host}/{name}")

    print_broker_reconciliation("Reconciling")
    rc = run_playbook("incus_site.yml", extra_vars, verbose=verbose)

    if rc == 0:
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

        if volume_size or cores or memory:
            rc = _run_resize_playbook(
                name=name,
                host=host,
                user=user,
                volume_size=volume_size,
                cores=cores,
                memory=memory,
                verbose=verbose,
            )

    if rc == 0:
        print_info("Vault sidecar available at: remo shell -p _remo-vault")
    return rc


def destroy(
    name: str,
    host: str = "",
    user: str = "",
    remove_storage: bool = False,
    auto_confirm: bool = False,
    verbose: bool = False,
) -> int:
    """Destroy an Incus container.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")

    # If --host not specified, look up container in known_hosts.
    if not host:
        host, looked_up_user = _lookup_incus_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    if remove_storage:
        print_warning(
            "WARNING: --remove-storage will delete host mount directories — all data on bound mounts will be lost!"
        )

    # FR-020 through FR-023: surface any remo-managed snapshots and offer to
    # clean them up alongside the instance, before the destructive prompt.
    try:
        _pre_destroy_snapshots = _list_snapshots_for_container(host, name, user)
    except RuntimeError as e:
        print_warning(
            f"Could not list snapshots before destroy ({e}); "
            f"proceeding without snapshot cleanup."
        )
        _pre_destroy_snapshots = []
    handle_destroy_snapshot_cleanup(
        provider_label="Incus",
        instance=name,
        snapshots=_pre_destroy_snapshots,
        delete_one=lambda snap: snapshot_delete(
            container=name,
            host=host,
            user=user,
            snap_name=snap.name,
            auto_confirm=True,
        ),
        auto_confirm=auto_confirm,
        show_status=False,
    )

    if not auto_confirm:
        location = f" on {host}" if host and host != "localhost" else ""
        prompt = f"Destroy Incus container '{name}'{location}? This cannot be undone."
        if not confirm(prompt):
            print_info("Aborted.")
            return 0

    print_warning(
        "Destroy also tears down the managed _remo-vault sidecar and broker state."
    )
    print_info(f"Destroying Incus container '{name}'...")

    extra_vars: list[str] = [
        "-e", f"container_name={name}",
        "-e", f"preserve_data={'false' if remove_storage else 'true'}",
    ]

    if host != "localhost":
        extra_vars.extend(["-i", f"{host},"])
        extra_vars.extend(["-e", "target_hosts=all"])
        if user:
            extra_vars.extend(["-e", f"incus_host_user={user}"])

    rc = run_playbook("incus_teardown.yml", extra_vars, verbose=verbose)

    # Remove from known_hosts regardless of rc (best-effort cleanup).
    remove_known_host("incus", f"{host}/{name}")

    return rc


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
) -> int:
    """Re-configure dev tools on an existing Incus container.

    When any of *volume_size*, *cores*, or *memory* is provided, apply
    those resource changes (via incus config set / device override)
    before running the dev-tools configure playbook.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")
    volume_size = parse_volume_size(volume_size)

    # If --host not specified, look up container in known_hosts.
    if not host:
        host, looked_up_user = _lookup_incus_host(name)
        if not user and looked_up_user:
            user = looked_up_user

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
        rc = _run_resize_playbook(
            name=name,
            host=host,
            user=user,
            volume_size=volume_size,
            cores=cores,
            memory=memory,
            verbose=verbose,
        )
        if rc != 0:
            return rc

    print_info(f"Looking up container '{name}'...")

    container_ip = _resolve_container_ip(name, host, user)

    if not container_ip:
        print_error(f"Could not find IP for container '{name}'")
        print_warning(
            "Container may not exist, may be stopped, or may not have an IP yet"
        )
        ssh_target = f"{user}@{host}" if user else host
        print_warning(f"Check with: ssh {ssh_target} 'incus list {name}'")
        sys.exit(1)

    print_info(f"Found container at {container_ip}")
    print_info(f"Configuring container '{name}'...")

    extra_vars: list[str] = ["-e", f"container_ip={container_ip}"]

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    print_broker_reconciliation("Reconfiguring")
    return run_playbook("incus_configure.yml", extra_vars, verbose=verbose)


def list_hosts() -> None:
    """Print a formatted table of all registered Incus containers.

    Reads from the known-hosts registry and displays CONTAINER, INCUS HOST,
    SSH HOST, and SSH COMMAND columns.  If no Incus entries exist, prints a
    hint about creating one with ``remo incus create``.
    """
    entries = get_known_hosts(type_filter="incus")

    print(
        f"{'CONTAINER':<20} {'INCUS HOST':<20} {'SSH HOST':<20} SSH COMMAND"
    )
    print(
        f"{'---------':<20} {'----------':<20} {'--------':<20} -----------"
    )

    for entry in entries:
        if "/" in entry.name:
            incus_host, container = entry.name.split("/", maxsplit=1)
        else:
            incus_host = ""
            container = entry.name

        ssh_host = entry.host
        ssh_user = entry.user
        ssh_cmd = f"ssh {ssh_user}@{ssh_host}"

        print(f"{container:<20} {incus_host:<20} {ssh_host:<20} {ssh_cmd}")

    if not entries:
        print("No Incus containers registered.")
        print("Create one with: remo incus create <name>")


def info(name: str, host: str = "", user: str = "") -> int:
    """Print detailed information about an Incus container.

    Runs ``incus list <name> --format=json`` (locally or via SSH on the
    Incus host) and reports state, IP, CPU limit, memory limit, and root
    disk size. Returns 0 on success or 1 if the container could not be
    located.
    """
    import json

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
        print_error(
            f"Failed to query container '{name}' on '{host}': {result.stderr.strip()}"
        )
        return 1

    try:
        containers = json.loads(result.stdout)
    except json.JSONDecodeError:
        print_error(f"Could not parse incus output for '{name}'.")
        return 1

    if not containers:
        print_error(f"Container '{name}' was not found on Incus host '{host}'.")
        return 1

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

    return 0


def sync(host: str = "localhost", user: str = "", use_ip: bool = False) -> None:
    """Discover Incus containers on *host* and register them in known-hosts.

    For localhost, runs ``incus list -f csv -c n`` directly.  For remote hosts,
    the same command is executed over SSH.  All previously registered entries
    for the given host prefix are cleared before the newly discovered
    containers are saved.

    When *use_ip* is true, each container's eth0 IP is resolved and stored as
    the ``host`` field; otherwise the container name itself is stored (and
    relies on DNS/MagicDNS for resolution at connect time).
    """
    if host == "localhost":
        result = subprocess.run(
            ["incus", "list", "-f", "csv", "-c", "n"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print_error(f"Failed to list containers: {result.stderr.strip()}")
            sys.exit(1)
    else:
        ssh_target = f"{user}@{host}" if user else host
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", ssh_target,
             "incus list -f csv -c n"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print_error(
                f"Failed to list containers on '{host}': "
                f"{result.stderr.strip()}"
            )
            sys.exit(1)

    containers = [
        line.strip() for line in result.stdout.splitlines() if line.strip()
    ]

    # Clear existing entries for this host before re-populating.
    clear_known_hosts_by_prefix("incus", f"{host}/")

    for name in containers:
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

    print_info(f"Synced {len(containers)} container(s) from '{host}'.")


def bootstrap(
    host: str = "localhost",
    user: str = "",
    network_type: str = "",
    verbose: bool = False,
) -> int:
    """Initialize an Incus host by running the bootstrap playbook.

    Configures storage pools, networking, and other prerequisites so the
    host is ready to create containers.

    Returns the ansible-playbook exit code (0 on success).
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

    return run_playbook("incus_bootstrap.yml", extra_vars, verbose=verbose)


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


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
    :class:`RuntimeError` if the Incus call itself fails so the caller can
    surface the error per FR-011.
    """
    quoted = shlex.quote(container)
    cmd = f"incus query /1.0/instances/{quoted}/snapshots?recursion=1"
    result = _ssh_run_on_incus_host(host, user, cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"incus query failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )

    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as e:
        raise RuntimeError(f"incus query returned unparseable JSON: {e}") from e

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


def snapshot_create(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    description: str = "",
) -> int:
    """Create a snapshot of *container* on the Incus host.

    Returns 0 on success, 1 on provider failure or duplicate-name conflict
    (per FR-006). The snapshot name must already have been validated via
    :func:`core.snapshot.validate_name` by the CLI layer.
    """
    validate_snapshot_name(snap_name)  # belt-and-suspenders

    try:
        existing = _list_snapshots_for_container(host, container, user)
    except RuntimeError as e:
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


def snapshot_restore(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Restore *container* to *snap_name*.

    Validates that the snapshot exists and is :attr:`SnapshotStatus.AVAILABLE`
    (always true on Incus once present). Confirms with the user unless
    *auto_confirm* is True. Orchestrates stop → restore → start so the
    container ends up reachable in whatever state it was before (FR-013).
    Returns 0 on success, 1 on any failure.
    """
    try:
        existing = _list_snapshots_for_container(host, container, user)
    except RuntimeError as e:
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


def snapshot_delete(
    container: str,
    host: str,
    user: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Delete a snapshot of *container*."""
    try:
        existing = _list_snapshots_for_container(host, container, user)
    except RuntimeError as e:
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
