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
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Re-scan and re-authorize every direct-access instance (bypass the unchanged fast-path).",
)
def adopt(
    url: str | None,
    token: str | None,
    via_host: str | None,
    allow_empty: bool,
    assume_yes: bool,
    force: bool,
) -> None:
    """[DEPRECATED] Alias for `remo web push` — first push adopts automatically.

    `remo web adopt` is deprecated and will be removed in a future release. The
    unified `remo web push` adopts a not-yet-adopted deployment on first use and
    re-syncs afterwards — you no longer choose between the two. This command
    prints a deprecation notice and then behaves exactly like `remo web push`.
    """
    # Deliberately imports only remo_cli.core.* — this must work without the
    # `web` extra installed (stdlib HTTP only, research R9).
    from remo_cli.core.output import print_error, print_warning  # noqa: PLC0415
    from remo_cli.core.web_adopt import AdoptError, run_push  # noqa: PLC0415

    print_warning(
        "`remo web adopt` is deprecated; use `remo web push` — the first push "
        "adopts automatically."
    )

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
            force=force,
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
        "(unverified instances are reported as skipped_no_trust); on flap "
        "detection, warn and proceed instead of prompting."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Re-scan host keys and re-authorize the service key on EVERY "
        "direct-access instance, bypassing the fingerprint 'unchanged' "
        "fast-path (recovers an out-of-band-rebuilt instance)."
    ),
)
def push(
    url: str | None,
    token: str | None,
    via_host: str | None,
    allow_empty: bool,
    assume_yes: bool,
    force: bool,
) -> None:
    """Connect this workstation's registry to a remo web service (adopt or re-sync).

    The FIRST push to a not-yet-adopted deployment adopts it; every later push
    re-syncs — this is auto-detected, you never choose. Open the page's pairing
    affordance, copy a fresh code, then run this command and paste it. It updates
    the service's registry (full mirror — removals propagate and their service
    key is best-effort revoked), pushes verified host keys, and authorizes the
    service's identity on new or changed direct-access instances. Instances
    unchanged since the last push are reported `unchanged` (use --force to
    re-process them anyway).

    URL resolution: argument, then $REMO_API_URL, then an interactive prompt.
    Pairing code: --token, then $REMO_API_TOKEN, then a hidden prompt. Nothing
    is saved between runs — every push gets a fresh code from the page.

    Exits 0 when the flow completes (per-instance skips/flags and revocation
    failures are reported in the summary, not fatal); exits 1 on hard failure
    (dormant setup surface, mount-configured deployment, empty registry without
    --allow-empty, or a flap abort declined interactively).
    """
    # Deliberately imports only remo_cli.core.* — `remo web push` must work
    # without the `web` extra installed (stdlib HTTP only, research R9).
    from remo_cli.core.output import print_error, print_warning  # noqa: PLC0415
    from remo_cli.core.web_adopt import AdoptError, run_push  # noqa: PLC0415

    print_warning(
        "`remo web push` is one-way: it force-overwrites the deployment's "
        "registry, discarding any changes made in the web console. Use "
        "`remo web sync` to merge both sides instead."
    )

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
            force=force,
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
        "SSH host to tunnel through (see `remo web push --via`). Requires "
        "127.0.0.1 in the service's REMO_WEB_ALLOWED_HOSTS."
    ),
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    default=False,
    help=(
        "Non-interactive: skip fingerprint prompts and consent to deletions in "
        "both directions. Conflicts still need --prefer-local/--prefer-remote."
    ),
)
@click.option(
    "--prefer-local",
    "prefer_local",
    is_flag=True,
    default=False,
    help="Resolve every conflict by keeping this workstation's version.",
)
@click.option(
    "--prefer-remote",
    "prefer_remote",
    is_flag=True,
    default=False,
    help="Resolve every conflict by keeping the deployment's version.",
)
@click.option(
    "--allow-empty",
    is_flag=True,
    default=False,
    help="Sync even when the merged registry is empty (wipes both instance lists).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Render the merge plan and stop — nothing is written anywhere.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Re-scan host keys and re-authorize the service key on every pushed "
        "direct-access instance (bypass the unchanged fast-path)."
    ),
)
def sync(
    url: str | None,
    token: str | None,
    via_host: str | None,
    assume_yes: bool,
    prefer_local: bool,
    prefer_remote: bool,
    allow_empty: bool,
    dry_run: bool,
    force: bool,
) -> None:
    """Bi-directionally sync this workstation's registry with a deployment.

    Computes an entry-level three-way merge (base = the last push/sync's
    cache, local = this registry, remote = the deployment's) and converges
    both sides: new local entries push up, console-added entries pull down,
    deletions propagate (with consent) in both directions, and divergent
    edits surface as conflicts you resolve per entry (keep local / keep
    remote / skip) or wholesale via --prefer-local/--prefer-remote.

    Concurrency-safe: the write carries the mirror generation the merge was
    computed against; if the deployment changed in between, sync re-merges
    and retries instead of overwriting. Use the deprecated `remo web push`
    only when you deliberately want to force-overwrite the deployment.

    URL resolution: argument, then $REMO_API_URL, then an interactive prompt.
    Pairing code: --token, then $REMO_API_TOKEN, then a hidden prompt.

    \b
    Exit codes:
      0  merged and applied (or --dry-run rendered the plan)
      1  hard failure (unsupported service, connection, retries exhausted)
      3  aborted: unresolved conflicts or declined deletion consent
    """
    # Deliberately imports only remo_cli.core.* — `remo web sync` must work
    # without the `web` extra installed (stdlib HTTP only).
    from remo_cli.core.output import print_error  # noqa: PLC0415
    from remo_cli.core.web_sync import run_web_sync  # noqa: PLC0415

    if prefer_local and prefer_remote:
        print_error("--prefer-local and --prefer-remote are mutually exclusive.")
        raise SystemExit(1)
    prefer = "local" if prefer_local else ("remote" if prefer_remote else None)

    resolved_url = url or os.environ.get("REMO_API_URL") or click.prompt("Service URL")
    resolved_code = (
        token or os.environ.get("REMO_API_TOKEN") or click.prompt("Pairing code", hide_input=True)
    )

    rc = run_web_sync(
        resolved_url,
        resolved_code,
        via=via_host,
        assume_yes=assume_yes,
        prefer=prefer,
        allow_empty=allow_empty,
        dry_run=dry_run,
        force=force,
    )
    if rc != 0:
        raise SystemExit(rc)


@web.command()
@click.option(
    "--deployment",
    "deployment",
    default=None,
    metavar="ID",
    help=(
        "Which cached deployment to report against (deployment id). Required "
        "only when this workstation has pushed to more than one deployment."
    ),
)
def status(deployment: str | None) -> None:
    """Show offline drift between the local registry and the last push.

    Compares the current registry against the non-secret push cache and reports
    which instances are new / changed / removed / in sync since the last
    `remo web push`. Makes ZERO network or SSH connections. Exits 0 (informational,
    even when drift exists); exits 1 only when more than one deployment is cached
    and no --deployment selector was given.
    """
    # core-only imports (works without the `web` extra).
    from remo_cli.core.known_hosts import get_known_hosts  # noqa: PLC0415
    from remo_cli.core.output import print_error, print_info, print_success  # noqa: PLC0415
    from remo_cli.core.web_adopt import load_push_cache  # noqa: PLC0415
    from remo_cli.core.web_drift import (  # noqa: PLC0415
        DriftError,
        build_drift_report,
        render_drift,
        select_deployment,
    )

    cache = load_push_cache()
    if not cache:
        print_info(
            "No prior push recorded from this workstation — nothing to compare. "
            "Run `remo web push <url>` to adopt/sync a deployment first."
        )
        return

    try:
        deployment_id = select_deployment(cache, deployment)
    except DriftError as e:
        print_error(str(e))
        raise SystemExit(1) from e

    report = build_drift_report(deployment_id, cache, get_known_hosts())
    render_drift(report)
    if report.is_in_sync:
        print_success("In sync — nothing to push.")
