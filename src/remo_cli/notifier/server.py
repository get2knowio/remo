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
from remo_cli.notifier.state import PendingApprovals, RegisterError, RegistrationFailed
from remo_cli.notifier.transports.base import NotificationTransport

_log = get_logger("remo_notifier.server")


def _clamp_timeout(requested: int | None, *, default: int, maximum: int) -> int:
    value = requested if requested is not None else default
    return max(1, min(value, maximum))


def _err(status_code: int, error: str, detail: str = "", approval_id: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail, approval_id=approval_id)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(config: NotifierConfig, transport: NotificationTransport) -> FastAPI:
    """Build the FastAPI app wired to ``transport`` and a fresh registry."""
    registry = PendingApprovals(max_pending=config.approval.max_pending_approvals)
    state: dict[str, object] = {"shutting_down": False, "start_time": time.monotonic()}

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
        try:
            yield
        finally:
            state["shutting_down"] = True
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
    return app
