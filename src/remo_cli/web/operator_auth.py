"""Operator-authentication provider seam (012-web-adopt-pairing).

Minting a pairing code is gated by operator authentication (FR-009): the
service only mints for a request that carries proof the operator is logged in.
v1 ships **forward auth** — the app platform terminates SSO and injects a
trusted identity header, and remo-web reads that header. The gate is a small
pluggable seam so a future in-app **OIDC** verifier (JWKS + iss/aud/exp) can be
added without touching the pairing core (FR-010); OIDC is deferred (Out of
Scope).

Configuration (WebSettings / `REMO_WEB_*`):

    OPERATOR_AUTH=forward + FORWARD_AUTH_HEADER=<name>  -> ForwardAuthProvider
    OPERATOR_AUTH=none                                  -> NetworkRestrictedProvider
    OPERATOR_AUTH=""  (unset)                           -> None (minting disabled)

`build_operator_auth_provider` fail-fasts (FR-009) if forward auth is enabled
without a header name, so the service never trusts a header the proxy does not
set and strip.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import Request

    from remo_cli.web.config import WebSettings


class OperatorAuthConfigError(Exception):
    """Fail-fast configuration error (e.g. forward auth without a header name)."""


@dataclass(frozen=True)
class OperatorIdentity:
    """The authenticated operator that minted a pairing session (FR-012)."""

    subject: str
    provider: str


@runtime_checkable
class OperatorAuthProvider(Protocol):
    """Authenticates a mint request. Returns an identity, or None to refuse."""

    #: Human-readable posture name surfaced in readiness/diagnostics (FR-013).
    posture: str

    def authenticate(self, request: "Request") -> OperatorIdentity | None: ...


class ForwardAuthProvider:
    """Trusts a proxy-injected identity header (FR-009/FR-014).

    The header is trusted only under the documented forward-auth boundary: a
    proxy in front sets/strips it and prevents direct client access to the app.
    """

    posture = "forward"

    def __init__(self, header_name: str) -> None:
        if not header_name.strip():
            raise OperatorAuthConfigError(
                "forward auth requires REMO_WEB_FORWARD_AUTH_HEADER to name the "
                "trusted, proxy-injected identity header (e.g. X-Forwarded-User)."
            )
        self.header_name = header_name.strip()

    def authenticate(self, request: "Request") -> OperatorIdentity | None:
        value = request.headers.get(self.header_name, "").strip()
        if not value:
            return None
        return OperatorIdentity(subject=value, provider="forward")


class NetworkRestrictedProvider:
    """No operator-auth provider — mint proceeds without a credential (FR-013).

    An explicit, loudly-logged opt-in for loopback/private/dev deployments; the
    minting request is treated as an anonymous operator.
    """

    posture = "network-restricted"

    def authenticate(self, request: "Request") -> OperatorIdentity | None:
        return OperatorIdentity(subject="network-restricted", provider="network-restricted")


def build_operator_auth_provider(settings: "WebSettings") -> OperatorAuthProvider | None:
    """Construct the configured provider, or None when minting is disabled.

    Fail-fasts (raises ``OperatorAuthConfigError``) when forward auth is enabled
    without a header name (FR-009). ``None`` means no provider is configured:
    the mint endpoint refuses with a "not configured" 403 (fail closed, R5).
    """
    mode = settings.operator_auth.strip().lower()
    if mode == "forward":
        return ForwardAuthProvider(settings.forward_auth_header)
    if mode == "none":
        return NetworkRestrictedProvider()
    if mode == "":
        return None
    raise OperatorAuthConfigError(
        f"REMO_WEB_OPERATOR_AUTH={settings.operator_auth!r} is not recognized "
        "(expected 'forward', 'none', or unset)."
    )
