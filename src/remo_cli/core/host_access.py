"""How remo reaches a host's scripts: over SSH, or on this very machine (029).

One small seam, two implementations chosen by host type. Everything that used
to build an ``ssh`` argv inline (``shell_connect``, ``check_remote_version``,
``resume``'s lookup prefix) asks :func:`get_host_access` instead. The SSH
implementation delegates to the existing builders with the existing arguments,
so every SSH argv is byte-identical to pre-029 (pinned by
``tests/unit/core/test_host_access_ssh_baseline.py``); the local one runs the
same host-side commands with the bash >= 4 that ``remo local setup`` detected.

No Click, no provider names: ``local`` is a pseudo-type like ``ssh``
(``core/config.LOCAL_HOST_TYPE``). Contract: specs/029-local-host/contracts/
host-access.md.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from remo_cli.core.config import LOCAL_HOST_TYPE
from remo_cli.core.errors import PreconditionError
from remo_cli.models.host import KnownHost

SETUP_HINT = (
    "remo local setup has not completed on this machine — run: remo local setup"
)
TUNNEL_REFUSAL = (
    "port tunnels do not apply to the local host: its ports are already on this machine"
)
MENU_COMMAND = 'exec "$HOME/.local/bin/project-menu"'
#: Spec 030: the explicit menu start for a bare `remo shell` over SSH, sent as the
#: remote command (with a pty) so no ``.bashrc`` hook has to fire. Menu exit 0
#: ("Exit to shell") yields a login shell; REMO_MENU=1 keeps the guarded hook
#: from starting a second menu in it. Any other exit ends the connection.
#: The user's login shell parses this, so the ``${SHELL:-…}`` fallback is left
#: to ``/bin/sh`` inside single quotes: fish has no ``${VAR:-default}`` form.
SSH_MENU_COMMAND = (
    'REMO_MENU=1 "$HOME/.local/bin/project-menu" && '
    "exec env REMO_MENU=1 /bin/sh -c 'exec \"${SHELL:-/bin/sh}\" -l'"
)
#: Joins the words after the script name with single spaces and runs them as
#: one shell command — exactly what ssh does with its trailing argv words, so
#: ``remo_host_client``'s ``REMOTE_PATH_PREFIX`` word keeps working unchanged.
EVAL_COMMAND = 'eval "$*"'


def local_state_dir() -> Path:
    return Path.home() / ".local" / "state" / "remo"


def local_env_path() -> Path:
    return local_state_dir() / "local.env"


def local_bin_dir() -> Path:
    return Path.home() / ".local" / "bin"


class HostAccess(Protocol):
    remote: bool

    def interactive_argv(
        self,
        host: KnownHost,
        remote_cmd: str | None,
        *,
        tty: bool,
        extra_opts: Sequence[str] = (),
    ) -> list[str]: ...

    def command_prefix(self, host: KnownHost, *, batch: bool) -> list[str]: ...

    def probe_argv(self, host: KnownHost, command: str) -> list[str]: ...

    def env(self, host: KnownHost, *, tab_key: str | None) -> dict[str, str] | None: ...

    def supports_tunnels(self) -> bool: ...


class SshAccess:
    """Today's behaviour, unchanged."""

    remote = True

    def interactive_argv(
        self,
        host: KnownHost,
        remote_cmd: str | None,
        *,
        tty: bool,
        extra_opts: Sequence[str] = (),
    ) -> list[str]:
        from remo_cli.core.ssh import build_ssh_base_cmd

        cmd = build_ssh_base_cmd(
            host,
            tty=tty,
            multiplex=True,
            control_dir=None,
            extra_opts=list(extra_opts) or None,
        )
        if remote_cmd is not None:
            cmd.append(remote_cmd)
        return cmd

    def command_prefix(self, host: KnownHost, *, batch: bool) -> list[str]:
        from remo_cli.core.ssh import build_ssh_base_cmd

        # Non-interactive (BatchMode, mirroring core/attach.py) and multiplexed,
        # so the interactive connection that follows reuses the master this opens.
        base = build_ssh_base_cmd(host, multiplex=True)
        if not batch:
            return base
        return [base[0], "-o", "BatchMode=yes", *base[1:]]

    def probe_argv(self, host: KnownHost, command: str) -> list[str]:
        from remo_cli.core.ssh import build_ssh_opts

        ssh_opts, ssh_target = build_ssh_opts(host)
        return [
            "ssh", *ssh_opts,
            "-o", "ConnectTimeout=10",
            "-o", "BatchMode=yes",
            ssh_target,
            command,
        ]

    def env(self, host: KnownHost, *, tab_key: str | None) -> dict[str, str] | None:
        if tab_key is None:
            return None  # inherit; argv stays exactly as before 028
        return {**os.environ, "REMO_TAB_KEY": tab_key}

    def supports_tunnels(self) -> bool:
        return True


class LocalAccess:
    """Run the host-side commands on this machine, on the user's own TTY."""

    remote = False

    def _bash(self) -> str:
        env_file = local_env_path()
        menu = local_bin_dir() / "project-menu"
        if not env_file.is_file() or not menu.is_file():
            raise PreconditionError(SETUP_HINT)
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "REMO_BASH" and value.strip():
                return value.strip().strip("'\"")
        raise PreconditionError(SETUP_HINT)

    def interactive_argv(
        self,
        host: KnownHost,
        remote_cmd: str | None,
        *,
        tty: bool,
        extra_opts: Sequence[str] = (),
    ) -> list[str]:
        return [self._bash(), "-c", remote_cmd if remote_cmd is not None else MENU_COMMAND]

    def command_prefix(self, host: KnownHost, *, batch: bool) -> list[str]:
        return [self._bash(), "-c", EVAL_COMMAND, "remo-local"]

    def probe_argv(self, host: KnownHost, command: str) -> list[str]:
        return [self._bash(), "-c", command]

    def env(self, host: KnownHost, *, tab_key: str | None) -> dict[str, str]:
        env = dict(os.environ)
        env["PATH"] = f"{local_bin_dir()}{os.pathsep}{env.get('PATH', '')}"
        if tab_key is not None:
            env["REMO_TAB_KEY"] = tab_key
        return env

    def supports_tunnels(self) -> bool:
        return False


_SSH = SshAccess()
_LOCAL = LocalAccess()


def get_host_access(host: KnownHost) -> HostAccess:
    return _LOCAL if host.type == LOCAL_HOST_TYPE else _SSH
