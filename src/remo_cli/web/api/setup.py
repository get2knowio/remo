"""Token-gated setup API router (`/api/v1/setup/*`, 011-web-adopt T008).

Authentication contract (contracts/setup-api.md, FR-020/FR-021/FR-024),
enforced for every route on this router by the `require_setup_token`
dependency:

- token NOT configured (``REMO_WEB_API_TOKEN`` unset/empty) -> ``404`` on
  every setup route. Fail closed: the surface is disabled and the response
  is indistinguishable from an unknown route (same body FastAPI returns for
  a path that does not exist).
- token configured + correct ``Authorization: Bearer <token>`` -> the route
  handles the request. Comparison is constant-time (`hmac.compare_digest`).
- token configured + missing/wrong header -> ``401 {"detail":
  "unauthorized"}`` with no further detail; the attempt is logged WITHOUT
  the presented credential.

This module is the scaffold only: the business endpoints (``GET /status``,
``GET /identity``, ``PUT /registry``, ``POST /verify`` -- see
contracts/setup-api.md) land in later tasks as ordinary ``@router.<verb>``
handlers below and inherit the router-level dependency automatically.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from remo_cli.web.config import WebSettings

logger = logging.getLogger("remo_cli.web.setup")


def _get_settings(request: Request) -> WebSettings:
    """The app-wide `WebSettings` (set in `create_app()`), like health.py."""
    return getattr(request.app.state, "settings", None) or WebSettings()


async def require_setup_token(request: Request) -> None:
    """Bearer-token gate shared by every setup route (research R4)."""
    configured = _get_settings(request).api_token.strip()
    if not configured:
        # No token configured: the setup surface does not exist. Mirror
        # FastAPI's default unknown-route response exactly (FR-021).
        raise HTTPException(status_code=404, detail="Not Found")

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() == "bearer" and hmac.compare_digest(
        presented.strip().encode(), configured.encode()
    ):
        return

    # Log the failure, never the presented credential (FR-024).
    client = request.client.host if request.client else "unknown"
    logger.warning("setup API authentication failure from %s on %s", client, request.url.path)
    raise HTTPException(status_code=401, detail="unauthorized")


router = APIRouter(prefix="/setup", dependencies=[Depends(require_setup_token)])
