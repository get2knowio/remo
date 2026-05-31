"""remo notifier commands — deploy and operate the notifier sidecar.

This module is the laptop-side integration point. It must NOT import the
notifier service package's heavy deps (FastAPI / python-telegram-bot); it only
drives Ansible (deploy) and SSH (status/logs/test/restart).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys

import click

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.config import get_remo_home
from remo_cli.core.output import print_error, print_info
from remo_cli.core.ssh import build_ssh_opts, resolve_remo_host
from remo_cli.models.host import KnownHost

# Files that make up the on-host image build context (finding U1 / research R5).
_CONTEXT_FILES = ["pyproject.toml", "README.md", "uv.lock"]


def _resolve(host: str) -> KnownHost:
    """Resolve a host by name, or fuzzy-pick when none is given (FR-031)."""
    return resolve_remo_host(host or None)


def _bind_url(host: KnownHost, path: str, *, bind: str, port: int) -> str:
    return f"http://{bind}:{port}{path}"


def _ssh_run(host: KnownHost, remote_cmd: str, *, capture: bool = False) -> subprocess.CompletedProcess:
    """Run a command on *host* over SSH, reusing remo's SSH options."""
    ssh_opts, target = build_ssh_opts(host)
    cmd = ["ssh", *ssh_opts, target, remote_cmd]
    return subprocess.run(cmd, text=True, capture_output=capture)


def _ensure_build_context() -> str:
    """Assemble and return a clean image build context directory.

    The context mirrors the repo layout the Dockerfile expects: ``notifier/``
    (Dockerfile), ``ansible/`` and ``src/remo_cli`` (both force-included by the
    wheel build), plus ``pyproject.toml`` / ``README.md`` / ``uv.lock``. v1
    requires a source checkout (``get_project_root``); published images are the
    future path (spec Out-of-Scope). See research R5 / finding U1.
    """
    from remo_cli.core.config import get_project_root

    root = get_project_root()
    ctx = get_remo_home() / "notifier_build"
    if ctx.exists():
        shutil.rmtree(ctx)
    (ctx / "notifier").mkdir(parents=True)
    shutil.copy2(root / "notifier" / "Dockerfile", ctx / "notifier" / "Dockerfile")
    for name in _CONTEXT_FILES:
        src = root / name
        if src.is_file():
            shutil.copy2(src, ctx / name)
    shutil.copytree(root / "src" / "remo_cli", ctx / "src" / "remo_cli")
    shutil.copytree(root / "ansible", ctx / "ansible")
    return str(ctx)


@click.group()
def notifier() -> None:
    """Manage the notifier sidecar (agentsh approval bridge)."""


@notifier.command()
@click.argument("host", default="")
@click.option("--rebuild", is_flag=True, help="Force a Docker image rebuild on the host.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def deploy(host: str, rebuild: bool, verbose: bool) -> None:
    """Deploy the notifier to HOST (applies the remo_notifier Ansible role)."""
    token = os.environ.get("REMO_NOTIFIER_TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("REMO_NOTIFIER_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print_error(
            "Missing Telegram credentials. Set both REMO_NOTIFIER_TELEGRAM_BOT_TOKEN "
            "and REMO_NOTIFIER_TELEGRAM_CHAT_ID before deploying "
            "(see the README 'Notifier setup' section)."
        )
        sys.exit(1)

    target = _resolve(host)
    context_dir = _ensure_build_context()

    print_info(f"Deploying notifier to '{target.display_name}' at {target.host}...")
    extra_vars = [
        "-i", f"{target.host},",
        "-e", f"ansible_user={target.user}",
        "-e", f"remo_notifier_build_context_local={context_dir}",
    ]
    if rebuild:
        extra_vars += ["-e", "remo_notifier_force_rebuild=true"]

    rc = run_playbook("notifier_deploy.yml", extra_vars, verbose=verbose)
    sys.exit(rc)


@notifier.command()
@click.argument("host", default="")
@click.option("--bind", default="172.17.0.1", help="Host bind address of the notifier.")
@click.option("--port", default=18181, help="Notifier port.")
def status(host: str, bind: str, port: int) -> None:
    """Show the notifier health summary on HOST."""
    target = _resolve(host)
    url = _bind_url(target, "/v1/health", bind=bind, port=port)
    result = _ssh_run(target, f"curl -sf {shlex.quote(url)}", capture=True)
    if result.returncode != 0:
        print_error(f"notifier unreachable on {target.display_name} ({url}).")
        sys.exit(1)
    try:
        click.echo(json.dumps(json.loads(result.stdout), indent=2))
    except json.JSONDecodeError:
        click.echo(result.stdout)


@notifier.command()
@click.argument("host", default="")
@click.option("--follow", "-f", is_flag=True, help="Follow the log stream.")
@click.option("--lines", "-n", default=100, help="Number of lines to show (default 100).")
def logs(host: str, follow: bool, lines: int) -> None:
    """Show notifier journald logs on HOST."""
    target = _resolve(host)
    cmd = f"journalctl -u remo-notifier.service -n {int(lines)}"
    if follow:
        cmd += " -f"
    sys.exit(_ssh_run(target, cmd).returncode)


@notifier.command()
@click.argument("host", default="")
def restart(host: str) -> None:
    """Restart the notifier service on HOST."""
    target = _resolve(host)
    print_info(f"Restarting remo-notifier on '{target.display_name}'...")
    sys.exit(_ssh_run(target, "sudo systemctl restart remo-notifier.service").returncode)


@notifier.command(name="test")
@click.argument("host", default="")
@click.option("--bind", default="172.17.0.1", help="Host bind address of the notifier.")
@click.option("--port", default=18181, help="Notifier port.")
@click.option("--timeout", default=120, help="Approval timeout in seconds.")
def test_cmd(host: str, bind: str, port: int, timeout: int) -> None:
    """Send a test approval through HOST and report the decision."""
    target = _resolve(host)
    payload = {
        "operation": {"kind": "command", "command": "echo", "args": ["wiring-check"]},
        "policy_rule_name": "test",
        "policy_message": (
            "This is a test approval — please tap Approve or Deny to confirm wiring."
        ),
        "project": "remo-notifier-selftest",
        "instance_id": target.display_name,
        "timeout_seconds": timeout,
    }
    url = _bind_url(target, "/v1/approve", bind=bind, port=port)
    body = shlex.quote(json.dumps(payload))
    remote = (
        f"curl -s -X POST {shlex.quote(url)} "
        f"-H 'content-type: application/json' -d {body}"
    )
    print_info(f"Sending test approval to '{target.display_name}' — check Telegram...")
    result = _ssh_run(target, remote, capture=True)
    if result.returncode != 0 or not result.stdout.strip():
        print_error(f"notifier unreachable on {target.display_name} ({url}).")
        sys.exit(1)
    try:
        decision = json.loads(result.stdout)
    except json.JSONDecodeError:
        print_error(f"unexpected response: {result.stdout!r}")
        sys.exit(1)
    click.echo(json.dumps(decision, indent=2))
    sys.exit(0 if decision.get("decision") in {"allow", "deny"} else 1)
