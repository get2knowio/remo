"""SSH option building, terminal reset, timezone detection, and host resolution for remo."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import termios
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


def reset_terminal(saved_attrs: list | None = None) -> None:
    """Restore the terminal to a sane state after an SSH session.

    Sends escape sequences that disable mouse tracking, the alternate screen
    buffer, bracketed paste mode, application cursor keys, and then restores
    cursor visibility.  If ``saved_attrs`` is provided (from
    ``termios.tcgetattr`` before the session), restores the exact pre-session
    tty settings; otherwise falls back to ``stty sane``.
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
    if saved_attrs is not None:
        try:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, saved_attrs)
        except termios.error:
            pass
    else:
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


def check_remote_version(host: KnownHost) -> tuple[str | None, str | None]:
    """Read the remo version marker from the remote instance.

    Runs ``cat ~/.remo-version`` over SSH. Returns a ``(version, error)``
    tuple where exactly one element is non-None:

    * ``(version, None)`` — marker present and readable.
    * ``(None, None)``    — SSH succeeded but the marker is missing/empty.
    * ``(None, error)``   — SSH itself failed (DNS, host key, network,
      timeout, ...); *error* is the underlying message so the caller can
      surface it to the user.
    """
    ssh_opts, ssh_target = build_ssh_opts(host)
    cmd = ["ssh"] + ssh_opts + [
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        ssh_target,
        "cat ~/.remo-version 2>/dev/null",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None, "SSH timed out after 15s"
    except OSError as e:
        return None, f"SSH failed: {e}"

    # ssh exits with 255 for its own failures (auth, host key, DNS,
    # connection refused, ...). Any other non-zero exit code comes from
    # the remote command — here, `cat` failing because the marker is
    # absent — which we treat as "no marker" rather than an SSH error.
    if result.returncode == 255:
        stderr = result.stderr.strip()
        return None, stderr or "SSH connection failed (exit code 255)"

    version = result.stdout.strip()
    if result.returncode == 0 and version:
        return version, None

    return None, None


def build_project_launch_remote_cmd(
    project: str,
    detach: bool,
    exec_cmd: str | None,
) -> str:
    """Build the remote command string passed to ``ssh`` for project-launch.

    Returns a single shell-quoted string suitable for ``ssh <opts> <target>
    <cmd>``. ``exec_cmd`` is treated as one opaque shell command (the user
    typed it after ``--exec``) and forwarded as a single shell-quoted arg
    to ``--exec`` on the remote. The remote runs it via ``bash -lc`` so
    variable expansion, pipes, ``&&`` etc. all work as the user wrote them.
    """
    # Absolute path: SSH non-interactive commands don't source .bashrc, so
    # ~/.local/bin isn't in PATH. The remote login shell expands ~ for us.
    parts = ["~/.local/bin/project-launch", "--project", shlex.quote(project)]
    if detach:
        parts.append("--detach")
    if exec_cmd:
        parts.append("--exec")
        parts.append(shlex.quote(exec_cmd))
    return " ".join(parts)


def shell_connect(
    host: KnownHost,
    tunnels: list[str],
    no_open: bool,
    project: str | None = None,
    detach: bool = False,
    exec_cmd: str | None = None,
) -> None:
    """Open an SSH session to *host* with optional port tunnels.

    Parameters
    ----------
    host:
        The resolved host to connect to.
    tunnels:
        Port forwarding specifications.  Each entry is either ``"PORT"``
        (same local and remote port) or ``"LOCAL:REMOTE"``.
    no_open:
        When ``True``, skip auto-opening the browser for the first tunnel.
    project:
        When set, skip the server-side picker and hand off to the
        ``project-launch`` helper for this project name.
    detach:
        When ``True``, ask ``project-launch`` to run *exec_cmd* detached and
        return immediately. Requires *exec_cmd* to be non-empty.
    exec_cmd:
        Single-string command to run via ``project-launch -- ...`` inside the
        project's devcontainer (or in the host project dir if no
        ``.devcontainer``).
    """
    use_project_launch = bool(project)

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

    if use_project_launch:
        # -t forces TTY allocation so the interactive zellij+devcontainer flow
        # inside project-launch behaves correctly. The detach branch also
        # benefits — devcontainer up prints progress that should reach the
        # user's terminal even though the command exits immediately after.
        ssh_cmd.append("-t")

    ssh_cmd.append(ssh_target)

    if use_project_launch:
        assert project is not None  # narrowed by use_project_launch
        ssh_cmd.append(build_project_launch_remote_cmd(project, detach, exec_cmd))

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
    saved_attrs = None
    try:
        saved_attrs = termios.tcgetattr(sys.stdin.fileno())
    except termios.error:
        pass

    try:
        subprocess.run(ssh_cmd)
    except KeyboardInterrupt:
        pass
    finally:
        reset_terminal(saved_attrs)
