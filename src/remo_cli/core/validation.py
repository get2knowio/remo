from __future__ import annotations

import re

import click

ALL_TOOLS: tuple[str, ...] = (
    "docker",
    "user_setup",
    "nodejs",
    "devcontainers",
    "github_cli",
    "fzf",
    "zellij",
)

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-[0-9]+$")


def validate_name(value: str, label: str = "name") -> None:
    if not value or not _NAME_RE.match(value) or len(value) > 63:
        raise click.BadParameter(
            f"Invalid {label}: '{value}'. Must start with alphanumeric and contain only"
            " alphanumeric, dots, hyphens, underscores, or slashes."
        )


def parse_volume_size(value: str) -> str:
    """Normalize a user-provided volume size to a pure integer string.

    Accepts a plain integer (``"100"``) or an integer with a common
    size suffix (``"100G"``, ``"100GB"``, ``"100GiB"`` — case-insensitive)
    and returns the integer portion as a string. Empty input passes
    through unchanged so callers can treat ``""`` as "not provided".

    Raises :class:`click.BadParameter` for anything else.
    """
    if not value:
        return value
    cleaned = value.strip()
    lowered = cleaned.lower()
    for suffix in ("gib", "gb", "g"):
        if lowered.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    try:
        as_int = int(cleaned)
    except ValueError as exc:
        raise click.BadParameter(
            f"Invalid volume size: '{value}'. Expected an integer,"
            " optionally with a G/GB/GiB suffix."
        ) from exc
    if as_int <= 0:
        raise click.BadParameter(
            f"Volume size must be a positive integer, got '{value}'."
        )
    return str(as_int)


def validate_port(value: int) -> None:
    if not isinstance(value, int) or value < 1 or value > 65535:
        raise click.BadParameter(
            f"Invalid port: '{value}'. Must be an integer between 1 and 65535."
        )


def validate_region(value: str) -> None:
    if not value or not _REGION_RE.match(value):
        raise click.BadParameter(
            f"Invalid region: '{value}'. Must match AWS region format (e.g. us-west-2)."
        )


def validate_project_name(name: str) -> None:
    """Validate a project name shared by the CLI and web attach paths.

    Mirrors, check-for-check and in the same order, the bash
    ``validate_project_name()`` in
    ``ansible/roles/user_setup/templates/remo-host.sh.j2`` (contracts/
    remo-host-protocol.md, FR-011) so both surfaces reject exactly the same
    inputs before ever constructing a remote command (US5 scenario 3):
    empty names, control characters, absolute paths, ``..`` traversal, and
    embedded path separators. Spaces, Unicode, punctuation, and leading
    dashes are all otherwise permitted.

    This is a syntactic pre-check only — it does NOT verify the project
    exists on the remote host (that requires filesystem access on the
    instance and stays the remote script's job via
    ``[[ -d "$PROJECTS_DIR/$name" ]]``).

    Raises
    ------
    ValueError
        With a human-readable reason describing the first violation found.
    """
    if not name:
        raise ValueError("project name must not be empty")

    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
        raise ValueError(f"project name contains control characters: {name!r}")

    if name.startswith("/"):
        raise ValueError(f"absolute paths are not allowed: {name}")

    if name == ".." or name.startswith("../") or name.endswith("/..") or "/../" in name:
        raise ValueError(f"path traversal is not allowed: {name}")

    if "/" in name:
        raise ValueError(f"path separators are not allowed: {name}")


def validate_tool_name(tool: str, flag: str = "--tools") -> None:
    if tool not in ALL_TOOLS:
        valid = ", ".join(ALL_TOOLS)
        raise click.BadParameter(
            f"Invalid tool name: '{tool}'. Valid tools are: {valid}.",
            param_hint=flag,
        )


def resolve_devcontainer_runtime(override: str | None) -> str:
    """Resolve and validate the devcontainer runtime.

    Precedence: explicit *override* (CLI flag) > REMO_DEVCONTAINER_RUNTIME env >
    built-in default. Unlike the --devcontainer-runtime flag (guarded by
    click.Choice), the env-var path is otherwise unchecked, so a mis-cased or
    bogus value would silently fall back to the Node runtime; validate it here.
    """
    # Imported lazily to keep core.config free of validation dependencies.
    from remo_cli.core.config import DEVCONTAINER_RUNTIMES, get_devcontainer_runtime

    runtime = override or get_devcontainer_runtime()
    if runtime not in DEVCONTAINER_RUNTIMES:
        valid = ", ".join(DEVCONTAINER_RUNTIMES)
        raise click.BadParameter(
            f"Invalid devcontainer runtime: '{runtime}'. Valid runtimes are: {valid}. "
            "Check the --devcontainer-runtime flag or REMO_DEVCONTAINER_RUNTIME.",
        )
    return runtime


def build_tool_args(only: tuple[str, ...], skip: tuple[str, ...]) -> list[str]:
    for tool in only:
        validate_tool_name(tool)
    for tool in skip:
        validate_tool_name(tool)

    args: list[str] = []

    if only:
        for tool in ALL_TOOLS:
            value = "true" if tool in only else "false"
            args.extend(["-e", f"configure_{tool}={value}"])
    elif skip:
        for tool in skip:
            args.extend(["-e", f"configure_{tool}=false"])

    return args
