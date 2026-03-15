"""Hetzner Cloud provider business logic for remo.

Manages the lifecycle of Hetzner Cloud VMs: create, destroy, and update
(re-configure dev tools).  All functions are pure business logic with no
Click imports; CLI argument handling lives in the ``cli`` layer.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_type,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import print_error, print_info, print_success, print_warning
from remo_cli.core.ssh import detect_timezone
from remo_cli.core.validation import build_tool_args, validate_name
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost


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
) -> int:
    """Create a new Hetzner Cloud VM and configure it with dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "server name")

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

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    rc = run_playbook("hetzner_site.yml", extra_vars, verbose=verbose)

    if rc != 0:
        return rc

    # Save to known_hosts on success.
    server_name = name or "remote-coding-server"
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

    return rc


def destroy(
    name: str = "",
    auto_confirm: bool = False,
    remove_volume: bool = False,
    verbose: bool = False,
) -> int:
    """Destroy a Hetzner Cloud VM.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "server name")

    server_name = name or "remote-coding-server"

    if remove_volume:
        print_warning(
            "WARNING: --remove-volume will destroy all data on the persistent volume!"
        )

    print_info(f"Destroying Hetzner VM '{server_name}'...")

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"hetzner_server_name={name}"])

    extra_vars.extend(["-e", f"auto_confirm={'true' if auto_confirm else 'false'}"])
    extra_vars.extend(["-e", f"remove_volume={'true' if remove_volume else 'false'}"])

    rc = run_playbook("hetzner_teardown.yml", extra_vars, verbose=verbose)

    # Remove from known_hosts.
    remove_known_host("hetzner", server_name)

    return rc


def update(
    name: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing Hetzner VM.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "server name")

    server_name = name or "remote-coding-server"

    # Get server address from known_hosts.
    server_host = _lookup_hetzner_host(server_name)
    if not server_host:
        print_error(f"Server '{server_name}' not found in known_hosts.")
        print("Run 'remo hetzner sync' or 'remo hetzner create' first.")
        sys.exit(1)

    print_info(f"Updating Hetzner VM '{server_name}' at {server_host}...")

    extra_vars: list[str] = [
        "-i", f"{server_host},",
        "-e", "ansible_user=remo",
    ]

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    return run_playbook(
        "hetzner_configure.yml",
        extra_vars,
        verbose=verbose,
    )


def list_hosts() -> None:
    """Print a formatted table of all registered Hetzner VMs."""
    entries = get_known_hosts(type_filter="hetzner")

    print(f"{'NAME':<25} {'HOST':<25} {'SSH COMMAND'}")
    print(f"{'----':<25} {'----':<25} {'-----------'}")

    for entry in entries:
        ssh_cmd = f"ssh {entry.user}@{entry.host}"
        print(f"{entry.name:<25} {entry.host:<25} {ssh_cmd}")

    if not entries:
        print("No Hetzner VMs registered.")
        print("Create one with: remo hetzner create")


def sync() -> None:
    """Discover Hetzner VMs with the ``remo`` label and update the registry.

    Requires the ``HETZNER_API_TOKEN`` environment variable.  Queries the
    Hetzner Cloud API for all servers carrying the ``remo`` label, clears
    existing hetzner entries from the known-hosts registry, and re-registers
    each discovered server.
    """
    token = os.environ.get("HETZNER_API_TOKEN", "")
    if not token:
        print_error("HETZNER_API_TOKEN environment variable is not set.")
        sys.exit(1)

    url = "https://api.hetzner.cloud/v1/servers?label_selector=remo"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print_error(f"Failed to query Hetzner API: {exc}")
        sys.exit(1)

    servers = data.get("servers", [])

    clear_known_hosts_by_type("hetzner")

    for server in servers:
        name = server.get("name", "")
        ip = (
            server.get("public_net", {})
            .get("ipv4", {})
            .get("ip", "")
        )
        if name and ip:
            save_known_host(
                KnownHost(
                    type="hetzner",
                    name=name,
                    host=ip,
                    user="remo",
                )
            )
            print_info(f"Registered: {name} ({ip})")

    count = len(servers)
    if count == 0:
        print_warning("No Hetzner VMs with 'remo' label found.")
    else:
        print_success(f"Synced {count} Hetzner VM(s).")
