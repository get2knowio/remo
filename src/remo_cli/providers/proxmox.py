"""Proxmox VE LXC container provider business logic for remo.

Manages the lifecycle of Proxmox LXC containers: create, destroy, update
(re-configure dev tools), list, sync, bootstrap.  All functions are pure
business logic with no Click imports; CLI argument handling lives in the
``cli`` layer.

Mirrors :mod:`remo_cli.providers.incus` in shape; substitutes ``pct`` for
``incus`` and uses the ``instance_id`` field of :class:`KnownHost` to store
the numeric VMID.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.config import PROXMOX_MANAGED_TAG
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_prefix,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import confirm, print_error, print_info, print_warning
from remo_cli.core.snapshot import (
    handle_destroy_snapshot_cleanup,
    validate_name as validate_snapshot_name,
)
from remo_cli.core.ssh import detect_timezone
from remo_cli.core.validation import (
    build_tool_args,
    parse_volume_size,
    resolve_devcontainer_runtime,
    validate_name,
)
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _lookup_proxmox_host(name: str) -> tuple[str, str, str]:
    """Find the Proxmox node, host SSH user, and VMID for *name*.

    Returns ``(host, user, vmid)``; missing fields are returned as empty
    strings and *host* defaults to ``""`` (caller must supply it explicitly).

    The Proxmox provider uses the ``instance_id`` slot for the numeric VMID
    and the ``region`` slot for the SSH user on the Proxmox host. (Incus uses
    ``instance_id`` for the host user; we trade that off because Proxmox needs
    both VMID and user to do its job.)
    """
    for entry in get_known_hosts(type_filter="proxmox"):
        if "/" in entry.name and entry.name.endswith(f"/{name}"):
            host = entry.name.split("/", maxsplit=1)[0]
            return host, entry.region, entry.instance_id
    return "", "", ""


def _ssh_run(host: str, user: str, command: str) -> subprocess.CompletedProcess[str]:
    """Run *command* on *host* via SSH and return the completed process.

    Mirrors the inline pattern used by ``providers.incus``; consolidated here
    for clarity.
    """
    ssh_target = f"{user}@{host}" if user else host
    return subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", ssh_target, command],
        capture_output=True,
        text=True,
    )


def _run_on_node(
    host: str, user: str, command: str
) -> subprocess.CompletedProcess[str]:
    """Run *command* on the Proxmox node — locally when ``host == 'localhost'``,
    otherwise over SSH. Used by marker apply/read so both paths behave the same.
    """
    if host == "localhost":
        return subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True
        )
    return _ssh_run(host, user, command)


# ---------------------------------------------------------------------------
# Managed marker (feature 013-managed-instance-tags)
# ---------------------------------------------------------------------------


def _parse_container_tags(config_text: str) -> list[str]:
    """Return the ordered guest tags from the ``tags:`` line of ``pct config``.

    Proxmox stores tags separated by ``;`` (and accepts ``;``, ``,`` or space
    on input); returns ``[]`` when the container has no tags.
    """
    line = _parse_pct_config_field(config_text, "tags")
    if not line:
        return []
    return [t for t in re.split(r"[;, ]+", line.strip()) if t]


def _apply_managed_marker(host: str, user: str, vmid: str) -> tuple[bool, str]:
    """Apply the remo managed tag to Proxmox LXC *vmid* (host-side).

    Reads the current tag set from ``pct config <vmid>`` and, only when the
    ``remo`` tag is absent, writes the union back with ``pct set <vmid> --tags``
    (FR-003: existing tags preserved and not reordered; the new tag is
    appended). When ``remo`` is already present this is a strict no-op (FR-002,
    SC-005). Returns ``(ok, err)``; a failure warns but does not fail the
    enclosing command on its own (FR-005).
    """
    if not vmid:
        return False, "VMID could not be resolved"

    cfg = _run_on_node(host, user, f"pct config {shlex.quote(vmid)}")
    if cfg.returncode != 0:
        return False, (cfg.stderr.strip() or cfg.stdout.strip())

    tags = _parse_container_tags(cfg.stdout)
    if PROXMOX_MANAGED_TAG in tags:
        return True, ""  # already marked — no-op, no reorder

    joined = ";".join([*tags, PROXMOX_MANAGED_TAG])
    res = _run_on_node(
        host, user, f"pct set {shlex.quote(vmid)} --tags {shlex.quote(joined)}"
    )
    if res.returncode != 0:
        return False, (res.stderr.strip() or res.stdout.strip())
    return True, ""


def _read_tags_by_vmid(host: str, user: str) -> dict[str, set[str]]:
    """Return ``{vmid: {tags}}`` for every LXC on *host* in one bulk query.

    Runs a single ``grep -H '^tags:' /etc/pve/lxc/*.conf`` (FR-013) — no
    per-container ``pct config`` round-trip. A vmid whose conf has no ``tags:``
    line is simply absent from the map and is treated as unmarked by callers.
    """
    result = _run_on_node(
        host, user, "grep -H '^tags:' /etc/pve/lxc/*.conf 2>/dev/null"
    )
    # grep exits 1 when there are no matches; that is not an error here.
    mapping: dict[str, set[str]] = {}
    for line in result.stdout.splitlines():
        path, sep, rest = line.partition(":")
        if not sep:
            continue
        m = re.search(r"/(\d+)\.conf$", path)
        if not m:
            continue
        _, _, tag_values = rest.partition(":")  # strip the leading "tags"
        mapping[m.group(1)] = {
            t for t in re.split(r"[;, ]+", tag_values.strip()) if t
        }
    return mapping


def _resolve_vmid(name: str, host: str, user: str) -> str:
    """Determine the VMID for container *name* on the Proxmox *host*.

    Checks the known-hosts registry first; falls back to SSH'ing the host and
    grepping ``/etc/pve/lxc/*.conf`` for a matching ``hostname:`` line.
    Returns ``""`` if no match is found.
    """
    for entry in get_known_hosts(type_filter="proxmox"):
        if entry.name == f"{host}/{name}" and entry.instance_id:
            return entry.instance_id

    if not host:
        return ""

    # Fall back to a remote lookup by hostname.
    cmd = (
        rf"grep -l '^hostname: {name}$' /etc/pve/lxc/*.conf 2>/dev/null "
        r"| head -1 | sed 's:.*/\([0-9]\+\)\.conf:\1:'"
    )
    result = _ssh_run(host, user, cmd)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _resolve_container_ip(
    name: str,
    host: str,
    user: str,
    vmid: str = "",
) -> str:
    """Determine the container's IP address.

    Prefers the cached IP from the known-hosts registry. Falls back to
    ``ssh <host> "pct exec <vmid> -- ip -4 -o addr show dev eth0"``.
    """
    for entry in get_known_hosts(type_filter="proxmox"):
        if entry.name == f"{host}/{name}" and entry.host:
            return entry.host

    if not host:
        return ""

    if not vmid:
        vmid = _resolve_vmid(name, host, user)
    if not vmid:
        return ""

    cmd = f"pct exec {vmid} -- ip -4 -o addr show dev eth0"
    result = _ssh_run(host, user, cmd)
    if result.returncode != 0:
        return ""

    match = re.search(r"inet (\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", result.stdout)
    return match.group(1) if match else ""


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
    vmid: str = "",
    verbose: bool = False,
) -> int:
    """Run proxmox_resize.yml against the given Proxmox host.

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
    if vmid:
        extra_vars.extend(["-e", f"container_vmid={vmid}"])

    extra_vars.extend(["-i", f"{host},"])
    extra_vars.extend(["-e", "target_hosts=all"])
    if user:
        extra_vars.extend(["-e", f"proxmox_host_user={user}"])

    return run_playbook("proxmox_resize.yml", extra_vars, verbose=verbose)


def create(
    name: str,
    host: str,
    user: str = "",
    node: str = "",
    bridge: str = "",
    storage: str = "",
    template: str = "",
    cores: int = 0,
    memory: int = 0,
    volume_size: str = "",
    unprivileged: bool = True,
    domain: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    use_ip: bool = False,
    devcontainer_runtime: str | None = None,
    verbose: bool = False,
) -> int:
    """Create a new Proxmox LXC container and configure dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")
    volume_size = parse_volume_size(volume_size)

    if not host:
        print_error("Proxmox host is required (use --host).")
        return 1

    print_info(f"Creating Proxmox LXC container '{name}' on {host}...")

    extra_vars: list[str] = ["-e", f"container_name={name}"]

    if node:
        extra_vars.extend(["-e", f"container_node={node}"])
    if bridge:
        extra_vars.extend(["-e", f"container_bridge={bridge}"])
    if storage:
        extra_vars.extend(["-e", f"container_storage={storage}"])
    if template:
        extra_vars.extend(["-e", f"container_template={template}"])
    if cores:
        extra_vars.extend(["-e", f"container_cores={cores}"])
    if memory:
        extra_vars.extend(["-e", f"container_memory={memory}"])
    if volume_size:
        extra_vars.extend(["-e", f"container_disk={volume_size}"])
    if domain:
        extra_vars.extend(["-e", f"container_domain={domain}"])

    extra_vars.extend(
        ["-e", f"container_unprivileged={'true' if unprivileged else 'false'}"]
    )

    extra_vars.extend(["-i", f"{host},"])
    extra_vars.extend(["-e", "target_hosts=all"])
    if user:
        extra_vars.extend(["-e", f"proxmox_host_user={user}"])

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    runtime = resolve_devcontainer_runtime(devcontainer_runtime)
    extra_vars.extend(["-e", f"devcontainer_runtime={runtime}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    # Clear any stale registry entry so _resolve_container_ip queries the
    # Proxmox host for the fresh IP instead of returning a cached value.
    remove_known_host("proxmox", f"{host}/{name}")

    rc = run_playbook("proxmox_site.yml", extra_vars, verbose=verbose)

    if rc == 0:
        vmid = _resolve_vmid(name, host, user)
        if use_ip:
            container_host = _resolve_container_ip(name, host, user, vmid=vmid) or name
        else:
            container_host = name
        save_known_host(
            KnownHost(
                type="proxmox",
                name=f"{host}/{name}",
                host=container_host,
                user="remo",
                instance_id=vmid,
                access_mode="direct",
                region=user or "root",
            )
        )

        # FR-001: mark the container as remo-managed. FR-005: a marking failure
        # (including an unresolved VMID) warns but does not fail create.
        if vmid:
            ok, err = _apply_managed_marker(host, user, vmid)
            if not ok:
                print_warning(
                    f"Container '{name}' was created but could not be marked "
                    f"as remo-managed ({err}); a default `remo proxmox sync` "
                    f"will skip it (use `--all` or `remo proxmox update`)."
                )
        else:
            print_warning(
                f"Container '{name}' was created but its VMID could not be "
                f"resolved, so it was not marked as remo-managed; run "
                f"`remo proxmox update --name {name} --host {host}` to mark it."
            )

        # If the container already existed, site.yml skipped pct create and
        # did not apply the requested resource values. Run the resize
        # playbook as a follow-up; idempotent (no-op when values match).
        if volume_size or cores or memory:
            rc = _run_resize_playbook(
                name=name,
                host=host,
                user=user,
                volume_size=volume_size,
                cores=cores,
                memory=memory,
                vmid=vmid,
                verbose=verbose,
            )

    return rc


def destroy(
    name: str,
    host: str = "",
    user: str = "",
    purge: bool = False,
    auto_confirm: bool = False,
    verbose: bool = False,
) -> int:
    """Destroy a Proxmox LXC container.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")

    vmid = ""
    if not host:
        host, looked_up_user, vmid = _lookup_proxmox_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    if not host:
        print_error(
            f"Proxmox host for container '{name}' could not be determined.\n"
            "Use --host (and --user) to specify it explicitly."
        )
        return 1

    # Proxmox node SSH defaults to root when nothing else is known.
    if not user:
        user = "root"

    # FR-020 through FR-023: surface remo-managed snapshots before destroying.
    if vmid:
        try:
            _pre = _list_snapshots_for_vmid(host, user, vmid, name)
        except RuntimeError as e:
            print_warning(
                f"Could not list snapshots before destroy ({e}); "
                f"proceeding without snapshot cleanup."
            )
            _pre = []
        handle_destroy_snapshot_cleanup(
            provider_label="Proxmox",
            instance=name,
            snapshots=_pre,
            delete_one=lambda snap: snapshot_delete(
                container=name,
                host=host,
                user=user,
                vmid=vmid,
                snap_name=snap.name,
                auto_confirm=True,
            ),
            auto_confirm=auto_confirm,
            show_status=False,
        )

    if not auto_confirm:
        prompt = f"Destroy Proxmox LXC container '{name}' on {host}? This cannot be undone."
        if not confirm(prompt):
            print_info("Aborted.")
            return 0

    print_info(f"Destroying Proxmox LXC container '{name}' on {host}...")

    extra_vars: list[str] = [
        "-e", f"container_name={name}",
        "-e", f"purge={'true' if purge else 'false'}",
    ]
    if vmid:
        extra_vars.extend(["-e", f"container_vmid={vmid}"])

    extra_vars.extend(["-i", f"{host},"])
    extra_vars.extend(["-e", "target_hosts=all"])
    if user:
        extra_vars.extend(["-e", f"proxmox_host_user={user}"])

    rc = run_playbook("proxmox_teardown.yml", extra_vars, verbose=verbose)

    # Best-effort registry cleanup regardless of rc.
    remove_known_host("proxmox", f"{host}/{name}")

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
    devcontainer_runtime: str | None = None,
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing Proxmox LXC container.

    When any of *volume_size*, *cores*, or *memory* is provided, apply
    those resource changes (via pct resize / pct set) before running the
    dev-tools configure playbook.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")
    volume_size = parse_volume_size(volume_size)

    vmid = ""
    if not host:
        host, looked_up_user, vmid = _lookup_proxmox_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    if not host:
        print_error(
            f"Proxmox host for container '{name}' could not be determined.\n"
            "Use --host (and --user) to specify it explicitly."
        )
        return 1

    if not user:
        user = "root"

    # FR-004: `update` is the backfill path — ensure the managed marker is
    # present (idempotent, preserving existing tags). FR-005: warn on failure
    # but do not fail update. Resolve the VMID if the registry did not have it.
    if not vmid:
        vmid = _resolve_vmid(name, host, user)
    if vmid:
        ok, err = _apply_managed_marker(host, user, vmid)
        if not ok:
            print_warning(
                f"Could not mark container '{name}' as remo-managed ({err}); "
                f"it may not be picked up by a default `remo proxmox sync`."
            )
    else:
        print_warning(
            f"Could not resolve a VMID for '{name}'; it was not marked as "
            f"remo-managed."
        )

    if volume_size or cores or memory:
        bits: list[str] = []
        if volume_size:
            bits.append(f"rootfs={volume_size}G")
        if cores:
            bits.append(f"cores={cores}")
        if memory:
            bits.append(f"memory={memory}MiB")
        print_info(f"Updating resources on '{name}' ({', '.join(bits)}) on {host}...")
        rc = _run_resize_playbook(
            name=name,
            host=host,
            user=user,
            volume_size=volume_size,
            cores=cores,
            memory=memory,
            vmid=vmid,
            verbose=verbose,
        )
        if rc != 0:
            return rc

    print_info(f"Looking up container '{name}' on {host}...")

    container_ip = _resolve_container_ip(name, host, user, vmid=vmid)

    if not container_ip:
        print_error(f"Could not find IP for container '{name}'")
        print_warning(
            "Container may not exist, may be stopped, or may not have an IP yet"
        )
        ssh_target = f"{user}@{host}" if user else host
        print_warning(f"Check with: ssh {ssh_target} 'pct list'")
        sys.exit(1)

    print_info(f"Found container at {container_ip}")
    print_info(f"Configuring container '{name}'...")

    extra_vars: list[str] = ["-e", f"container_ip={container_ip}"]

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    runtime = resolve_devcontainer_runtime(devcontainer_runtime)
    extra_vars.extend(["-e", f"devcontainer_runtime={runtime}"])

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    return run_playbook("proxmox_configure.yml", extra_vars, verbose=verbose)


def list_hosts() -> None:
    """Print a formatted table of all registered Proxmox containers."""
    entries = get_known_hosts(type_filter="proxmox")

    print(
        f"{'CONTAINER':<20} {'NODE':<20} {'VMID':<8} {'SSH HOST':<20} SSH COMMAND"
    )
    print(
        f"{'---------':<20} {'----':<20} {'----':<8} {'--------':<20} -----------"
    )

    for entry in entries:
        if "/" in entry.name:
            node, container = entry.name.split("/", maxsplit=1)
        else:
            node = ""
            container = entry.name

        vmid = entry.instance_id or "-"
        ssh_host = entry.host
        ssh_user = entry.user
        ssh_cmd = f"ssh {ssh_user}@{ssh_host}"

        print(f"{container:<20} {node:<20} {vmid:<8} {ssh_host:<20} {ssh_cmd}")

    if not entries:
        print("No Proxmox containers registered.")
        print("Create one with: remo proxmox create <name> --host <node>")


def info(name: str, host: str = "", user: str = "") -> int:
    """Print detailed information about a Proxmox LXC container.

    Reads ``pct config`` and ``pct status`` over SSH on the Proxmox host,
    then prints state, network, CPU, memory, and rootfs details. Returns
    0 on success or 1 if the container could not be located.
    """
    validate_name(name, "container name")

    vmid = ""
    if not host:
        host, looked_up_user, vmid = _lookup_proxmox_host(name)
        if not user and looked_up_user:
            user = looked_up_user

    if not host:
        print_error(
            f"Proxmox host for container '{name}' could not be determined.\n"
            "Use --host (and --user) to specify it explicitly."
        )
        return 1

    if not user:
        user = "root"

    if not vmid:
        vmid = _resolve_vmid(name, host, user)
    if not vmid:
        print_error(f"Container '{name}' was not found on Proxmox host '{host}'.")
        return 1

    # Single SSH round-trip: combine config + status.
    cmd = f"pct config {vmid}; echo ---STATUS---; pct status {vmid}"
    result = _ssh_run(host, user, cmd)
    if result.returncode != 0:
        print_error(
            f"Failed to query container '{name}' on '{host}': {result.stderr.strip()}"
        )
        return 1

    config_text, _, status_text = result.stdout.partition("---STATUS---")

    cores = _parse_pct_config_field(config_text, "cores")
    memory = _parse_pct_config_field(config_text, "memory")
    swap = _parse_pct_config_field(config_text, "swap")
    hostname = _parse_pct_config_field(config_text, "hostname") or name
    rootfs_line = _parse_pct_config_field(config_text, "rootfs")
    rootfs_size = ""
    rootfs_storage = ""
    if rootfs_line:
        # rootfs format: "vmpool:subvol-100-disk-0,size=20G"
        rootfs_storage = rootfs_line.split(",", 1)[0]
        size_match = re.search(r"size=(\S+)", rootfs_line)
        if size_match:
            rootfs_size = size_match.group(1)

    state = ""
    state_match = re.search(r"status:\s*(\S+)", status_text)
    if state_match:
        state = state_match.group(1)

    container_ip = _resolve_container_ip(name, host, user, vmid=vmid)

    print("")
    print(f"  Name:       {hostname}")
    print(f"  VMID:       {vmid}")
    print(f"  Node:       {host}")
    print(f"  State:      {state or 'unknown'}")
    print(f"  IP:         {container_ip or '(unavailable)'}")
    print(f"  Cores:      {cores or '?'}")
    print(f"  Memory:     {memory + ' MiB' if memory else '?'}")
    if swap:
        print(f"  Swap:       {swap} MiB")
    print(f"  Rootfs:     {rootfs_size or '?'}{f' ({rootfs_storage})' if rootfs_storage else ''}")
    print("")

    return 0


def _parse_pct_config_field(config_text: str, field: str) -> str:
    """Return the value of *field* from the output of ``pct config``.

    Returns an empty string when the field is not present.
    """
    pattern = rf"^{re.escape(field)}:\s*(.+)$"
    match = re.search(pattern, config_text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def sync(
    host: str,
    user: str = "",
    use_ip: bool = False,
    include_all: bool = False,
) -> None:
    """Discover Proxmox LXC containers on *host* and register them.

    Runs ``pct list`` for the vmid/name inventory and one bulk
    ``grep '^tags:' /etc/pve/lxc/*.conf`` to read every container's tags in a
    single round-trip (FR-013). By default (``include_all=False``) only
    containers carrying the ``remo`` managed tag are registered (FR-006), and
    skipped unmarked containers are named in a hint (FR-008). With
    ``include_all=True`` every container is registered — the pre-feature
    behavior (FR-007) — and unmarked adoptions are called out (FR-009).

    This function never mutates container state (FR-010). When *use_ip* is true,
    each container's eth0 IP is resolved and stored as the ``host`` field.

    Existing entries with the host prefix are cleared first.
    """
    if not host:
        print_error("Proxmox host is required (use --host).")
        sys.exit(1)

    # `pct list` columns: VMID Status Lock Name
    if host == "localhost":
        result = subprocess.run(
            ["pct", "list"], capture_output=True, text=True
        )
    else:
        result = _ssh_run(host, user, "pct list")

    if result.returncode != 0:
        print_error(
            f"Failed to list containers on '{host}': {result.stderr.strip()}"
        )
        sys.exit(1)

    containers: list[tuple[str, str]] = []  # (vmid, hostname)
    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if len(parts) < 2:
            continue
        vmid = parts[0]
        if not vmid.isdigit():
            continue
        # `pct list` puts Name in the last column; Lock may be empty.
        hostname = parts[-1]
        containers.append((vmid, hostname))

    tags_by_vmid = _read_tags_by_vmid(host, user)

    clear_known_hosts_by_prefix("proxmox", f"{host}/")

    registered = 0
    skipped: list[str] = []
    adopted_unmarked: list[str] = []
    for vmid, hostname in containers:
        marked = PROXMOX_MANAGED_TAG in tags_by_vmid.get(vmid, set())
        if not include_all and not marked:
            skipped.append(hostname)
            continue
        if include_all and not marked:
            adopted_unmarked.append(hostname)
        if use_ip:
            container_host = _resolve_container_ip(hostname, host, user, vmid=vmid) or hostname
        else:
            container_host = hostname
        save_known_host(
            KnownHost(
                type="proxmox",
                name=f"{host}/{hostname}",
                host=container_host,
                user="remo",
                instance_id=vmid,
                access_mode="direct",
                region=user or "root",
            )
        )
        registered += 1

    print_info(f"Synced {registered} container(s) from '{host}'.")

    if not include_all and skipped:
        print_warning(
            f"Skipped {len(skipped)} unmarked container(s): {', '.join(skipped)}"
        )
        print_info(
            f"  • Adopt all this run:      remo proxmox sync --host {host} --all"
        )
        print_info(
            "  • Mark one permanently:    remo proxmox update --name <name> "
            f"--host {host}"
        )

    if include_all and adopted_unmarked:
        print_warning(
            f"{len(adopted_unmarked)} of the registered container(s) are not "
            f"remo-created (adopted via --all): {', '.join(adopted_unmarked)}"
        )
        print_info(
            "Note: a later default `sync` will drop those unmarked one(s) again."
        )


def bootstrap(
    host: str,
    user: str = "",
    bridge: str = "",
    storage: str = "",
    template: str = "",
    verbose: bool = False,
) -> int:
    """Verify a Proxmox node is ready and download the default template.

    Returns the ansible-playbook exit code (0 on success).
    """
    if not host:
        print_error("Proxmox host is required (use --host).")
        return 1

    extra_vars: list[str] = ["-i", f"{host},", "-e", "target_hosts=all"]
    if user:
        extra_vars.extend(["-e", f"ansible_user={user}"])
    if bridge:
        extra_vars.extend(["-e", f"proxmox_bridge={bridge}"])
    if storage:
        extra_vars.extend(["-e", f"proxmox_storage={storage}"])
    if template:
        extra_vars.extend(["-e", f"proxmox_template={template}"])

    return run_playbook("proxmox_bootstrap.yml", extra_vars, verbose=verbose)


# ---------------------------------------------------------------------------
# Internal: kept around for symmetry with providers.incus, may be useful
# for a future `--output-format json` flag on `list`/`sync`.
# ---------------------------------------------------------------------------


def _parse_pct_json(stdout: str) -> list[dict[str, str]]:
    """Parse the JSON output of ``pvesh get /nodes/<node>/lxc --output-format json``."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


# Proxmox storage types that support snapshots.  Anything else (notably
# plain `dir` and thick LVM) is rejected pre-flight with a clear error.
_SNAPSHOT_CAPABLE_STORAGE = frozenset(
    {"zfspool", "lvmthin", "btrfs", "cephfs", "rbd", "nfs", "cifs"}
)


def _detect_snapshot_capable_storage(
    host: str, user: str, vmid: str
) -> tuple[bool, str]:
    """Return ``(supported, storage_type)`` for the rootfs of *vmid*.

    Pre-flight check for FR-005.  Reads ``pct config <vmid>`` for the
    rootfs storage name, then ``pvesm status`` for that storage's type.
    Returns ``(False, "")`` if either probe fails — caller should then
    bail with a clear error.
    """
    cfg = _ssh_run(host, user, f"pct config {shlex.quote(vmid)}")
    if cfg.returncode != 0:
        return False, ""

    storage_name = ""
    for line in cfg.stdout.splitlines():
        if line.startswith("rootfs:"):
            # Format:  rootfs: <storage>:<volume>,size=...
            rest = line[len("rootfs:"):].strip()
            storage_name, _, _ = rest.partition(":")
            break
    if not storage_name:
        return False, ""

    status = _ssh_run(host, user, "pvesm status")
    if status.returncode != 0:
        return False, ""

    for line in status.stdout.splitlines():
        parts = line.split()
        # `pvesm status` columns: Name Type Status Total Used Available %Used
        if parts and parts[0] == storage_name and len(parts) >= 2:
            storage_type = parts[1]
            return storage_type in _SNAPSHOT_CAPABLE_STORAGE, storage_type
    return False, ""


def _parse_pct_conf_snapshots(
    conf_text: str, container_name: str
) -> list[Snapshot]:
    """Parse ``/etc/pve/lxc/<vmid>.conf`` and return the snapshots inside.

    Snapshots appear as INI-style sections (``[<snap-name>]``) at the
    bottom of the conf file; the top-level keys before any section are
    the current container config.  Each section contains
    ``snaptime: <epoch>`` and may contain ``description: <text>``.
    """
    snapshots: list[Snapshot] = []
    current: dict[str, str] | None = None
    current_name: str | None = None

    def flush() -> None:
        if current_name is None or current is None:
            return
        created_at = datetime.fromtimestamp(
            int(current.get("snaptime", "0") or "0"), tz=timezone.utc
        )
        snapshots.append(
            Snapshot(
                provider="proxmox",
                instance_name=container_name,
                name=current_name,
                backend_id=current_name,
                created_at=created_at,
                size_bytes=None,  # Proxmox doesn't report per-snapshot bytes
                description=current.get("description", ""),
                status=SnapshotStatus.AVAILABLE,
            )
        )

    for raw in conf_text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^\[([^\]]+)\]\s*$", line)
        if m:
            flush()
            current_name = m.group(1)
            current = {}
            continue
        if current is None:
            # Top-level config; skip.
            continue
        if not line or line.startswith("#"):
            continue
        key, sep, val = line.partition(":")
        if sep:
            current[key.strip()] = val.strip()
    flush()

    return snapshots


def _list_snapshots_for_vmid(
    host: str, user: str, vmid: str, container_name: str
) -> list[Snapshot]:
    """Return the snapshots of LXC *vmid* on the Proxmox *host*.

    Reads ``/etc/pve/lxc/<vmid>.conf`` over SSH and parses the
    ``[<snap>]`` sections.  Raises :class:`RuntimeError` on SSH failure
    so the caller can surface it per FR-011.
    """
    cmd = f"cat /etc/pve/lxc/{shlex.quote(vmid)}.conf"
    result = _ssh_run(host, user, cmd)
    if result.returncode != 0:
        raise RuntimeError(
            f"reading /etc/pve/lxc/{vmid}.conf failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return _parse_pct_conf_snapshots(result.stdout, container_name)


def snapshot_create(
    container: str,
    host: str,
    user: str,
    vmid: str,
    snap_name: str,
    description: str = "",
) -> int:
    """Create a snapshot of LXC *vmid* on the Proxmox *host*.

    Pre-flight checks snapshot-capable storage (FR-005) and duplicate
    name (FR-006).  Returns 0 on success, 1 on any failure.
    """
    validate_snapshot_name(snap_name)

    supported, storage_type = _detect_snapshot_capable_storage(host, user, vmid)
    if not supported:
        if storage_type:
            print_error(
                f"Storage backend '{storage_type}' for container '{container}' "
                f"does not support snapshots. Supported backends: "
                f"{', '.join(sorted(_SNAPSHOT_CAPABLE_STORAGE))}."
            )
        else:
            print_error(
                f"Could not determine rootfs storage for container "
                f"'{container}' (vmid {vmid}); is it stopped or missing?"
            )
        return 1

    try:
        existing = _list_snapshots_for_vmid(host, user, vmid, container)
    except RuntimeError as e:
        print_error(str(e))
        return 1
    if any(s.name == snap_name for s in existing):
        print_error(
            f"Snapshot '{snap_name}' already exists for proxmox instance "
            f"'{container}'."
        )
        return 1

    cmd = (
        f"pct snapshot {shlex.quote(vmid)} {shlex.quote(snap_name)}"
    )
    if description:
        cmd += f" --description {shlex.quote(description)}"
    result = _ssh_run(host, user, cmd)
    if result.returncode != 0:
        print_error(
            f"pct snapshot failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return 1

    print_info(
        f"Created snapshot '{snap_name}' for proxmox instance '{container}'."
    )
    return 0


def _get_pct_status(host: str, user: str, vmid: str) -> str:
    """Return ``"running"`` / ``"stopped"`` or empty string on probe failure."""
    result = _ssh_run(host, user, f"pct status {shlex.quote(vmid)}")
    if result.returncode != 0:
        return ""
    # Output:  "status: running" or "status: stopped"
    parts = result.stdout.strip().split()
    if len(parts) >= 2 and parts[0] == "status:":
        return parts[1]
    return ""


def snapshot_restore(
    container: str,
    host: str,
    user: str,
    vmid: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Restore LXC *vmid* to *snap_name* via ``pct rollback``.

    ``pct rollback`` stops the container internally as part of the
    operation; we restart it afterwards if it was running pre-rollback
    (FR-013).  Returns 0 on success, 1 on any failure.
    """
    try:
        existing = _list_snapshots_for_vmid(host, user, vmid, container)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for proxmox instance '{container}'."
        )
        return 1

    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; "
            f"run `remo proxmox snapshot list {container}` to check status."
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

    pre_status = _get_pct_status(host, user, vmid)
    was_running = pre_status == "running"

    rollback = _ssh_run(
        host, user, f"pct rollback {shlex.quote(vmid)} {shlex.quote(snap_name)}"
    )
    if rollback.returncode != 0:
        print_error(
            f"pct rollback failed (rc={rollback.returncode}): "
            f"{rollback.stderr.strip() or rollback.stdout.strip()}"
        )
        return 1

    if was_running:
        start = _ssh_run(host, user, f"pct start {shlex.quote(vmid)}")
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
    vmid: str,
    snap_name: str,
    auto_confirm: bool = False,
) -> int:
    """Delete a snapshot of LXC *vmid*."""
    try:
        existing = _list_snapshots_for_vmid(host, user, vmid, container)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for proxmox instance '{container}'."
        )
        return 1
    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; cannot delete."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Delete snapshot '{snap_name}' of {container}?", default=False
        ):
            print_info("Aborted.")
            return 1

    result = _ssh_run(
        host, user,
        f"pct delsnapshot {shlex.quote(vmid)} {shlex.quote(snap_name)}",
    )
    if result.returncode != 0:
        print_error(
            f"pct delsnapshot failed (rc={result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
        return 1

    print_info(f"Deleted snapshot '{snap_name}' of {container}.")
    return 0
