"""Shared dormant-404 gating for flag-guarded admin routers.

One implementation of the setup-router dormancy precedent (010 FR-005/FR-006)
for every *flag-gated* admin surface (`REMO_WEB_HOST_ADMIN`,
`REMO_WEB_REGISTRY_ADMIN`): off-flag and operator-auth-refused requests both
get a response byte-identical to FastAPI's default unknown-route 404, so a
scanner cannot learn the surface exists. `web/api/setup.py` keeps its own gate
— its credential is a live pairing code, not a config flag — but shares
:func:`dormant` so the 404 shape has a single definition.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request

from remo_cli.web.config import WebSettings


def dormant() -> HTTPException:
    """The dormant response — byte-identical to FastAPI's default unknown-route
    404. A fresh instance per raise (never a shared singleton, which would
    accumulate traceback/context state)."""
    return HTTPException(status_code=404, detail="Not Found")


def require_admin_flag(
    is_enabled: Callable[[WebSettings], bool],
    *,
    surface: str,
    logger: logging.Logger,
) -> Callable[[Request], Awaitable[None]]:
    """Build a router-wide dependency gating an admin surface behind a flag.

    Dormant ``404`` unless *is_enabled(settings)*. When an operator-auth
    provider is configured, a request the provider refuses gets the SAME 404 —
    never a distinguishable 401/403 that would reveal the surface exists.
    *surface* names the router in the refused-auth warning (e.g.
    ``"host-admin"``).
    """

    async def _require(request: Request) -> None:
        settings = getattr(request.app.state, "settings", None) or WebSettings()
        if not is_enabled(settings):
            raise dormant()

        provider = getattr(request.app.state, "operator_auth_provider", None)
        if provider is not None and provider.authenticate(request) is None:
            client = request.client.host if request.client else "unknown"
            logger.warning(
                "%s request without operator authentication from %s: %s %s",
                surface,
                client,
                request.method,
                request.url.path,
            )
            raise dormant()

    return _require
