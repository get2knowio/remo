"""Frozen snapshot of the hand-written CLI surface (018-provider-abstraction, T002).

Captured 2026-07-26 from the (pre-refactor) hand-written modules:
src/remo_cli/cli/providers/{incus,hetzner,aws,proxmox}.py

This is the FR-009 preservation reference consumed by
tests/unit/cli/test_surface_preservation.py: every command and every
declared option/argument string recorded here MUST still exist on the
generated CLI produced by the descriptor + factory mechanism. Per
contracts/cli-surface.md, this captured data is authoritative over the
prose matrix in that file for any discrepancy found during descriptor
authoring.

Format
------
``SURFACE[provider][command]`` is a list of declaration strings exactly as
passed to ``click.option()``/``click.argument()`` in the original source
(e.g. ``"--yes"``, ``"-y"``). Positional arguments are recorded using their
click variable name in upper case (e.g. ``"INSTANCE"``); an optional
positional argument is suffixed with ``"?"`` (e.g. ``"INSTANCE?"``).
Subcommands of a provider's ``snapshot`` group are keyed as
``"snapshot create"``, ``"snapshot restore"``, ``"snapshot delete"``,
``"snapshot list"``.
"""

from __future__ import annotations

SURFACE: dict[str, dict[str, list[str]]] = {
    "incus": {
        "create": [
            "--name",
            "--host",
            "--user",
            "--domain",
            "--image",
            "--volume-size",
            "--cores",
            "--memory",
            "--only",
            "--skip",
            "--use-ip",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "destroy": [
            "--name",
            "--host",
            "--user",
            "--remove-storage",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "update": [
            "--name",
            "--host",
            "--user",
            "--volume-size",
            "--cores",
            "--memory",
            "--only",
            "--skip",
            "-v",
            "--verbose",
        ],
        "list": [],
        "info": ["--name", "--host", "--user"],
        "sync": [
            "--host",
            "--user",
            "--use-ip",
            "--all",
            "--yes",
            "-y",
            "--dry-run",
        ],
        "bootstrap": ["--host", "--user", "--network-type", "-v", "--verbose"],
        "snapshot create": ["INSTANCE", "--name", "--description"],
        "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot list": ["INSTANCE?"],
    },
    "proxmox": {
        "create": [
            "--name",
            "--host",
            "--user",
            "--node",
            "--bridge",
            "--storage",
            "--template",
            "--cores",
            "--memory",
            "--volume-size",
            "--unprivileged",
            "--privileged",
            "--domain",
            "--only",
            "--skip",
            "--use-ip",
            "--devcontainer-runtime",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "destroy": [
            "--name",
            "--host",
            "--user",
            "--purge",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "update": [
            "--name",
            "--host",
            "--user",
            "--volume-size",
            "--cores",
            "--memory",
            "--only",
            "--skip",
            "--devcontainer-runtime",
            "-v",
            "--verbose",
        ],
        "list": [],
        "info": ["--name", "--host", "--user"],
        "sync": [
            "--host",
            "--user",
            "--use-ip",
            "--all",
            "--yes",
            "-y",
            "--dry-run",
        ],
        "bootstrap": [
            "--host",
            "--user",
            "--bridge",
            "--storage",
            "--template",
            "-v",
            "--verbose",
        ],
        "snapshot create": ["INSTANCE", "--name", "--description"],
        "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot list": ["INSTANCE?"],
    },
    "aws": {
        "create": [
            "--name",
            "--type",
            "--region",
            "--volume-size",
            "--spot",
            "--iam-profile",
            "--only",
            "--skip",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "destroy": ["--name", "--remove-storage", "--yes", "-y", "-v", "--verbose"],
        "update": ["--name", "--volume-size", "--only", "--skip", "-v", "--verbose"],
        "list": [],
        "sync": ["--region", "--all", "--yes", "-y", "--dry-run"],
        "stop": ["--name", "--yes", "-y"],
        "start": ["--name"],
        "reboot": ["--name", "--yes", "-y"],
        "info": ["--name"],
        "snapshot create": ["INSTANCE", "--name", "--description", "--region"],
        "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y", "--region"],
        "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y", "--region"],
        "snapshot list": ["INSTANCE?", "--region"],
    },
    "hetzner": {
        "create": [
            "--name",
            "--type",
            "--location",
            "--volume-size",
            "--only",
            "--skip",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "destroy": [
            "--name",
            "--remove-volume",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "update": ["--name", "--volume-size", "--only", "--skip", "-v", "--verbose"],
        "list": [],
        "info": ["--name"],
        "sync": ["--all", "--yes", "-y", "--dry-run"],
        "snapshot create": ["INSTANCE", "--name", "--description"],
        "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot list": ["INSTANCE?"],
    },
}

# Default instance name shown/used in `create --help` for each provider (FR-011).
# aws uses the login user ($USER) rather than a literal; recorded as None here.
DEFAULT_INSTANCE_NAMES: dict[str, str | None] = {
    "incus": "dev1",
    "proxmox": "dev1",
    "aws": None,
    "hetzner": "remo",
}

# Providers whose destroy/create/sync accept --yes/-y for auto-confirmation
# (all four, uniformly) — recorded explicitly for FR-012 cross-checks.
CONFIRMABLE_COMMANDS: dict[str, list[str]] = {
    "incus": ["create", "destroy", "sync", "snapshot restore", "snapshot delete"],
    "proxmox": ["create", "destroy", "sync", "snapshot restore", "snapshot delete"],
    "aws": [
        "create",
        "destroy",
        "sync",
        "stop",
        "reboot",
        "snapshot restore",
        "snapshot delete",
    ],
    "hetzner": ["create", "destroy", "sync", "snapshot restore", "snapshot delete"],
}
