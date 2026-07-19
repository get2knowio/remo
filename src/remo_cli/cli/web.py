"""remo web command group - the browser terminal broker service.

This is the ONLY module allowed to import `remo_cli.web.*` (which in turn
imports FastAPI/Uvicorn), and it must do so lazily, inside command bodies —
never at module level. This module itself is imported unconditionally by
`remo_cli.cli.main._register_commands()`, so keeping the top level free of
`fastapi`/`uvicorn`/`remo_cli.web` imports is what makes NFR-008 hold: the
ordinary CLI works even when the `web` extra is not installed.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

_INSTALL_HINT = 'Web support is not installed. Install it with: pip install "remo-cli[web]"'

#: Seconds uvicorn waits for in-flight connections/lifespan shutdown to
#: finish before forcing them closed (NFR-007/SC-014: bounded shutdown).
_GRACEFUL_SHUTDOWN_TIMEOUT_S = 5


def _ensure_ssh_control_dir(control_dir: str) -> str:
    """Ensure the SSH ControlMaster socket dir exists and is writable.

    The default (``/run/remo-ssh``) is a tmpfs mount that only exists inside
    the container image. When running ``remo web serve`` directly on a
    workstation that path is usually absent and can't be created without root
    (e.g. macOS has no ``/run`` at all), which makes every multiplexed terminal
    attach fail with ``unix_listener: cannot bind to path ...``. Create the
    configured dir when possible; otherwise fall back to a short per-user dir
    under ``$HOME`` so local runs work with no manual setup. Returns the path
    actually used (absolute).
    """
    candidate = Path(control_dir).expanduser()
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        if os.access(candidate, os.W_OK):
            return str(candidate)
    except OSError:
        pass

    # `~/.remo/ssh` is intentionally short: the ControlPath socket path
    # (dir + "remo-%r@%h-%p" + ssh's random suffix) must stay under the
    # ~104-byte AF_UNIX limit, which a deep temp path can blow past.
    fallback = Path.home() / ".remo" / "ssh"
    fallback.mkdir(parents=True, exist_ok=True)
    if str(fallback) != str(candidate):
        click.echo(
            f"SSH control dir '{control_dir}' is not usable; using {fallback} instead. "
            f"Set REMO_WEB_SSH_CONTROL_DIR to override."
        )
    return str(fallback)


@click.group()
def web() -> None:
    """Web session interface service (remo web serve / remo web check)."""


@web.command()
@click.option("--host", "bind_host", default=None, help="Override REMO_WEB_BIND_HOST.")
@click.option("--port", "bind_port", type=int, default=None, help="Override REMO_WEB_BIND_PORT.")
def serve(bind_host: str | None, bind_port: int | None) -> None:
    """Run the Remo web service (browser terminal broker).

    Configuration is primarily driven by `REMO_WEB_*` environment variables
    (see `remo_cli.web.config.WebSettings`); --host/--port are convenience
    overrides for local runs.
    """
    try:
        import uvicorn  # noqa: PLC0415, F401

        from remo_cli.web.app import create_app  # noqa: PLC0415
        from remo_cli.web.config import WebSettings  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit(_INSTALL_HINT) from e

    settings = WebSettings()
    if bind_host:
        settings.bind_host = bind_host
    if bind_port:
        settings.bind_port = bind_port

    # Local convenience (012-web-adopt-pairing, research R5): when serving on a
    # loopback interface with no operator-auth provider configured, default to
    # the network-restricted posture so single-machine `remo web serve` can
    # mint pairing codes without a proxy. The service still logs this weaker
    # posture loudly (FR-013); a non-loopback bind is left untouched so a real
    # deployment must configure forward auth explicitly.
    _LOOPBACK = {"127.0.0.1", "localhost", "::1"}
    if not settings.operator_auth and settings.bind_host in _LOOPBACK:
        settings.operator_auth = "none"

    # Ensure the ControlMaster socket dir is usable (create it, or fall back to
    # a per-user dir for local runs where the container's /run/remo-ssh tmpfs
    # doesn't exist). Do this BEFORE exporting REMO_SSH_CONTROL_DIR / building
    # the app so every SSH attach uses the resolved, writable path.
    settings.ssh_control_dir = _ensure_ssh_control_dir(settings.ssh_control_dir)

    # Every web call site that builds an SSH command threads
    # settings.ssh_control_dir explicitly through build_ssh_base_cmd's
    # control_dir= param (see web/discovery.py's _discover_one_sync and
    # web/terminal.py's build_attach_argv, both invoked with
    # control_dir=settings.ssh_control_dir) -- verified by reading both call
    # sites, so this env var isn't load-bearing for this process's own SSH
    # invocations. It's set anyway as a defense-in-depth safety net for any
    # code path (present or future) that falls back to
    # core.ssh.resolve_ssh_control_dir()'s $REMO_SSH_CONTROL_DIR lookup
    # instead of an explicit control_dir=.
    os.environ["REMO_SSH_CONTROL_DIR"] = settings.ssh_control_dir

    # create_app fail-fasts on an invalid operator-auth posture (forward auth
    # enabled without REMO_WEB_FORWARD_AUTH_HEADER); surface it as a clean
    # error instead of a raw traceback (Constitution IV, fail fast with a clear
    # message).
    from remo_cli.web.operator_auth import OperatorAuthConfigError  # noqa: PLC0415

    try:
        app = create_app(settings)
    except OperatorAuthConfigError as e:
        raise SystemExit(f"Configuration error: {e}") from e

    # "logs a ready readiness state" (quickstart.md section A): this line,
    # combined with uvicorn's own "Application startup complete" log emitted
    # once the lifespan startup phase finishes, gives an operator a clear
    # ready signal without needing a custom startup-complete hook.
    click.echo(f"Remo web service starting on http://{settings.bind_host}:{settings.bind_port}")

    # uvicorn.Server (rather than the bare uvicorn.run(...) convenience
    # wrapper) so timeout_graceful_shutdown is explicit: on SIGINT/SIGTERM,
    # uvicorn stops accepting new connections and runs the FastAPI lifespan
    # shutdown phase (web/app.py's _lifespan sets app.state.shutting_down
    # before reaping every TerminalRegistry attachment -- local ssh/PTY
    # processes only; remote Zellij sessions are left running), bounded by
    # this timeout (NFR-007/SC-014).
    config = uvicorn.Config(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_TIMEOUT_S,
    )
    server = uvicorn.Server(config)
    server.run()


@web.command()
@click.option(
    "--skip-instance-checks",
    is_flag=True,
    default=False,
    help=(
        "Skip per-instance reachability/protocol checks; validate only "
        "config/mounts/executables. Used as the container startup gate so a "
        "single unreachable instance can't block the service from starting."
    ),
)
def check(skip_instance_checks: bool) -> None:
    """Validate configuration and connectivity for the web service.

    Runs the full FR-046 diagnostic (registry, SSH identity, runtime dir,
    required executables, and per-instance reachability/protocol
    compatibility) and prints a PASS/FAIL report. Never opens an
    interactive session -- only `remo-host capabilities` is invoked against
    registered instances, never `sessions attach`. Exits non-zero if any
    check fails.

    With ``--skip-instance-checks``, the per-instance reachability round-trips
    are omitted (config/mounts/executables only) — an unreachable instance is
    an expected, per-instance condition (FR-006) and must not fail the whole
    startup gate.
    """
    try:
        from remo_cli.web import check as web_check  # noqa: PLC0415
        from remo_cli.web.config import WebSettings  # noqa: PLC0415
    except ImportError as e:
        raise SystemExit(_INSTALL_HINT) from e

    results = web_check.run_checks(WebSettings(), include_instances=not skip_instance_checks)
    click.echo(web_check.format_results(results))
    if not web_check.all_passed(results):
        raise SystemExit(1)


@web.command()
@click.argument("url", required=False, default=None)
@click.option(
    "--token",
    default=None,
    help="Pairing code (falls back to REMO_API_TOKEN, then a hidden prompt).",
)
@click.option(
    "--via",
    "via_host",
    default=None,
    metavar="HOST",
    help=(
        "SSH host to tunnel through: opens `ssh -N -L <free-port>:127.0.0.1:"
        "<service-port> HOST` and runs the flow via http://127.0.0.1:<free-port>. "
        "Requires 127.0.0.1 in the service's REMO_WEB_ALLOWED_HOSTS."
    ),
)
@click.option(
    "--allow-empty",
    is_flag=True,
    default=False,
    help="Push even when the local registry is empty (wipes the service's instance list).",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help=(
        "Non-interactive: skip fingerprint prompts (unverified instances are "
        "reported as skipped_no_trust)."
    ),
)
def adopt(
    url: str | None,
    token: str | None,
    via_host: str | None,
    allow_empty: bool,
    assume_yes: bool,
) -> None:
    """Adopt a running remo web service from this workstation.

    Open the service's awaiting-adoption page in a browser, click "Copy pairing
    code", then run this command and paste the code when prompted. It pushes the
    local registry (full mirror) plus verified SSH host keys to the service and
    authorizes the service's SSH key on each reachable direct-access instance,
    then runs a service-side verification pass.

    URL resolution: argument, then $REMO_API_URL, then an interactive prompt.
    Pairing code: --token, then $REMO_API_TOKEN, then a hidden prompt. Nothing
    is saved — a later `remo web push` gets a fresh code the same way.

    Exits 0 when the flow completes (per-instance skips/flags are reported in
    the summary, not fatal); exits 1 on hard failure (dormant setup surface,
    mount-configured deployment, empty registry without --allow-empty, tunnel
    failure, payload rejected).
    """
    # Deliberately imports only remo_cli.core.* — `remo web adopt` must work
    # without the `web` extra installed (stdlib HTTP only, research R9).
    from remo_cli.core.output import print_error  # noqa: PLC0415
    from remo_cli.core.web_adopt import AdoptError, run_adopt  # noqa: PLC0415

    resolved_url = url or os.environ.get("REMO_API_URL") or click.prompt("Service URL")
    resolved_code = (
        token or os.environ.get("REMO_API_TOKEN") or click.prompt("Pairing code", hide_input=True)
    )

    try:
        run_adopt(
            resolved_url,
            resolved_code,
            via=via_host,
            allow_empty=allow_empty,
            assume_yes=assume_yes,
        )
    except AdoptError as e:
        print_error(str(e))
        raise SystemExit(1) from e


@web.command()
@click.argument("url", required=False, default=None)
@click.option(
    "--token",
    default=None,
    help="Pairing code (falls back to REMO_API_TOKEN, then a hidden prompt).",
)
@click.option(
    "--via",
    "via_host",
    default=None,
    metavar="HOST",
    help=(
        "SSH host to tunnel through (see `remo web adopt --via`). Requires "
        "127.0.0.1 in the service's REMO_WEB_ALLOWED_HOSTS."
    ),
)
@click.option(
    "--allow-empty",
    is_flag=True,
    default=False,
    help="Push even when the local registry is empty (wipes the service's instance list).",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help=(
        "Non-interactive: skip fingerprint prompts for new/changed instances "
        "(unverified instances are reported as skipped_no_trust)."
    ),
)
def push(
    url: str | None,
    token: str | None,
    via_host: str | None,
    allow_empty: bool,
    assume_yes: bool,
) -> None:
    """Re-sync the local registry to an adopted remo web service.

    Open the dashboard's "Pair CLI to sync" affordance, click to copy a fresh
    pairing code, then run this command and paste it. It updates the service's
    registry (full mirror — removals propagate), pushes host keys, and authorizes
    the service's identity on new or changed direct-access instances. Instances
    unchanged since the last push skip the keyscan/authorize work and are
    reported as `unchanged`.

    URL resolution: argument, then $REMO_API_URL, then an interactive prompt.
    Pairing code: --token, then $REMO_API_TOKEN, then a hidden prompt. Nothing
    is saved between runs — every push gets a fresh code from the page.

    Exits 0 when the flow completes (per-instance skips/flags are reported in
    the summary, not fatal); exits 1 on hard failure (dormant setup surface,
    mount-configured deployment, empty registry without --allow-empty).
    """
    # Deliberately imports only remo_cli.core.* — `remo web push` must work
    # without the `web` extra installed (stdlib HTTP only, research R9).
    from remo_cli.core.output import print_error  # noqa: PLC0415
    from remo_cli.core.web_adopt import AdoptError, run_push  # noqa: PLC0415

    resolved_url = url or os.environ.get("REMO_API_URL") or click.prompt("Service URL")
    resolved_code = (
        token or os.environ.get("REMO_API_TOKEN") or click.prompt("Pairing code", hide_input=True)
    )

    try:
        run_push(
            resolved_url,
            resolved_code,
            via=via_host,
            allow_empty=allow_empty,
            assume_yes=assume_yes,
        )
    except AdoptError as e:
        print_error(str(e))
        raise SystemExit(1) from e
