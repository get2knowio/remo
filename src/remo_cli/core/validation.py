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


def validate_tool_name(tool: str, flag: str = "--tools") -> None:
    if tool not in ALL_TOOLS:
        valid = ", ".join(ALL_TOOLS)
        raise click.BadParameter(
            f"Invalid tool name: '{tool}'. Valid tools are: {valid}.",
            param_hint=flag,
        )


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
