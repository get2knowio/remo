"""FastAPI app for the notifier (spec 008, agentsh approver model).

The notifier polls agentsh for pending approvals, delivers each through the
active channel, and resolves the human's decision back to agentsh. One
fail-secure resolver governs outcomes: only an authorized human Approve yields
``allow``; every other terminal state is deny (FR-007/FR-008). The human's
decision always flows human → channel → notifier → agentsh.
See contracts/agentsh-integration.md.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from remo_cli.notifier import __version__
from remo_cli.notifier.agentsh_client import AgentshClient, AgentshError
from remo_cli.notifier.config import NotifierConfig
from remo_cli.notifier.grants import GrantStore
from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import (
    AgentshRequest,
    ApprovalDecision,
    ApprovalResponse,
    Decision,
    ErrorResponse,
    HealthResponse,
)
from remo_cli.notifier.state import PendingApprovals, RegisterError, RegistrationFailed
from remo_cli.notifier.transports.base import NotificationTransport

_log = get_logger("remo_notifier.server")


class _DeliveryError(Exception):
    """Notification could not be delivered (channel send failed)."""


def _clamp_timeout(requested: int | None, *, default: int, maximum: int) -> int:
    value = requested if requested is not None else default
    return max(1, min(value, maximum))


def _err(status_code: int, error: str, detail: str = "", approval_id: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail, approval_id=approval_id)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def create_app(
    config: NotifierConfig,
    transport: NotificationTransport,
    agentsh: AgentshClient | None = None,
) -> FastAPI:
    """Build the FastAPI app wired to ``transport``, ``agentsh``, and a registry.

    ``agentsh`` is optional so tests can exercise delivery (the local ``/v1/test``
    injection) without a live agentsh; when None, the poll loop does not run.
    """
    registry = PendingApprovals(max_pending=config.approval.max_pending_approvals)
    grant_store = GrantStore(
        max_grants=config.grants.max_grants,
        instance_id=config.instance.id,
        allow_global_scope=config.grants.allow_global_scope,
    )
    state: dict[str, object] = {
        "shutting_down": False,
        "start_time": time.monotonic(),
        "auto_approvals_since_digest": 0,
        "agentsh_connected": False,
    }
    inflight: set[str] = set()
    poll_event = asyncio.Event()

    if config.grants.enabled and hasattr(transport, "bind_grants"):
        transport.bind_grants(grant_store, default_ttl_seconds=config.grants.default_ttl_seconds)

    def _timeout_for(request: AgentshRequest) -> int:
        if request.expires_at is not None:
            now = datetime.now(timezone.utc)
            secs = int((request.expires_at - now).total_seconds())
            return max(1, min(secs, config.approval.max_timeout_seconds))
        return config.approval.default_timeout_seconds

    async def _resolve_agentsh(approval_id: str, decision: Decision, *, reason: str) -> None:
        if agentsh is None:
            return
        try:
            await agentsh.resolve(approval_id, decision, reason=reason)
        except AgentshError as exc:
            # Fail-secure: agentsh's own ExpiresAt denies if we never succeed.
            _log.error("agentsh_resolve_failed", approval_id=approval_id, error=str(exc))

    async def _deliver(request: AgentshRequest) -> ApprovalResponse:
        """Reserve a slot, deliver, and await the human's decision.

        Raises RegistrationFailed (duplicate/capacity) or _DeliveryError (send
        failed). Returns a fail-secure deny ApprovalResponse on timeout.
        """
        approval_id = request.id
        entry = await registry.reserve(approval_id, request)
        started = time.monotonic()

        def _on_response(decision: ApprovalDecision) -> None:
            registry.resolve(approval_id, decision)

        try:
            await transport.send_approval_request(request, on_response=_on_response)
        except Exception as exc:  # noqa: BLE001 - any send failure is fail-secure (FR-008)
            await registry.release(approval_id)
            _log.info("send_failed", approval_id=approval_id)
            raise _DeliveryError(str(exc)) from exc

        timeout = _timeout_for(request)
        try:
            decision = await asyncio.wait_for(entry.future, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            registry.discard(approval_id)
            await transport.cancel(approval_id, outcome="timeout")
            return ApprovalResponse(
                approval_id=approval_id,
                decision=Decision.deny,
                responder="system:timeout",
                reason="timeout",
                decided_at=datetime.now(timezone.utc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return ApprovalResponse(
            approval_id=approval_id,
            decision=decision.decision,
            responder=decision.responder,
            reason=decision.reason,
            decided_at=decision.decided_at,
            latency_ms=int((time.monotonic() - started) * 1000),
            grant_id=decision.grant_id,
        )

    async def _handle(request: AgentshRequest) -> None:
        """Process one polled agentsh approval end-to-end (poll-loop task)."""
        approval_id = request.id
        try:
            # Standing-grant short-circuit (Addendum 001): auto-approve a matching
            # class with no notification, resolving directly to agentsh.
            if config.grants.enabled and not grant_store.paused:
                matched = grant_store.match(request)
                if matched is not None:
                    state["auto_approvals_since_digest"] = int(state["auto_approvals_since_digest"]) + 1  # type: ignore[call-overload]
                    _log.info(
                        "auto_approved",
                        approval_id=approval_id,
                        grant_id=matched.grant_id,
                        kind=request.kind,
                    )
                    await _resolve_agentsh(approval_id, Decision.allow, reason="standing grant")
                    return

            if state["shutting_down"] or not await transport.healthy():
                return  # leave it pending in agentsh; a later poll retries

            try:
                resp = await _deliver(request)
            except RegistrationFailed:
                return
            except _DeliveryError:
                return
            await _resolve_agentsh(approval_id, resp.decision, reason=resp.reason)
        finally:
            inflight.discard(approval_id)

    async def _poll_loop() -> None:
        interval = config.agentsh.poll_interval_seconds
        while not state["shutting_down"]:
            assert agentsh is not None
            try:
                requests = await agentsh.poll()
                state["agentsh_connected"] = True
            except AgentshError as exc:
                state["agentsh_connected"] = False
                _log.warning("agentsh_poll_failed", error=str(exc))
                requests = []
            for request in requests:
                if request.id in inflight:
                    continue
                inflight.add(request.id)
                asyncio.create_task(_handle(request))
            try:
                await asyncio.wait_for(poll_event.wait(), timeout=interval)
            except (asyncio.TimeoutError, TimeoutError):
                pass
            poll_event.clear()

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
            _log.error("transport_start_failed", error=str(exc), transport=transport.name)
        tasks: list[asyncio.Task] = []
        if agentsh is not None:
            await agentsh.start()
            tasks.append(asyncio.create_task(_poll_loop()))
        if config.grants.enabled:
            tasks.append(asyncio.create_task(_sweeper()))
            if config.grants.digest_interval_seconds > 0:
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
            if agentsh is not None:
                await agentsh.stop()

    app = FastAPI(title="remo-notifier", version=__version__, lifespan=lifespan)

    @app.get("/v1/health")
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            transport=transport.name,
            agentsh_connected=bool(state["agentsh_connected"]),
            uptime_seconds=int(time.monotonic() - float(state["start_time"])),  # type: ignore[arg-type]
            pending_approvals=registry.count(),
        )

    if config.agentsh.webhook_enabled:
        @app.post("/v1/webhook")
        async def webhook(request: Request) -> JSONResponse:
            # Untrusted "poll now" trigger only (agentsh webhook is unsigned and
            # carries no resolvable id). The body is ignored; we just poll.
            poll_event.set()
            return JSONResponse(status_code=202, content={"status": "scheduled"})

    @app.post("/v1/test")
    async def test_approval(request: Request) -> JSONResponse:
        # Local synthetic-approval injection (FR / cli `test`): deliver a
        # test-labeled approval through the installed channel WITHOUT contacting
        # agentsh, and report the human's tap. Never resolves to agentsh.
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        timeout = int(body.get("timeout_seconds") or config.approval.default_timeout_seconds)
        timeout = _clamp_timeout(
            timeout,
            default=config.approval.default_timeout_seconds,
            maximum=config.approval.max_timeout_seconds,
        )
        if state["shutting_down"] or not await transport.healthy():
            return _err(503, "unavailable", detail="notifier not ready")
        synthetic = AgentshRequest(
            id=str(uuid.uuid4()),
            kind="command",
            target="echo wiring-check",
            rule="test",
            message="This is a test approval — tap Approve or Deny to confirm wiring.",
            session_id="remo-notifier-selftest",
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=timeout),
        )
        try:
            resp = await _deliver(synthetic)
        except RegistrationFailed as exc:
            detail = "duplicate" if exc.reason is RegisterError.duplicate else "at capacity"
            return _err(503, "unavailable", detail=detail)
        except _DeliveryError:
            return _err(503, "unavailable", detail="notification delivery failed")
        return JSONResponse(status_code=200, content=resp.model_dump(mode="json"))

    app.state.registry = registry  # exposed for tests
    app.state.grant_store = grant_store  # exposed for tests
    app.state.trigger_poll = poll_event  # exposed for tests
    return app
