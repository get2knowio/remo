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

import click

_INSTALL_HINT = 'Web support is not installed. Install it with: pip install "remo-cli[web]"'

#: Seconds uvicorn waits for in-flight connections/lifespan shutdown to
#: finish before forcing them closed (NFR-007/SC-014: bounded shutdown).
_GRACEFUL_SHUTDOWN_TIMEOUT_S = 5


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

    app = create_app(settings)

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
