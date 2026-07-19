"""FastAPI application factory for the Remo web service.

This module (and everything under ``remo_cli.web``) is only ever imported
lazily, from inside `remo_cli.cli.web` command bodies — see that module for
the NFR-008 lazy-import boundary. Because of that boundary, importing
FastAPI/Starlette at module level here is expected and safe: by the time
this module is imported, the caller has already confirmed the `web` extra is
installed.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from remo_cli.web.api.hosts import router as hosts_router
from remo_cli.web.api.pairing import router as pairing_router
from remo_cli.web.api.setup import router as setup_router
from remo_cli.web.api.terminals import router as terminals_router
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import DiscoveryService
from remo_cli.web.health import router as health_router
from remo_cli.web.logging_config import configure_logging
from remo_cli.web.operator_auth import build_operator_auth_provider
from remo_cli.web.pairing import PairingSessionManager
from remo_cli.web.ssh_master import stale_socket_cleanup
from remo_cli.web.state import (
    ConfigurationState,
    ServiceIdentityError,
    detect_state,
    ensure_service_identity,
)
from remo_cli.web.terminal_registry import TerminalRegistry

logger = logging.getLogger("remo_cli.web.app")

# Restrictive CSP compatible with the local (same-origin, no-CDN) Ghostty
# WASM renderer and the same-origin terminal WebSocket (FR-038/FR-051), plus
# standard hardening directives with zero functional downside for this app.
# T062 finalization notes:
# - `script-src 'self' 'wasm-unsafe-eval'`: 'self' allows the same-origin JS
#   bundle; 'wasm-unsafe-eval' is required to instantiate the Ghostty WASM
#   module (WebAssembly.instantiate) -- there is no narrower standard token
#   for this.
# - `connect-src 'self'`: deliberately does NOT add a bare `ws:`/`wss:`
#   source. Per the Fetch/CSP spec, a `connect-src` source list without an
#   explicit scheme matches the *scheme of the protected resource* for
#   same-origin, and modern browsers additionally special-case WebSocket
#   connect-src matching to accept `'self'` for a same-origin `ws:`/`wss:`
#   upgrade of the page's own origin (the scheme is normalized away for the
#   comparison). Adding a bare `ws:`/`wss:` token would instead allow a
#   WebSocket to ANY host on that scheme -- strictly more permissive than
#   what this same-origin-only app needs, so it's intentionally omitted.
# - No directive anywhere lists a wildcard (`*`), a bare `http:`/`https:`
#   scheme, or any external host -- FR-038 ("no CDN") and FR-051.
# - `frame-ancestors 'none'`: this app is never meant to be framed by
#   another page (clickjacking hardening).
# - `base-uri 'self'` / `form-action 'self'`: standard hardening restricting
#   `<base>` rewriting and form submission targets to same-origin.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'wasm-unsafe-eval'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# Methods that never carry state-changing intent and so are exempt from the
# Origin allowlist check below (plain navigation / preflight).
_ORIGIN_EXEMPT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def create_app(settings: WebSettings | None = None) -> FastAPI:
    """Build and configure the Remo web service FastAPI application.

    Wires:
    - A ``Host`` header allowlist (`TrustedHostMiddleware`).
    - An ``Origin`` allowlist check for state-changing HTTP requests, plus a
      restrictive ``Content-Security-Policy`` response header. No wildcard
      CORS is ever added (FR-048). The primary Origin check for the terminal
      WebSocket happens at the WS handshake itself (T038); this middleware
      is a first line of defense for ordinary HTTP.
    - The ``/api/v1/health`` and ``/api/v1/ready`` routes.
    - The built frontend SPA, served same-origin, when it has been built.

    The `terminals` router is mounted by a later task (T038) once
    `remo_cli.web.api.terminals` exists.
    """
    settings = settings or WebSettings()

    # Defense-in-depth log redaction (FR-028/T055) -- see logging_config.py's
    # module docstring for what this does and does not guarantee.
    configure_logging()

    # --- Ephemeral pairing (012-web-adopt-pairing) ------------------------
    # Construct the operator-auth provider FAIL-FAST at app-build time (FR-009):
    # forward auth without a header name raises here and aborts `remo web serve`
    # before the socket is bound. `None` means minting is disabled (fail closed).
    operator_auth_provider = build_operator_auth_provider(settings)
    if os.environ.get("REMO_WEB_API_TOKEN"):
        # 011's static token gate is removed (FR-021); a stale value is inert.
        logger.info(
            "REMO_WEB_API_TOKEN is set but is now ignored; the setup surface is "
            "authorized by ephemeral pairing codes (see docs)."
        )
    if operator_auth_provider is None:
        logger.info(
            "operator authentication is not configured: pairing-code minting is "
            "disabled. Set REMO_WEB_OPERATOR_AUTH=forward (with "
            "REMO_WEB_FORWARD_AUTH_HEADER) for a proxy front door, or =none for "
            "the network-restricted posture."
        )
    elif operator_auth_provider.posture == "network-restricted":
        # FR-013: the weaker posture is never entered silently.
        logger.warning(
            "operator authentication is in the NETWORK-RESTRICTED posture: "
            "pairing codes are minted WITHOUT operator authentication. Use this "
            "only for loopback/private deployments; put a forward-auth proxy in "
            "front for anything reachable by others."
        )
    else:
        logger.info(
            "operator authentication: forward auth (trusted header %r).",
            settings.forward_auth_header,
        )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Startup (011-web-adopt T030/FR-002, research R3): mint the service
        # identity the first time the service boots unconfigured. Gated on
        # detect_state so a read-only REMO_HOME (mount_configured) is never
        # written to, and an existing/damaged keypair is never regenerated
        # (ensure_service_identity loads a complete pair as-is and refuses a
        # half-pair by design). A generation failure is logged, not fatal:
        # the service must still reach its running unconfigured state
        # (FR-001); the setup API retries generation on demand.
        if detect_state(settings) is ConfigurationState.UNCONFIGURED:
            try:
                ensure_service_identity(settings)
            except (ServiceIdentityError, OSError):
                logger.exception("service identity generation failed at startup")

        # Startup: remove ControlMaster sockets left by a previously crashed
        # process (T035); the next attachment re-establishes a master.
        stale_socket_cleanup(settings.ssh_control_dir)

        # Kick off an initial discovery so the cache is populated shortly after
        # boot -- GET /hosts and GET /sessions only READ the cache, so without
        # this the dashboard shows an empty registry until a client explicitly
        # POSTs /discovery/refresh (or clicks "Refresh"). Fire-and-forget: never
        # block startup on SSH round-trips (a slow/unreachable instance must not
        # delay readiness). refresh() isolates per-host failures itself
        # (FR-006); the done-callback just drains any unexpected exception so it
        # isn't reported as "never retrieved".
        initial_discovery = asyncio.create_task(app.state.discovery_service.refresh())
        initial_discovery.add_done_callback(lambda t: t.cancelled() or t.exception())
        app.state.initial_discovery_task = initial_discovery

        yield

        if not initial_discovery.done():
            initial_discovery.cancel()
        # Shutdown (NFR-007/SC-014): flip `shutting_down` BEFORE reaping, so
        # `POST /terminals` (web/api/terminals.py) starts rejecting new
        # terminal creation the instant shutdown begins -- otherwise a
        # request landing between "shutdown started" and "attachments
        # reaped" could create an attachment that close_all() below would
        # never see. Reaping itself only tears down the local ssh/PTY
        # process group per attachment; remote Zellij sessions are left
        # running (FR-019).
        app.state.shutting_down = True
        await app.state.terminal_registry.close_all()

    app = FastAPI(title="Remo Web Session Interface", lifespan=_lifespan)
    app.state.settings = settings
    app.state.shutting_down = False
    app.state.discovery_service = DiscoveryService(settings)
    app.state.terminal_registry = TerminalRegistry(settings)
    app.state.pairing_manager = PairingSessionManager(ttl_s=settings.pairing_ttl_s)
    app.state.operator_auth_provider = operator_auth_provider

    # --- Host allowlist (FR-048) -----------------------------------------
    # settings.allowed_hosts is never empty (WebSettings defaults to
    # ["127.0.0.1", "localhost"]), so this always has a real allowlist —
    # never a wildcard.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    # --- Origin allowlist (state-changing requests) + CSP ------------------
    @app.middleware("http")
    async def _origin_allowlist_and_csp(request: Request, call_next):  # noqa: ANN001, ANN202
        if request.method not in _ORIGIN_EXEMPT_METHODS:
            origin = request.headers.get("origin")
            # The origin allowlist is a browser-CSRF defense. The setup API is
            # bearer-token-only (no ambient credentials), and a cross-origin
            # browser request cannot attach an Authorization header without a
            # CORS preflight this app never grants — while a genuine browser
            # CSRF attempt always carries an Origin header. So Origin-less
            # requests to /api/v1/setup/* (the `remo web adopt` CLI, including
            # --via tunnels whose 127.0.0.1:<random-port> origin could never
            # be allowlisted) are exempt; a present-but-disallowed Origin is
            # still rejected.
            is_originless_setup = origin is None and request.url.path.startswith(
                "/api/v1/setup"
            )
            if not is_originless_setup and (
                origin is None or origin not in settings.allowed_origins
            ):
                rejection = JSONResponse(
                    status_code=403,
                    content={
                        "error": {
                            "code": "forbidden_origin",
                            "message": "Origin missing or not allowed.",
                            "retryable": False,
                            "remediation": "Access the app from an allowed origin.",
                        }
                    },
                )
                # Every HTTP response carries the CSP, including this
                # early-return rejection -- not just the happy path below.
                rejection.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
                return rejection

        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
        return response

    # --- API routers --------------------------------------------------------
    app.include_router(health_router, prefix="/api/v1")
    app.include_router(hosts_router, prefix="/api/v1")
    app.include_router(terminals_router, prefix="/api/v1")
    app.include_router(setup_router, prefix="/api/v1")
    app.include_router(pairing_router, prefix="/api/v1")

    # --- Same-origin frontend static files (FR-038, no CDN) -----------------
    # The built frontend won't exist until the Docker image build stage (or a
    # local `npm run build`) has run; guard so app creation never fails in
    # dev/test environments where frontend/dist is absent.
    frontend_dist = Path(settings.frontend_dist_dir)
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app
