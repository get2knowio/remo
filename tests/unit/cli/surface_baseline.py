"""Frozen snapshot of the generated CLI surface (021-cli-plane-separation).

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
Subcommands of a provider's ``snapshot``/``host`` group are keyed as
``"snapshot create"``, ``"snapshot restore"``, ``"snapshot delete"``,
``"snapshot list"``, ``"host bootstrap"``.

Rewritten in full for spec 021 (CLI plane separation): the three-intent
``update`` verb is gone (replaced by ``upgrade``/``resize``/``tag``), flat
``bootstrap`` moved under the ``host`` subgroup, and incus/proxmox's
``--user`` flag is now ``--host-user``/``--node-user`` respectively. This
file *is* the intentional-breaking-change acknowledgment (research.md D9) —
it is deliberately NOT preserved byte-for-byte from the pre-021 surface.

Deliberate divergence from the 2026-07-26 capture (019-hygiene-deps-docs,
US5, the sole FR-026 carve-out): ``"--yes"``/``"-y"`` were removed from each
provider's ``create`` list. The flag never had any effect on create (there
is no confirmation prompt to skip); its removal is an intentional CLI
break, not baseline corruption. See
``specs/019-hygiene-deps-docs/contracts/cli-surface-delta.md``. ``--yes``/
``-y`` are unchanged everywhere else (destroy/sync/snapshot restore/
snapshot delete).
"""

from __future__ import annotations

SURFACE: dict[str, dict[str, list[str]]] = {
    "incus": {
        "create": [
            "--name",
            "--host",
            "--host-user",
            "--domain",
            "--image",
            "--volume-size",
            "--cores",
            "--memory",
            "--only",
            "--skip",
            "--use-ip",
            "-v",
            "--verbose",
        ],
        "destroy": [
            "--name",
            "--host",
            "--host-user",
            "--remove-storage",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "upgrade": [
            "NAME",
            "--host",
            "--host-user",
            "--only",
            "--skip",
            "-v",
            "--verbose",
        ],
        "resize": [
            "NAME",
            "--volume-size",
            "--cores",
            "--memory",
            "--host",
            "--host-user",
            "-v",
            "--verbose",
        ],
        "tag": ["NAME", "--host", "--host-user"],
        "list": [],
        "info": ["--name", "--host", "--host-user"],
        "sync": [
            "--host",
            "--host-user",
            "--use-ip",
            "--all",
            "--yes",
            "-y",
            "--dry-run",
        ],
        "host bootstrap": ["HOST?", "--host-user", "--network-type", "-v", "--verbose"],
        "snapshot create": ["INSTANCE", "--name", "--description"],
        "snapshot restore": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot delete": ["INSTANCE", "SNAP_NAME", "--yes", "-y"],
        "snapshot list": ["INSTANCE?"],
    },
    "proxmox": {
        "create": [
            "--name",
            "--host",
            "--node-user",
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
            "-v",
            "--verbose",
        ],
        "destroy": [
            "--name",
            "--host",
            "--node-user",
            "--purge",
            "--yes",
            "-y",
            "-v",
            "--verbose",
        ],
        "upgrade": [
            "NAME",
            "--host",
            "--node-user",
            "--devcontainer-runtime",
            "--only",
            "--skip",
            "-v",
            "--verbose",
        ],
        "resize": [
            "NAME",
            "--volume-size",
            "--cores",
            "--memory",
            "--host",
            "--node-user",
            "-v",
            "--verbose",
        ],
        "tag": ["NAME", "--host", "--node-user"],
        "list": [],
        "info": ["--name", "--host", "--node-user"],
        "sync": [
            "--host",
            "--node-user",
            "--use-ip",
            "--all",
            "--yes",
            "-y",
            "--dry-run",
        ],
        "host bootstrap": [
            "HOST",
            "--node-user",
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
            "-v",
            "--verbose",
        ],
        "destroy": ["--name", "--remove-storage", "--yes", "-y", "-v", "--verbose"],
        "upgrade": ["NAME", "--only", "--skip", "-v", "--verbose"],
        "resize": ["NAME", "--volume-size", "-v", "--verbose"],
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
        "upgrade": ["NAME", "--only", "--skip", "-v", "--verbose"],
        "resize": ["NAME", "--volume-size", "-v", "--verbose"],
        "tag": ["NAME"],
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

# Providers whose destroy/sync accept --yes/-y for auto-confirmation (all
# four, uniformly) — recorded explicitly for FR-012 cross-checks. `create`
# was removed from this list in 019-hygiene-deps-docs US5: the flag never
# had any effect there and has been removed from create entirely.
CONFIRMABLE_COMMANDS: dict[str, list[str]] = {
    "incus": ["destroy", "sync", "snapshot restore", "snapshot delete"],
    "proxmox": ["destroy", "sync", "snapshot restore", "snapshot delete"],
    "aws": [
        "destroy",
        "sync",
        "stop",
        "reboot",
        "snapshot restore",
        "snapshot delete",
    ],
    "hetzner": ["destroy", "sync", "snapshot restore", "snapshot delete"],
}
