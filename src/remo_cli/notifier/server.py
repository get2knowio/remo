"""FastAPI app for the notifier.

Holds the caller open until a decision exists, mapping outcomes through one
fail-secure resolver: only an authorized human Approve yields ``allow``; every
other terminal state is deny or a 5xx (FR-008, research R4). See
contracts/openapi.yaml.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from remo_cli.notifier import __version__
from remo_cli.notifier.config import NotifierConfig
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    Decision,
    ErrorResponse,
    HealthResponse,
)
from remo_cli.notifier.grants import GrantStore
from remo_cli.notifier.state import PendingApprovals, RegisterError, RegistrationFailed
from remo_cli.notifier.transports.base import NotificationTransport

_log = get_logger("remo_notifier.server")


def _op_summary(request: ApprovalRequest) -> str:
    """Redacted one-line op descriptor for audit (no args/bodies/secrets/paths)."""
    op = request.operation
    if op.kind.value == "command":
        return f"command:{op.command or '?'}"
    if op.kind.value == "network":
        return f"network:{op.remote_host or '?'}:{op.remote_port or '?'}"
    if op.kind.value == "file":
        return "file"  # path withheld (FR-017)
    return op.kind.value


def _clamp_timeout(requested: int | None, *, default: int, maximum: int) -> int:
    value = requested if requested is not None else default
    return max(1, min(value, maximum))


def _err(status_code: int, error: str, detail: str = "", approval_id: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail, approval_id=approval_id)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(config: NotifierConfig, transport: NotificationTransport) -> FastAPI:
    """Build the FastAPI app wired to ``transport`` and a fresh registry."""
    registry = PendingApprovals(max_pending=config.approval.max_pending_approvals)
    grant_store = GrantStore(
        max_grants=config.grants.max_grants,
        instance_id=config.instance.id,
        allow_global_scope=config.grants.allow_global_scope,
    )
    # The server owns the auto-approval counter for the digest (research RG6/U1).
    state: dict[str, object] = {
        "shutting_down": False,
        "start_time": time.monotonic(),
        "auto_approvals_since_digest": 0,
    }

    # Let a grant-aware transport (Telegram) reach the store for proposals,
    # creation, and /rules /revoke /pause. Other transports skip this.
    if config.grants.enabled and hasattr(transport, "bind_grants"):
        transport.bind_grants(grant_store, default_ttl_seconds=config.grants.default_ttl_seconds)

    async def _sweeper() -> None:
        while True:
            await asyncio.sleep(60)
            grant_store.sweep()

    async def _digester(interval: int) -> None:
        while True:
            await asyncio.sleep(interval)
            n = int(state["auto_approvals_since_digest"])  # type: ignore[call-overload]
            if n > 0 and hasattr(transport, "send_digest"):
                state["auto_approvals_since_digest"] = 0
                try:
                    await transport.send_digest(
                        f"Auto-approved {n} operation(s) via standing grants "
                        f"({grant_store.count()} active)."
                    )
                except Exception:  # noqa: BLE001 - digest is best-effort
                    _log.debug("digest_send_failed")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state["start_time"] = time.monotonic()
        try:
            await transport.start()
        except Exception as exc:  # noqa: BLE001
            # A transport that can't start (bad token, no network) must not take
            # down liveness: /v1/health stays up; /v1/approve returns 503 while
            # the transport reports unhealthy (FR-007).
            _log.error("transport_start_failed", error=str(exc), transport=transport.name)
        tasks = [asyncio.create_task(_sweeper())] if config.grants.enabled else []
        if config.grants.enabled and config.grants.digest_interval_seconds > 0:
            tasks.append(asyncio.create_task(_digester(config.grants.digest_interval_seconds)))
        try:
            yield
        finally:
            state["shutting_down"] = True
            for t in tasks:
                t.cancel()
            registry.drain(
                ApprovalDecision(
                    decision=Decision.deny, responder="system:shutdown", reason="shutdown"
                )
            )
            await transport.stop()

    app = FastAPI(title="remo-notifier", version=__version__, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FR-001: schema validation failure -> 400 (not FastAPI's default 422).
        return _err(400, "validation_error", detail=str(exc.errors()))

    @app.post("/v1/approve")
    async def approve(request: ApprovalRequest) -> JSONResponse:
        # Standing-grant short-circuit (Addendum 001, FR-G1): before anything
        # else, auto-approve a matching class with no notification and no slot.
        if config.grants.enabled and not grant_store.paused:
            sc_started = time.monotonic()
            matched = grant_store.match(request)
            if matched is not None:
                state["auto_approvals_since_digest"] = int(state["auto_approvals_since_digest"]) + 1  # type: ignore[call-overload]
                _log.info(
                    "auto_approved",
                    approval_id=request.approval_id or "(generated)",
                    grant_id=matched.grant_id,
                    kind=request.operation.kind.value,
                    summary=_op_summary(request),
                    latency_ms=int((time.monotonic() - sc_started) * 1000),
                )
                resp = ApprovalResponse(
                    approval_id=request.approval_id or str(uuid.uuid4()),
                    decision=Decision.allow,
                    responder=f"rule:{matched.grant_id}",
                    reason="auto-approved via standing grant",
                    decided_at=datetime.now(timezone.utc),
                    latency_ms=int((time.monotonic() - sc_started) * 1000),
                    grant_id=matched.grant_id,
                )
                return JSONResponse(status_code=200, content=resp.model_dump(mode="json"))

        if state["shutting_down"] or not await transport.healthy():
            return _err(503, "unavailable", detail="notifier not ready")

        approval_id = request.approval_id or str(uuid.uuid4())
        effective_timeout = _clamp_timeout(
            request.timeout_seconds,
            default=config.approval.default_timeout_seconds,
            maximum=config.approval.max_timeout_seconds,
        )
        # Normalize so the transport renders the effective values.
        request.approval_id = approval_id
        request.timeout_seconds = effective_timeout

        try:
            entry = await registry.reserve(approval_id, request)
        except RegistrationFailed as exc:
            if exc.reason is RegisterError.duplicate:
                return _err(409, "duplicate_approval_id", approval_id=approval_id)
            return _err(503, "unavailable", detail="at capacity", approval_id=approval_id)

        started = time.monotonic()

        def _on_response(decision: ApprovalDecision) -> None:
            registry.resolve(approval_id, decision)

        try:
            await transport.send_approval_request(request, on_response=_on_response)
        except Exception:  # noqa: BLE001 - any send failure is fail-secure 503 (FR-010a)
            await registry.release(approval_id)
            _log.info("send_failed", approval_id=approval_id)
            return _err(503, "unavailable", detail="notification delivery failed", approval_id=approval_id)

        # Await the reserved entry's future directly: resolve() pops the entry
        # from the registry but the future object stays valid, so this works
        # even if the decision arrived synchronously during send.
        try:
            decision = await asyncio.wait_for(entry.future, timeout=effective_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            registry.discard(approval_id)
            await transport.cancel(approval_id, outcome="timeout")
            latency_ms = int((time.monotonic() - started) * 1000)
            timeout_resp = ApprovalResponse(
                approval_id=approval_id,
                decision=Decision.deny,
                responder="system:timeout",
                reason="timeout",
                decided_at=datetime.now(timezone.utc),
                latency_ms=latency_ms,
            )
            return JSONResponse(status_code=408, content=timeout_resp.model_dump(mode="json"))

        latency_ms = int((time.monotonic() - started) * 1000)
        resp = ApprovalResponse(
            approval_id=approval_id,
            decision=decision.decision,
            responder=decision.responder,
            reason=decision.reason,
            decided_at=decision.decided_at,
            latency_ms=latency_ms,
            grant_id=decision.grant_id,  # set when the human chose "Always"
        )
        return JSONResponse(status_code=200, content=resp.model_dump(mode="json"))

    @app.get("/v1/health")
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            transport=transport.name,
            uptime_seconds=int(time.monotonic() - float(state["start_time"])),  # type: ignore[arg-type]
            pending_approvals=registry.count(),
        )

    app.state.registry = registry  # exposed for tests
    app.state.grant_store = grant_store  # exposed for tests
    return app
