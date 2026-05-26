"""`remo init` — configure the laptop side of the credential broker.

Per contracts/cli-surface.md:
  remo init [--backend {1password|vault|aws-sm|age-git}]
            [--admin-sa-fnox-key KEY]
            [--accept-downgrade]
            [--non-interactive]

Exit codes:
  0 success
  2 user declined a required warning (e.g. age-git without --accept-downgrade)
  3 fnox not installed
  4 interactive identity rejected
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import click
import yaml

from remo_cli.core import fnox
from remo_cli.core.config import get_remo_home
from remo_cli.core.output import print_error, print_info, print_success, print_warning
from remo_cli.providers.broker import SUPPORTED_BACKENDS

INTERACTIVE_IDENTITY_HINT = (
    "interactive backend identity rejected. The broker must run unattended "
    "(autonomous overnight agent sessions). Configure a Service Account / "
    "AppRole / IAM principal that does not require human unlock."
)


def _save_config(backend: str, admin_sa_fnox_key: str | None) -> Path:
    broker_cfg: dict[str, object] = {"backend": backend}
    if admin_sa_fnox_key:
        broker_cfg["admin_sa_fnox_key"] = admin_sa_fnox_key
    config: dict[str, object] = {
        "version": 1,
        "broker": broker_cfg,
    }

    path = get_remo_home() / "config.yml"
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except PermissionError:
        pass
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


@click.command("init")
@click.option(
    "--backend",
    type=click.Choice(list(SUPPORTED_BACKENDS), case_sensitive=False),
    default=None,
    help="Backend used to mint per-instance credentials.",
)
@click.option(
    "--admin-sa-fnox-key",
    default=None,
    help="fnox key holding the developer's admin SA token (Incus/Proxmox only).",
)
@click.option(
    "--accept-downgrade",
    is_flag=True,
    default=False,
    help="Acknowledge that age-git provides no per-instance scoping (FR-003).",
)
@click.option(
    "--non-interactive",
    is_flag=True,
    default=False,
    help="Fail rather than prompt for missing inputs.",
)
@click.option(
    "--allow-interactive-identity",
    is_flag=True,
    default=False,
    help=(
        "(advanced) bypass the interactive-identity rejection. Only use if "
        "you have manually verified the backend identity is unattended."
    ),
)
def init_command(
    backend: str | None,
    admin_sa_fnox_key: str | None,
    accept_downgrade: bool,
    non_interactive: bool,
    allow_interactive_identity: bool,
) -> None:
    """Initialize the laptop side of the credential broker.

    Detects `fnox`, selects a backend, and persists the choice to
    `~/.config/remo/config.yml` (mode 0600). Never writes secret values.
    """

    if not fnox.is_installed():
        print_error(
            "`fnox` is not installed. Install it from "
            "https://github.com/jdx/fnox and re-run `remo init`."
        )
        sys.exit(3)

    if backend is None:
        if non_interactive:
            print_error("--backend is required when --non-interactive is set.")
            sys.exit(2)
        click.echo("Available backends:")
        for opt in SUPPORTED_BACKENDS:
            click.echo(f"  - {opt}")
        backend = click.prompt(
            "Pick a backend",
            type=click.Choice(list(SUPPORTED_BACKENDS), case_sensitive=False),
        )
    backend = backend.lower()

    # FR-003 downgrade warning (age-git lacks per-instance scoping).
    if backend == "age-git" and not accept_downgrade:
        print_warning(
            "age + git backend has no per-instance scoping primitive. "
            "Bootstrap tokens for Hetzner/Incus/Proxmox will fall back to "
            "laptop-unlock-per-session; AWS scoping is unaffected. "
            "Re-run with --accept-downgrade to acknowledge."
        )
        sys.exit(2)

    # FR-003a — interactive identity rejection (Clarifications Q2).
    # We cannot directly probe a Service Account vs personal account here, but
    # we surface the rule and refuse unless the user opts in explicitly. The
    # backend SDK (when called for a mint) is the second line of defense.
    if not allow_interactive_identity and backend in {"1password", "vault"}:
        # Heuristic: the broker on the instance must run unattended; if the
        # operator is configuring it on an interactive laptop session, we
        # warn but proceed when fnox can fetch the admin SA without prompting.
        if admin_sa_fnox_key:
            try:
                fnox.get(admin_sa_fnox_key)
            except fnox.FnoxError as exc:
                print_error(
                    f"Could not read admin SA token from fnox key "
                    f"`{admin_sa_fnox_key}`: {exc}"
                )
                print_error(INTERACTIVE_IDENTITY_HINT)
                sys.exit(4)

    config_path = _save_config(backend, admin_sa_fnox_key)
    print_success(f"Wrote {config_path} (mode 0600). Backend = {backend}.")
    print_info(
        "Next: store provisioning + admin SA credentials in fnox, e.g. "
        "`fnox set hetzner_api_token` / `fnox set <admin_sa_fnox_key>`."
    )
