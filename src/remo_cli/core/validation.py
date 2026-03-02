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
