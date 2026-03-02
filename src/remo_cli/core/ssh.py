"""SSH option building, terminal reset, timezone detection, and host resolution for remo."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from remo_cli.core.known_hosts import get_aws_region, get_known_hosts, resolve_remo_host_by_name
from remo_cli.core.output import print_info
from remo_cli.core.picker import pick_environment
from remo_cli.core.validation import validate_port
from remo_cli.models.host import KnownHost


def build_ssh_opts(host: KnownHost, multiplex: bool = False) -> tuple[list[str], str]:
    """Build SSH option flags and target string for the given host.

    Parameters
    ----------
    host:
        The registered host to connect to.
    multiplex:
        When ``True``, add ControlMaster/ControlPath/ControlPersist options so
        that subsequent SSH connections to the same host reuse the existing
        master socket.

    Returns
    -------
    tuple[list[str], str]
        A two-element tuple of ``(ssh_opts, ssh_target)`` where *ssh_opts* is a
        flat list of option strings (each ``-o`` flag is a separate element
        followed by its value) and *ssh_target* is the ``user@host`` string to
        pass to SSH.
    """
    ssh_opts: list[str] = []

    # ------------------------------------------------------------------
    # Multiplexing
    # ------------------------------------------------------------------
    if multiplex:
        ssh_opts += [
            "-o", "ControlMaster=auto",
            "-o", "ControlPath=~/.ssh/remo-%r@%h-%p",
            "-o", "ControlPersist=60s",
        ]

    # ------------------------------------------------------------------
    # Access-mode-specific options and target
    # ------------------------------------------------------------------
    if host.access_mode == "ssm":
        ssh_opts += [
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
        ]

        region = get_aws_region(host.name)
        proxy_cmd = (
            f"aws ssm start-session"
            f" --region {region}"
            f" --target %h"
            f" --document-name AWS-StartSSHSession"
            f" --parameters 'portNumber=%p'"
        )

        aws_profile = os.environ.get("AWS_PROFILE", "")
        if aws_profile:
            proxy_cmd = (
                f"env AWS_ACCESS_KEY_ID= AWS_SECRET_ACCESS_KEY="
                f" AWS_PROFILE={aws_profile} {proxy_cmd}"
            )

        ssh_opts += ["-o", f"ProxyCommand={proxy_cmd}"]
        ssh_target = f"{host.user}@{host.instance_id}"
    else:
        ssh_target = f"{host.user}@{host.host}"

    # ------------------------------------------------------------------
    # Timezone forwarding
    # ------------------------------------------------------------------
    tz = detect_timezone()
    if tz:
        os.environ["TZ"] = tz
        ssh_opts += ["-o", "SendEnv=TZ"]

    return ssh_opts, ssh_target


def resolve_remo_host(name: str | None = None) -> KnownHost:
    """Resolve which registered host to connect to.

    Parameters
    ----------
    name:
        When given, look up the host by name via
        :func:`~remo.core.known_hosts.resolve_remo_host_by_name`.
        When ``None``, automatically selects the sole registered host or
        presents an interactive picker if there are multiple.

    Returns
    -------
    KnownHost
        The resolved host entry.

    Raises
    ------
    SystemExit
        If no hosts are registered.
    """
    if name is not None:
        return resolve_remo_host_by_name(name)

    hosts = get_known_hosts()

    if not hosts:
        raise SystemExit(
            "Error: No remo environments registered.\n"
            "\n"
            "Create one with:\n"
            "  remo aws create\n"
            "  remo hetzner create\n"
            "  remo incus create <name>"
        )

    if len(hosts) == 1:
        return hosts[0]

    return pick_environment(hosts)


def require_session_manager_plugin() -> None:
    """Ensure session-manager-plugin is available on PATH.

    Raises
    ------
    SystemExit
        If the plugin binary cannot be found.
    """
    if shutil.which("session-manager-plugin") is None:
        raise SystemExit(
            "Error: session-manager-plugin is not installed.\n"
            "\n"
            "Install it from:\n"
            "  https://docs.aws.amazon.com/systems-manager/latest/userguide/"
            "session-manager-working-with-install-plugin.html\n"
            "\n"
            "On macOS:  brew install --cask session-manager-plugin\n"
            "On Ubuntu: see AWS docs for .deb package"
        )


def reset_terminal() -> None:
    """Restore the terminal to a sane state after an SSH session.

    Sends escape sequences that disable mouse tracking, the alternate screen
    buffer, bracketed paste mode, application cursor keys, and then restores
    cursor visibility.  Follows up with ``stty sane`` for good measure.
    """
    sys.stdout.write(
        "\033[?1000l"   # disable X10 mouse tracking
        "\033[?1002l"   # disable button-event mouse tracking
        "\033[?1003l"   # disable any-event mouse tracking
        "\033[?1006l"   # disable SGR mouse mode
        "\033[?1049l"   # exit alternate screen buffer
        "\033[?2004l"   # disable bracketed paste
        "\033[?1l"      # reset application cursor keys
        "\033[?25h"     # show cursor
    )
    sys.stdout.flush()
    subprocess.run(["stty", "sane"], stderr=subprocess.DEVNULL)


def detect_timezone() -> str:
    """Detect the local timezone in IANA format (e.g. ``"America/New_York"``).

    Detection order mirrors the remo bash implementation:

    1. ``TZ`` environment variable (accepted when non-empty, not ``"UTC"``,
       and contains a ``/`` so it looks like an IANA name).
    2. ``timedatectl show -p Timezone --value`` (systemd systems).
    3. ``/etc/timezone`` plain-text file (Debian/Ubuntu).
    4. ``/etc/localtime`` symlink target (macOS and other Linux distros).
    5. ``systemsetup -gettimezone`` (macOS).

    Returns an empty string when the timezone cannot be determined.
    """
    # 1. TZ environment variable
    tz_env = os.environ.get("TZ", "")
    if tz_env and tz_env != "UTC" and "/" in tz_env:
        return tz_env

    # 2. timedatectl (systemd)
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            capture_output=True,
            text=True,
        )
        tz = result.stdout.strip()
        if tz and "/" in tz:
            return tz
    except FileNotFoundError:
        pass

    # 3. /etc/timezone (Debian/Ubuntu)
    etc_timezone = Path("/etc/timezone")
    if etc_timezone.is_file():
        tz = etc_timezone.read_text().strip()
        if tz and "/" in tz:
            return tz

    # 4. /etc/localtime symlink target
    etc_localtime = Path("/etc/localtime")
    if etc_localtime.is_symlink():
        try:
            target = etc_localtime.resolve()
            # Extract the IANA name from the resolved path, e.g.
            # /usr/share/zoneinfo/America/New_York -> America/New_York
            parts = target.parts
            try:
                zi_index = parts.index("zoneinfo")
                tz = "/".join(parts[zi_index + 1 :])
                if tz and "/" in tz:
                    return tz
            except ValueError:
                pass
        except OSError:
            pass

    # 5. systemsetup (macOS)
    try:
        result = subprocess.run(
            ["systemsetup", "-gettimezone"],
            capture_output=True,
            text=True,
        )
        # Output looks like: "Time Zone: America/New_York"
        output = result.stdout.strip()
        if ":" in output:
            tz = output.split(":", 1)[1].strip()
            if tz and "/" in tz:
                return tz
    except FileNotFoundError:
        pass

    return ""


def shell_connect(host: KnownHost, tunnels: list[str], no_open: bool) -> None:
    """Open an interactive SSH session to *host* with optional port tunnels.

    Parameters
    ----------
    host:
        The resolved host to connect to.
    tunnels:
        Port forwarding specifications.  Each entry is either ``"PORT"``
        (same local and remote port) or ``"LOCAL:REMOTE"``.
    no_open:
        When ``True``, skip auto-opening the browser for the first tunnel.
    """
    ssh_opts, ssh_target = build_ssh_opts(host, multiplex=True)

    print_info(f"Connecting to {host.type}: {host.name} ({host.host})...")

    ssh_cmd: list[str] = ["ssh"] + ssh_opts

    # ------------------------------------------------------------------
    # Parse and validate tunnel specifications
    # ------------------------------------------------------------------
    parsed_tunnels: list[tuple[int, int]] = []
    for spec in tunnels:
        if ":" in spec:
            parts = spec.split(":", 1)
            try:
                local_port = int(parts[0])
                remote_port = int(parts[1])
            except ValueError:
                raise SystemExit(f"Error: Invalid tunnel specification: '{spec}'")
        else:
            try:
                local_port = int(spec)
                remote_port = local_port
            except ValueError:
                raise SystemExit(f"Error: Invalid tunnel specification: '{spec}'")

        validate_port(local_port)
        validate_port(remote_port)

        # Check if local port is already in use
        if shutil.which("ss"):
            result = subprocess.run(
                ["ss", "-tlnH", f"sport = :{local_port}"],
                capture_output=True,
                text=True,
            )
            if result.stdout.strip():
                raise SystemExit(f"Error: Local port {local_port} is already in use.")

        ssh_cmd += ["-L", f"{local_port}:localhost:{remote_port}"]
        print_info(f"Tunnel: localhost:{local_port} -> remote :{remote_port}")
        parsed_tunnels.append((local_port, remote_port))

    ssh_cmd.append(ssh_target)

    # ------------------------------------------------------------------
    # Auto-open browser for first tunnel
    # ------------------------------------------------------------------
    if parsed_tunnels and not no_open:
        first_local = parsed_tunnels[0][0]
        opener = shutil.which("xdg-open") or shutil.which("open")
        if opener:
            subprocess.Popen(
                [opener, f"http://localhost:{first_local}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    # ------------------------------------------------------------------
    # Execute SSH session with terminal reset on exit
    # ------------------------------------------------------------------
    try:
        subprocess.run(ssh_cmd)
    except KeyboardInterrupt:
        pass
    finally:
        reset_terminal()
