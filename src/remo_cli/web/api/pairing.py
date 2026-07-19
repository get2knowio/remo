"""Pairing control-plane router (`/api/v1/pairing/*`, 012-web-adopt-pairing).

These routes live OUTSIDE the dormant `/api/v1/setup/*` router so they are
reachable while the setup surface is dormant (contracts/pairing-api.md). They
are browser-facing (`POST` from the SPA's own origin) and therefore subject to
the existing Origin allowlist middleware — the CLI never calls them.

- ``POST /pairing/mint`` — forward-auth gated (FR-009/FR-011). Rotates + creates
  the live session and returns ``{code, expires_in}`` with ``Cache-Control:
  no-store``. The code is the only thing ever transmitted from the server; it is
  never embedded in the served bundle (FR-016). Refused (403) when the operator
  is not authenticated, or when no provider is configured (fail closed).
- ``POST /pairing/end`` — best-effort session end for the page-hide beacon
  (FR-004). Unauthenticated and idempotent; always ``204``. The idle TTL is the
  authoritative backstop.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from remo_cli.web.operator_auth import OperatorAuthProvider
from remo_cli.web.pairing import PairingOrigin, PairingSessionManager

logger = logging.getLogger("remo_cli.web.pairing")

router = APIRouter(prefix="/pairing")


def _manager(request: Request) -> PairingSessionManager:
    return request.app.state.pairing_manager


def _provider(request: Request) -> OperatorAuthProvider | None:
    return getattr(request.app.state, "operator_auth_provider", None)


@router.post("/mint")
def mint(request: Request) -> Response:
    """Mint a fresh pairing code (rotation-on-open, FR-003), operator-auth gated."""
    provider = _provider(request)
    if provider is None:
        # No operator-auth provider configured -> minting disabled (fail closed).
        client = request.client.host if request.client else "unknown"
        logger.warning("pairing mint refused from %s: operator auth not configured", client)
        return JSONResponse(
            status_code=403, content={"detail": "operator authentication not configured"}
        )

    identity = provider.authenticate(request)
    if identity is None:
        # Authenticated proof absent -> refuse, create no session (FR-011).
        client = request.client.host if request.client else "unknown"
        logger.warning(
            "pairing mint refused from %s: operator authentication required (%s)",
            client,
            provider.posture,
        )
        return JSONResponse(
            status_code=403, content={"detail": "operator authentication required"}
        )

    origin_raw = request.query_params.get("origin", "adopt")
    origin: PairingOrigin = "resync" if origin_raw == "resync" else "adopt"
    code, ttl_s = _manager(request).mint(identity, origin)
    # Record WHO minted (FR-012) — never the code (FR-016).
    logger.info("pairing code minted for %s (origin=%s)", identity.subject, origin)

    response = JSONResponse(content={"code": code, "expires_in": int(ttl_s)})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/end")
def end(request: Request) -> Response:
    """Best-effort end of the live pairing session (page-hide beacon, FR-004)."""
    _manager(request).end()
    return Response(status_code=204)
