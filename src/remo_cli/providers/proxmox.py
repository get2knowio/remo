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
import subprocess
import sys

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_prefix,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import confirm, print_error, print_info, print_warning
from remo_cli.core.ssh import detect_timezone
from remo_cli.core.validation import build_tool_args, validate_name
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost


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
    verbose: bool = False,
) -> int:
    """Create a new Proxmox LXC container and configure dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")

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

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    # Clear any stale registry entry so _resolve_container_ip queries the
    # Proxmox host for the fresh IP instead of returning a cached value.
    remove_known_host("proxmox", f"{host}/{name}")

    rc = run_playbook("proxmox_site.yml", extra_vars, verbose=verbose)

    if rc == 0:
        vmid = _resolve_vmid(name, host, user)
        ip = _resolve_container_ip(name, host, user, vmid=vmid) or name
        save_known_host(
            KnownHost(
                type="proxmox",
                name=f"{host}/{name}",
                host=ip,
                user="remo",
                instance_id=vmid,
                access_mode="direct",
                region=user or "root",
            )
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
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing Proxmox LXC container.

    When any of *volume_size*, *cores*, or *memory* is provided, apply
    those resource changes (via pct resize / pct set) before running the
    dev-tools configure playbook.

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

    if not user:
        user = "root"

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


def sync(host: str, user: str = "") -> None:
    """Discover Proxmox LXC containers on *host* and register them.

    Runs ``pct list`` over SSH (or locally if host == "localhost"),
    parses the output, then queries each container for its VMID and IP.
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

    clear_known_hosts_by_prefix("proxmox", f"{host}/")

    for vmid, hostname in containers:
        ip = _resolve_container_ip(hostname, host, user, vmid=vmid) or hostname
        save_known_host(
            KnownHost(
                type="proxmox",
                name=f"{host}/{hostname}",
                host=ip,
                user="remo",
                instance_id=vmid,
                access_mode="direct",
                region=user or "root",
            )
        )

    print_info(f"Synced {len(containers)} container(s) from '{host}'.")


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
