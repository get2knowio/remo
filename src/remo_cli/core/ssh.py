"""SSH option building, terminal reset, timezone detection, and host resolution for remo."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import termios
from pathlib import Path

from remo_cli.core.config import DEFAULT_SSH_PORT
from remo_cli.core.known_hosts import get_aws_region, get_known_hosts, resolve_remo_host_by_name
from remo_cli.core.output import print_info
from remo_cli.core.picker import pick_environment
from remo_cli.core.validation import validate_port, validate_project_name
from remo_cli.models.host import KnownHost


def resolve_ssh_control_dir(control_dir: str | None = None) -> str:
    """Resolve the directory that holds SSH ControlMaster sockets.

    Resolution order:

    1. *control_dir* argument, when given explicitly by the caller.
    2. The ``$REMO_SSH_CONTROL_DIR`` environment variable, when set.
    3. ``~/.ssh`` (today's hard-coded default), otherwise.

    This lets CLI call sites keep working with zero changes (they never pass
    *control_dir* and, ordinarily, never set the env var either) while giving
    the web service a single hook — set ``$REMO_SSH_CONTROL_DIR`` once — to
    point ControlPath sockets at a writable tmpfs (e.g. ``/run/remo-ssh``)
    instead of a read-only-mounted ``~/.ssh``.
    """
    if control_dir:
        return control_dir
    env_dir = os.environ.get("REMO_SSH_CONTROL_DIR")
    if env_dir:
        return env_dir
    return "~/.ssh"


def build_ssh_opts(
    host: KnownHost,
    multiplex: bool = False,
    control_dir: str | None = None,
    identity_file: str | None = None,
    known_hosts_file: str | None = None,
) -> tuple[list[str], str]:
    """Build SSH option flags and target string for the given host.

    Parameters
    ----------
    host:
        The registered host to connect to.
    multiplex:
        When ``True``, add ControlMaster/ControlPath/ControlPersist options so
        that subsequent SSH connections to the same host reuse the existing
        master socket.
    control_dir:
        Directory for the ControlMaster socket (the ``ControlPath`` becomes
        ``f"{control_dir}/remo-%r@%h-%p"``). When ``None`` (the default for
        every existing CLI call site), falls back to ``$REMO_SSH_CONTROL_DIR``
        and finally to ``~/.ssh`` — see :func:`resolve_ssh_control_dir`.
    identity_file:
        When set, emit ``-o IdentityFile=<path>`` plus ``-o
        IdentitiesOnly=yes`` so SSH uses exactly this key and never falls back
        to ambient ``~/.ssh`` identities or agent keys (adopted-mode web
        service identity, R6). ``None`` (the default) emits nothing, leaving
        the argv byte-identical to before this parameter existed.
    known_hosts_file:
        When set, emit ``-o UserKnownHostsFile=<path>``. ``None`` (the
        default) emits nothing. For SSM hosts the access-mode block's
        ``UserKnownHostsFile=/dev/null`` comes first in the argv and — since
        SSH honors the first value obtained per option — keeps winning, which
        preserves SSM's deliberate no-host-key-checking behavior.

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
        resolved_control_dir = resolve_ssh_control_dir(control_dir)
        ssh_opts += [
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={resolved_control_dir}/remo-%r@%h-%p",
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
        # Manually-added SSH host (feature 014, type=ssh): apply a non-default
        # port stored in KnownHost.instance_id. Gated on the ssh type so a
        # provider's numeric instance_id (e.g. a Proxmox vmid) is never read
        # as a port and every other provider's argv is unchanged.
        if host.type == "ssh" and host.ssh_port != DEFAULT_SSH_PORT:
            ssh_opts += ["-o", f"Port={host.ssh_port}"]

    # ------------------------------------------------------------------
    # Explicit identity / known-hosts (adopted-mode web service R6; added-host
    # stored identity, feature 014). An explicit identity_file argument always
    # wins; otherwise an added (ssh-type) host's stored identity is used
    # (``ssh_identity`` is None for every other type, so their argv is unchanged).
    # ------------------------------------------------------------------
    effective_identity = identity_file if identity_file is not None else host.ssh_identity
    if effective_identity is not None:
        ssh_opts += [
            "-o", f"IdentityFile={effective_identity}",
            "-o", "IdentitiesOnly=yes",
        ]
    if known_hosts_file is not None:
        ssh_opts += ["-o", f"UserKnownHostsFile={known_hosts_file}"]

    # ------------------------------------------------------------------
    # Timezone forwarding
    # ------------------------------------------------------------------
    tz = detect_timezone()
    if tz:
        os.environ["TZ"] = tz
        ssh_opts += ["-o", "SendEnv=TZ"]

    return ssh_opts, ssh_target


def build_ssh_base_cmd(
    host: KnownHost,
    *,
    tty: bool = False,
    multiplex: bool = False,
    control_dir: str | None = None,
    identity_file: str | None = None,
    known_hosts_file: str | None = None,
    extra_opts: list[str] | None = None,
) -> list[str]:
    """Build the full ``ssh`` argv for connecting to *host*.

    This is the single shared builder for both the CLI (:func:`shell_connect`)
    and the web terminal service: it delegates all option construction (SSM
    ``ProxyCommand``, direct-target selection, timezone ``SendEnv``,
    ControlMaster/ControlPath) to :func:`build_ssh_opts` so that logic is
    never duplicated, then assembles the final argv as a plain list — never a
    shell string — so hostnames/usernames/proxy commands can't be
    reinterpreted by a shell.

    Parameters
    ----------
    host:
        The registered host to connect to.
    tty:
        When ``True``, force remote TTY allocation with ``-tt`` (used for
        attaching to an interactive remote session, e.g. Zellij). When
        ``False`` (the default), no TTY flag is added, matching a plain
        ``ssh <opts> <target>`` invocation.
    multiplex:
        Forwarded to :func:`build_ssh_opts`.
    control_dir:
        Forwarded to :func:`build_ssh_opts`.
    identity_file:
        Forwarded to :func:`build_ssh_opts` — emits ``-o IdentityFile=<path>``
        and ``-o IdentitiesOnly=yes`` when set; ``None`` (the default) leaves
        the argv unchanged.
    known_hosts_file:
        Forwarded to :func:`build_ssh_opts` — emits ``-o
        UserKnownHostsFile=<path>`` when set; ``None`` (the default) leaves
        the argv unchanged.
    extra_opts:
        Extra argv elements inserted after *ssh_opts* but before the
        ``-tt``/target elements — e.g. ``["-L", "8080:localhost:8080"]``
        port-tunnel flags for :func:`shell_connect`. ``None``/empty (the
        default for both existing call sites) leaves today's argv shape
        unchanged.

    Returns
    -------
    list[str]
        ``["ssh", *ssh_opts, *extra_opts, "-tt"?, ssh_target]`` ready to pass
        to ``subprocess``/``asyncio.create_subprocess_exec`` without
        ``shell=True``.
    """
    ssh_opts, ssh_target = build_ssh_opts(
        host,
        multiplex=multiplex,
        control_dir=control_dir,
        identity_file=identity_file,
        known_hosts_file=known_hosts_file,
    )

    cmd: list[str] = ["ssh"] + ssh_opts
    if extra_opts:
        cmd += extra_opts
    if tty:
        cmd.append("-tt")
    cmd.append(ssh_target)
    return cmd


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

    *project* is checked against :func:`~remo_cli.core.validation.
    validate_project_name` (T059) BEFORE ``shlex.quote()`` — an additional,
    client-side safety check on top of (not a replacement for) shell
    quoting: today's ``project-launch`` direct-invocation CLI path has no
    upfront validation at all, relying solely on the remote script's own
    ``[[ ! -d "$PROJECT_DIR" ]]`` existence check. This runs the SAME
    validator the web attach path runs (:func:`~remo_cli.web.terminal.
    build_attach_argv`), so both surfaces identify/reject a given project
    name identically (US5 scenario 3).
    """
    try:
        validate_project_name(project)
    except ValueError as e:
        raise SystemExit(f"Error: invalid project name: {e}")

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

    print_info(f"Connecting to {host.type}: {host.name} ({host.host})...")

    # ------------------------------------------------------------------
    # Parse and validate tunnel specifications
    # ------------------------------------------------------------------
    parsed_tunnels: list[tuple[int, int]] = []
    tunnel_opts: list[str] = []
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

        tunnel_opts += ["-L", f"{local_port}:localhost:{remote_port}"]
        print_info(f"Tunnel: localhost:{local_port} -> remote :{remote_port}")
        parsed_tunnels.append((local_port, remote_port))

    # ------------------------------------------------------------------
    # Build the SSH argv via the shared builder (T058): the same
    # build_ssh_opts()-backed option construction the web terminal service
    # uses (build_attach_argv), so both paths share one SSH-argv-construction
    # layer. `-L` tunnel flags (extra_opts) land before any `-tt`/target,
    # matching today's flag ordering; `-tt` is added only for the
    # *interactive* project-launch flow, so zellij+devcontainer reach the
    # user's terminal.
    #
    # Detaching must NOT allocate a pty. sshd kills the pty session's
    # process group when the channel closes, and project-launch's detach
    # branch does `nohup setsid ... &` then `exit 0` -- returning
    # immediately, by design. The child is killed mid-exec before setsid(2)
    # can move it to a new session, so its command never runs and the log
    # gets its header and nothing else. nohup's ignored SIGHUP does not save
    # it; the process group is torn down regardless. Without a pty there is
    # no such teardown and the child survives (verified over real ssh, with
    # and without -tt). The detach branch needs no tty of its own: it sends
    # `devcontainer up` output to /dev/null, and its only terminal output is
    # a plain echo that reaches the client over ordinary stdout.
    #
    # `control_dir=None` preserves the CLI's default `~/.ssh` ControlPath.
    # ------------------------------------------------------------------
    ssh_cmd = build_ssh_base_cmd(
        host,
        tty=use_project_launch and not detach,
        multiplex=True,
        control_dir=None,
        extra_opts=tunnel_opts or None,
    )

    if use_project_launch:
        assert project is not None  # narrowed by use_project_launch
        # build_project_launch_remote_cmd() runs validate_project_name()
        # (T059) before shlex.quote(), so a malicious/malformed name is
        # rejected here with a clear SystemExit rather than reaching ssh.
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
