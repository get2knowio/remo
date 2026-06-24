"""remo notifier commands — deploy and operate the notifier sidecar.

This module is the laptop-side integration point. It imports only the
channel **catalog** (pure metadata) — never the notifier service deps
(FastAPI / a channel SDK) — and drives Ansible (deploy) and SSH
(status/logs/test/restart). See contracts/cli-notifier.md.
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

# agentsh approver connection — channel-independent, required for every channel.
_AGENTSH_URL_ENV = "REMO_NOTIFIER_AGENTSH_API_URL"
_AGENTSH_KEY_ENV = "REMO_NOTIFIER_AGENTSH_API_KEY"


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
    """Assemble and return a clean image build context directory."""
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


def _resolve_channel(channel_opt: str | None):
    """Resolve the channel descriptor per the selection rules (FR-009/010/011)."""
    from remo_cli.notifier.channels import catalog

    channels = catalog.list_channels()
    if channel_opt:
        descriptor = catalog.get(channel_opt)
        if descriptor is None:
            available = ", ".join(c.id for c in channels)
            print_error(f"unknown channel '{channel_opt}'; available: {available}")
            sys.exit(1)
        return descriptor

    # No --channel given.
    if len(channels) == 1:
        return channels[0]  # single-channel catalog: auto-select (edge case)
    if sys.stdin.isatty():
        from InquirerPy import inquirer  # lazy import

        choices = [{"name": f"{c.id}  —  {c.label}", "value": c} for c in channels]
        try:
            picked = inquirer.fuzzy(message="Select a notifier channel: ", choices=choices).execute()
        except KeyboardInterrupt:
            sys.exit(0)
        if picked is None:
            sys.exit(0)
        return picked
    print_error(
        "No channel specified and not running interactively. "
        "Re-run with --channel <id> (see `remo notifier channels`)."
    )
    sys.exit(1)


def _preflight(descriptor) -> dict[str, str]:
    """Verify the agentsh + per-channel env is set; return the gathered values.

    Exits non-zero naming exactly the missing vars and deploying nothing
    (FR-012/FR-012a, SC-007).
    """
    missing: list[str] = []
    values: dict[str, str] = {}

    # Channel-independent agentsh approver inputs (required for every channel).
    for name, purpose in (
        (_AGENTSH_URL_ENV, "agentsh approvals API base URL"),
        (_AGENTSH_KEY_ENV, "agentsh approver-role API key"),
    ):
        val = os.environ.get(name, "").strip()
        if not val:
            missing.append(f"  {name} — {purpose}")
        else:
            values[name] = val

    # Per-channel credentials declared by the descriptor.
    for env in descriptor.required_env:
        val = os.environ.get(env.name, "").strip()
        secret_note = " (secret)" if env.secret else ""
        if not val:
            missing.append(f"  {env.name}{secret_note} — {env.purpose}")
        else:
            values[env.name] = val

    if missing:
        print_error(
            f"Missing required environment for channel '{descriptor.id}'. "
            "Set these and re-run (nothing was deployed):\n" + "\n".join(missing)
        )
        sys.exit(1)
    return values


@click.group()
def notifier() -> None:
    """Manage the notifier sidecar (agentsh approval bridge)."""


@notifier.command()
def channels() -> None:
    """List the available notifier channels and their required env (FR-006a)."""
    from remo_cli.notifier.channels import catalog

    rows = [("CHANNEL", "LABEL", "REQUIRED ENV")]
    for descriptor in catalog.list_channels():
        env = ", ".join(
            f"{e.name}{' (secret)' if e.secret else ''}" for e in descriptor.required_env
        )
        rows.append((descriptor.id, descriptor.label, env or "(none)"))
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(r[1]) for r in rows)
    for c0, c1, c2 in rows:
        click.echo(f"{c0:<{w0}}  {c1:<{w1}}  {c2}")


@notifier.command()
@click.argument("host", default="")
@click.option("--channel", "channel_opt", default=None, help="Channel id to deploy (e.g. telegram).")
@click.option("--rebuild", is_flag=True, help="Force a Docker image rebuild on the host.")
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output.")
def deploy(host: str, channel_opt: str | None, rebuild: bool, verbose: bool) -> None:
    """Deploy the notifier to HOST with the selected channel."""
    descriptor = _resolve_channel(channel_opt)
    values = _preflight(descriptor)

    target = _resolve(host)
    context_dir = _ensure_build_context()

    # Non-secret values render into the transport TOML fragment (owned by the
    # channel); the single secret goes to the on-host secret file.
    non_secret = {e.name: values[e.name] for e in descriptor.required_env if not e.secret}
    transport_toml = descriptor.render_transport_toml(non_secret)
    secret_env = descriptor.secret_env()

    print_info(
        f"Deploying notifier ({descriptor.label}) to "
        f"'{target.display_name}' at {target.host}..."
    )
    extra_vars = [
        "-i", f"{target.host},",
        "-e", f"ansible_user={target.user}",
        "-e", f"remo_notifier_build_context_local={context_dir}",
        "-e", f"remo_notifier_channel={descriptor.id}",
        "-e", f"remo_notifier_transport_toml={transport_toml}",
        "-e", f"remo_notifier_agentsh_api_url={values[_AGENTSH_URL_ENV]}",
        "-e", f"remo_notifier_agentsh_api_key={values[_AGENTSH_KEY_ENV]}",
    ]
    if secret_env is not None and descriptor.secret_filename():
        extra_vars += [
            "-e", f"remo_notifier_channel_secret={values[secret_env.name]}",
            "-e", f"remo_notifier_secret_filename={descriptor.secret_filename()}",
            "-e", f"remo_notifier_secret_mount={descriptor.secret_mount}",
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
@click.option("--bind", default="172.17.0.1", help="Host bind address of the notifier.")
@click.option("--port", default=18181, help="Notifier port.")
def sources(host: str, bind: str, port: int) -> None:
    """List the sources HOST's notifier is currently serving (FR-020)."""
    target = _resolve(host)
    url = _bind_url(target, "/v1/sources", bind=bind, port=port)
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
    """Round-trip a test approval through HOST's installed channel.

    Drives the local synthetic-approval injection (`POST /v1/test`) — it never
    contacts agentsh — and reports the human's tap (FR / contracts/cli-notifier.md).
    """
    target = _resolve(host)
    payload = {"timeout_seconds": timeout}
    url = _bind_url(target, "/v1/test", bind=bind, port=port)
    body = shlex.quote(json.dumps(payload))
    remote = (
        f"curl -s -X POST {shlex.quote(url)} "
        f"-H 'content-type: application/json' -d {body}"
    )
    print_info(f"Sending test approval to '{target.display_name}' — check your channel...")
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
