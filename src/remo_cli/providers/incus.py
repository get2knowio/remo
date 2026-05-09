"""Incus container provider business logic for remo.

Manages the lifecycle of Incus containers: create, destroy, and update
(re-configure dev tools).  All functions are pure business logic with no
Click imports; CLI argument handling lives in the ``cli`` layer.
"""

from __future__ import annotations

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
    verbose: bool = False,
) -> int:
    """Create a new Incus container and configure it with dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    validate_name(name, "container name")

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

    rc = run_playbook("incus_site.yml", extra_vars, verbose=verbose)

    if rc == 0:
        container_host = _resolve_container_ip(name, host, user) or name
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

    if not auto_confirm:
        location = f" on {host}" if host and host != "localhost" else ""
        prompt = f"Destroy Incus container '{name}'{location}? This cannot be undone."
        if not confirm(prompt):
            print_info("Aborted.")
            return 0

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


def sync(host: str = "localhost", user: str = "") -> None:
    """Discover Incus containers on *host* and register them in known-hosts.

    For localhost, runs ``incus list -f csv -c n`` directly.  For remote hosts,
    the same command is executed over SSH.  All previously registered entries
    for the given host prefix are cleared before the newly discovered
    containers are saved.
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
        save_known_host(
            KnownHost(
                type="incus",
                name=f"{host}/{name}",
                host=name,
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
