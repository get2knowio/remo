"""Client for the `remo-host` protocol (contracts/remo-host-protocol.md).

Talks to the `remo-host` command installed on every instance over an
already-established SSH transport. This module owns argv construction for
`remo-host` verbs, JSON response parsing/validation (size cap, malformed
JSON, protocol version negotiation), and exit-code classification. It does
*not* build the SSH transport itself — callers (CLI, web `discovery.py`)
hand in an `ssh_argv_prefix` (e.g. ``["ssh", *opts, target]``) so this module
has no dependency on `core/ssh.py`'s `build_ssh_base_cmd` refactor.

`capabilities --json` parses into the canonical `models.capability.RemoteCapability`,
and `ZellijState`/`DevcontainerRunning` are the canonical enums from
`models.session_target` (both re-exported here for convenience) so their
values line up directly with `SessionTarget` fields for the later
`DiscoverySnapshot` assembly (T026). `ProjectEntry` — one raw item from
`sessions list --json`, before it is combined with instance identity into a
`SessionTarget` — has no existing model, so it stays local to this module.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from enum import Enum

from remo_cli.models.capability import RemoteCapability
from remo_cli.models.session_target import DevcontainerRunning, ZellijState

__all__ = [
    "DEFAULT_PAYLOAD_CAP",
    "DEFAULT_TIMEOUT",
    "SSH_TRANSPORT_EXIT_CODE",
    "SUPPORTED_PROTOCOL_RANGE",
    "DevcontainerRunning",
    "IncompatibleProtocolError",
    "MalformedResponseError",
    "PayloadTooLargeError",
    "REMOTE_PATH_PREFIX",
    "ProjectEntry",
    "RemoHostClientError",
    "RemoHostCommandError",
    "RemoHostExitReason",
    "RemoteCapability",
    "SshTransportError",
    "ZellijState",
    "build_remo_host_argv",
    "build_remo_host_shell_cmd",
    "get_capabilities",
    "list_sessions",
    "run_remo_host_json",
]

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

#: Client-supported major protocol range, `[min, max]` inclusive (R2).
SUPPORTED_PROTOCOL_RANGE: tuple[int, int] = (1, 1)

#: Default payload size cap in bytes (contract default: 256 KiB).
DEFAULT_PAYLOAD_CAP = 256 * 1024

#: ssh's own exit code for transport-layer failures (auth, DNS, refused, ...).
SSH_TRANSPORT_EXIT_CODE = 255

DEFAULT_TIMEOUT = 10.0

#: The `user_setup` Ansible role installs `remo-host` (and `project-launch`) to
#: ``~/.local/bin``, which is NOT on the PATH of a non-interactive
#: ``ssh <host> <command>`` shell (it doesn't source ``.bashrc``/``.profile``).
#: So every remote invocation is prefixed with this assignment, which the
#: remote shell evaluates before running the command — locating ``remo-host``
#: in ``~/.local/bin`` while still honoring any system-wide install on ``$PATH``
#: (this is also why the CLI's `project-launch` path uses an explicit
#: ``~/.local/bin/...`` path — see ``core.ssh.build_project_launch_remote_cmd``).
REMOTE_PATH_PREFIX = 'PATH="$HOME/.local/bin:$PATH"'


# ---------------------------------------------------------------------------
# Enums
#
# ZellijState / DevcontainerRunning are re-exported from
# models.session_target above (not redefined here) so a ProjectEntry's
# enum values are the exact same type SessionTarget expects.
# ---------------------------------------------------------------------------


class RemoHostExitReason(str, Enum):
    """Classification of a non-zero, non-255 `remo-host` exit code."""

    USAGE_ERROR = "usage_error"                  # exit 2
    INVALID_PROJECT = "invalid_project"           # exit 3
    UNSUPPORTED_SUBCOMMAND = "unsupported_subcommand"  # exit 4
    INTERNAL_ERROR = "internal_error"              # exit 5
    UNKNOWN = "unknown"                            # any other non-zero code


_EXIT_CODE_REASONS: dict[int, RemoHostExitReason] = {
    2: RemoHostExitReason.USAGE_ERROR,
    3: RemoHostExitReason.INVALID_PROJECT,
    4: RemoHostExitReason.UNSUPPORTED_SUBCOMMAND,
    5: RemoHostExitReason.INTERNAL_ERROR,
}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class RemoHostClientError(Exception):
    """Base class for all typed `remo-host` client errors."""


class IncompatibleProtocolError(RemoHostClientError):
    """Host's `protocol_version` is outside the client's supported range."""

    def __init__(
        self,
        reported_version: object,
        supported_range: tuple[int, int] = SUPPORTED_PROTOCOL_RANGE,
    ) -> None:
        self.reported_version = reported_version
        self.supported_range = supported_range
        lo, hi = supported_range
        super().__init__(
            f"remo-host reports protocol_version={reported_version!r}, "
            f"which is outside the supported range [{lo}, {hi}]. "
            "Update remo on this instance."
        )


class MalformedResponseError(RemoHostClientError):
    """stdout was not valid JSON, or lacked the required shape."""

    def __init__(self, reason: str, *, raw_excerpt: str = "") -> None:
        self.reason = reason
        self.raw_excerpt = raw_excerpt
        message = f"remo-host returned a malformed response: {reason}"
        if raw_excerpt:
            message += f" (excerpt: {raw_excerpt!r})"
        super().__init__(message)


class PayloadTooLargeError(RemoHostClientError):
    """stdout exceeded the configured payload size cap."""

    def __init__(self, size: int, cap: int) -> None:
        self.size = size
        self.cap = cap
        super().__init__(
            f"remo-host response of {size} bytes exceeds the {cap}-byte payload cap"
        )


class RemoHostCommandError(RemoHostClientError):
    """`remo-host` exited non-zero with a documented (non-SSH) exit code."""

    def __init__(self, returncode: int, stderr: str, *, verb: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.verb = verb
        self.reason = _EXIT_CODE_REASONS.get(returncode, RemoHostExitReason.UNKNOWN)
        detail = stderr.strip() or "<no stderr>"
        super().__init__(
            f"remo-host {verb} exited {returncode} ({self.reason.value}): {detail}"
        )


class SshTransportError(RemoHostClientError):
    """The SSH transport itself failed (exit 255, timeout, or spawn failure)."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        self.returncode = returncode
        super().__init__(message)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectEntry:
    """One project entry from `remo-host sessions list --json`."""

    name: str
    has_devcontainer: bool
    zellij_state: ZellijState
    devcontainer_running: DevcontainerRunning


# ---------------------------------------------------------------------------
# argv / shell-string construction
# ---------------------------------------------------------------------------


def build_remo_host_argv(
    verb: str,
    *,
    project: str | None = None,
    json: bool = True,
) -> list[str]:
    """Build the `remo-host` argv for *verb* as a clean list (no shell quoting).

    Supported verbs: ``"capabilities"``, ``"sessions list"``,
    ``"sessions attach"``. *project* is required for ``"sessions attach"``
    and ignored otherwise. *json* appends ``--json`` for the read-only
    verbs; it has no effect on ``"sessions attach"``, which is always
    interactive and never emits ``--json``.

    Returns a plain argv list — safe to pass straight to
    ``subprocess.run([...])`` (no ``shell=True``). Callers that need a
    single shell-quoted string for embedding in an ``ssh ... "remote cmd"``
    invocation should use :func:`build_remo_host_shell_cmd` instead.
    """
    argv = ["remo-host", *verb.split()]

    if verb == "sessions attach":
        if not project:
            raise ValueError("project is required for the 'sessions attach' verb")
        argv += ["--project", project]
        return argv

    if json:
        argv.append("--json")
    return argv


def build_remo_host_shell_cmd(
    verb: str,
    *,
    project: str | None = None,
    json: bool = True,
) -> str:
    """Like :func:`build_remo_host_argv` but returns one shell-quoted string.

    For embedding as the remote command in ``ssh <opts> <target> "<cmd>"``
    (e.g. ``ssh -tt ... "remo-host sessions attach --project <quoted>"``).
    Prefixed with :data:`REMOTE_PATH_PREFIX` so the remote shell can locate
    ``remo-host`` in ``~/.local/bin`` (not on a non-interactive shell's PATH).
    """
    return f"{REMOTE_PATH_PREFIX} {shlex.join(build_remo_host_argv(verb, project=project, json=json))}"


# ---------------------------------------------------------------------------
# Low-level subprocess runner
# ---------------------------------------------------------------------------


def _invoke(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Run *argv* via `subprocess.run`, mapping spawn failures to typed errors.

    Captures stdout/stderr as raw bytes (not decoded) so payload-size
    enforcement happens before any text decoding/parsing.
    """
    try:
        return subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise SshTransportError(f"remo-host invocation timed out after {timeout}s") from e
    except OSError as e:
        raise SshTransportError(f"failed to invoke ssh: {e}") from e


def _classify_exit(result: subprocess.CompletedProcess[bytes], *, verb: str) -> None:
    """Raise a typed error if *result* did not exit 0."""
    if result.returncode == 0:
        return
    if result.returncode == SSH_TRANSPORT_EXIT_CODE:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise SshTransportError(
            stderr or "ssh exited 255 (transport failure)",
            returncode=SSH_TRANSPORT_EXIT_CODE,
        )
    stderr = result.stderr.decode("utf-8", errors="replace")
    raise RemoHostCommandError(result.returncode, stderr, verb=verb)


def _decode_json_payload(
    stdout: bytes,
    *,
    payload_cap: int,
) -> dict:
    """Enforce the payload cap, then decode+parse *stdout* as a JSON object."""
    if len(stdout) > payload_cap:
        raise PayloadTooLargeError(len(stdout), payload_cap)

    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError as e:
        raise MalformedResponseError(f"stdout is not valid UTF-8: {e}") from e

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedResponseError(
            f"stdout is not valid JSON: {e}",
            raw_excerpt=text[:200],
        ) from e

    if not isinstance(payload, dict):
        raise MalformedResponseError(
            f"expected a JSON object, got {type(payload).__name__}",
            raw_excerpt=text[:200],
        )

    return payload


def _check_protocol_version(
    payload: dict,
    *,
    supported_range: tuple[int, int] = SUPPORTED_PROTOCOL_RANGE,
) -> int:
    """Validate `payload["protocol_version"]` against *supported_range*."""
    version = payload.get("protocol_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise MalformedResponseError(
            f"missing or non-integer protocol_version: {version!r}"
        )

    lo, hi = supported_range
    if not (lo <= version <= hi):
        raise IncompatibleProtocolError(version, supported_range)

    return version


def run_remo_host_json(
    ssh_argv_prefix: list[str],
    verb: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    payload_cap: int = DEFAULT_PAYLOAD_CAP,
    supported_range: tuple[int, int] = SUPPORTED_PROTOCOL_RANGE,
) -> dict:
    """Run a JSON `remo-host` verb over *ssh_argv_prefix* and return the payload dict.

    *ssh_argv_prefix* is the already-built SSH invocation, e.g.
    ``["ssh", *opts, "user@host"]`` — this function appends the
    ``remo-host`` argv itself and never touches SSH option construction.

    Raises :class:`SshTransportError`, :class:`RemoHostCommandError`,
    :class:`PayloadTooLargeError`, :class:`MalformedResponseError`, or
    :class:`IncompatibleProtocolError` on failure.
    """
    # REMOTE_PATH_PREFIX is a separate command word; ssh joins the post-target
    # words with spaces, so the remote shell evaluates it as a `PATH=... cmd`
    # assignment prefix that locates remo-host in ~/.local/bin.
    argv = [*ssh_argv_prefix, REMOTE_PATH_PREFIX, *build_remo_host_argv(verb, json=True)]
    result = _invoke(argv, timeout=timeout)
    _classify_exit(result, verb=verb)
    payload = _decode_json_payload(result.stdout, payload_cap=payload_cap)
    _check_protocol_version(payload, supported_range=supported_range)
    return payload


def get_capabilities(
    ssh_argv_prefix: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    payload_cap: int = DEFAULT_PAYLOAD_CAP,
    supported_range: tuple[int, int] = SUPPORTED_PROTOCOL_RANGE,
) -> RemoteCapability:
    """Run `remo-host capabilities --json` and return a typed result.

    Unknown extra top-level fields are ignored (additive-compatible, R2).
    """
    payload = run_remo_host_json(
        ssh_argv_prefix,
        "capabilities",
        timeout=timeout,
        payload_cap=payload_cap,
        supported_range=supported_range,
    )
    try:
        return RemoteCapability.from_dict(payload)
    except ValueError as e:
        raise MalformedResponseError(f"capabilities response invalid: {e}") from e


def list_sessions(
    ssh_argv_prefix: list[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    payload_cap: int = DEFAULT_PAYLOAD_CAP,
    supported_range: tuple[int, int] = SUPPORTED_PROTOCOL_RANGE,
) -> list[ProjectEntry]:
    """Run `remo-host sessions list --json` and return typed project entries.

    Individual project entries with an unrecognized `zellij_state` or
    `devcontainer_running` enum value are skipped (logged by the caller if
    desired) rather than failing the whole response — unknown *extra*
    fields on an entry are simply ignored. A structurally invalid
    `projects` list (missing, or not a list) is still a
    :class:`MalformedResponseError`.
    """
    payload = run_remo_host_json(
        ssh_argv_prefix,
        "sessions list",
        timeout=timeout,
        payload_cap=payload_cap,
        supported_range=supported_range,
    )

    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, list):
        raise MalformedResponseError(
            f"expected 'projects' to be a list, got {type(raw_projects).__name__}"
        )

    entries: list[ProjectEntry] = []
    for raw in raw_projects:
        if not isinstance(raw, dict):
            continue
        try:
            entries.append(
                ProjectEntry(
                    name=str(raw["name"]),
                    has_devcontainer=bool(raw.get("has_devcontainer", False)),
                    zellij_state=ZellijState(raw["zellij_state"]),
                    devcontainer_running=DevcontainerRunning(raw["devcontainer_running"]),
                )
            )
        except (KeyError, ValueError):
            # Unknown/invalid enum value or missing required field on this
            # entry only — skip it, the rest of the response is still usable.
            continue

    return entries
