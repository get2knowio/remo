"""FastAPI app for the notifier (spec 009, dynamic multi-source registry).

The notifier serves many **sources** concurrently. Each source is one agentsh
approval endpoint (1:1 with a devcontainer); registration is expressed as a
held-open ``POST /v1/sources`` presence connection. One independent poll/resolve
loop runs per source (``SourcePoller``), and the core fans every source's
approvals into the single installed channel — minting a colon-free **delivery
id** per delivery so two sources' approvals never collide in the channel's
callback space, and routing each human decision back to its origin source
(spec 009 R3). One fail-secure resolver governs outcomes: only an authorized
human Approve yields ``allow``; every other terminal state is deny
(FR-007/FR-008). The static ``[agentsh]`` endpoint is retained as an optional
permanent **seed** source. See contracts/source-registration.md.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

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
    SourceRegistration,
)
from remo_cli.notifier.sources.poller import SourcePoller
from remo_cli.notifier.sources.registry import AtCapacity, SourceRegistry
from remo_cli.notifier.sources.source import Source
from remo_cli.notifier.state import (
    DRAINED_RESPONDER,
    PendingApprovals,
    RegisterError,
    RegistrationFailed,
)
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
    *,
    source_client_factory: Callable[[str, str], AgentshClient] | None = None,
) -> FastAPI:
    """Build the FastAPI app wired to ``transport``, a ``SourceRegistry``, and
    (optionally) a seed agentsh client.

    ``agentsh`` is optional: when given it is registered as the permanent seed
    source's client (tests inject a fake); when omitted, a seed is built from
    ``[agentsh]`` config if present. With neither, the registry starts empty and
    serves only dynamic sources. ``source_client_factory`` lets tests inject fake
    per-source agentsh clients for dynamic registrations.
    """
    pending = PendingApprovals(max_pending=config.approval.max_pending_approvals)
    grant_store = GrantStore(
        max_grants=config.grants.max_grants,
        instance_id=config.instance.id,
        allow_global_scope=config.grants.allow_global_scope,
    )
    state: dict[str, object] = {
        "shutting_down": False,
        "start_time": time.monotonic(),
        "auto_approvals_since_digest": 0,
    }

    if config.grants.enabled and hasattr(transport, "bind_grants"):
        transport.bind_grants(grant_store, default_ttl_seconds=config.grants.default_ttl_seconds)

    def _timeout_for(request: AgentshRequest) -> int:
        if request.expires_at is not None:
            now = datetime.now(timezone.utc)
            secs = int((request.expires_at - now).total_seconds())
            return max(1, min(secs, config.approval.max_timeout_seconds))
        return config.approval.default_timeout_seconds

    async def _resolve_agentsh(
        source: Source, agentsh_approval_id: str, decision: Decision, *, reason: str
    ) -> None:
        try:
            await source.client.resolve(agentsh_approval_id, decision, reason=reason)
        except AgentshError as exc:
            # Fail-secure: agentsh's own ExpiresAt denies if we never succeed.
            _log.error(
                "agentsh_resolve_failed",
                source_id=source.source_id,
                approval_id=agentsh_approval_id,
                error=str(exc),
            )

    async def _deliver(
        delivery_id: str,
        request: AgentshRequest,
        *,
        source: Source | None = None,
        agentsh_approval_id: str | None = None,
    ) -> ApprovalResponse:
        """Reserve a slot, deliver, and await the human's decision.

        ``request`` carries the colon-free ``delivery_id`` as its ``id`` so the
        channel's callback space never collides across sources (R3). Raises
        RegistrationFailed / _DeliveryError; returns a fail-secure deny on timeout.
        """
        entry = await pending.reserve(
            delivery_id,
            request,
            source_id=source.source_id if source is not None else None,
            epoch=source.epoch if source is not None else 0,
            agentsh_approval_id=agentsh_approval_id,
        )
        started = time.monotonic()

        def _on_response(decision: ApprovalDecision) -> None:
            pending.resolve(delivery_id, decision)

        try:
            await transport.send_approval_request(request, on_response=_on_response)
        except Exception as exc:  # noqa: BLE001 - any send failure is fail-secure (FR-008)
            await pending.release(delivery_id)
            _log.info("send_failed", delivery_id=delivery_id)
            raise _DeliveryError(str(exc)) from exc

        timeout = _timeout_for(request)
        try:
            decision = await asyncio.wait_for(entry.future, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            pending.discard(delivery_id)
            await transport.cancel(delivery_id, outcome="timeout")
            return ApprovalResponse(
                approval_id=delivery_id,
                decision=Decision.deny,
                responder="system:timeout",
                reason="timeout",
                decided_at=datetime.now(timezone.utc),
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return ApprovalResponse(
            approval_id=delivery_id,
            decision=decision.decision,
            responder=decision.responder,
            reason=decision.reason,
            decided_at=decision.decided_at,
            latency_ms=int((time.monotonic() - started) * 1000),
            grant_id=decision.grant_id,
        )

    async def _dispatch(source: Source, request: AgentshRequest) -> None:
        """Process one polled approval for ``source`` end-to-end (poller task).

        Source-scoped by construction: the resolve goes back to *this* source's
        agentsh via its own key, so decisions never cross-route (FR-002).
        """
        agentsh_approval_id = request.id

        # Standing-grant short-circuit (Addendum 001): auto-approve a matching
        # class with no notification, resolving directly to the source's agentsh.
        if config.grants.enabled and not grant_store.paused:
            matched = grant_store.match(request)
            if matched is not None:
                state["auto_approvals_since_digest"] = int(state["auto_approvals_since_digest"]) + 1  # type: ignore[call-overload]
                _log.info(
                    "auto_approved",
                    source_id=source.source_id,
                    approval_id=agentsh_approval_id,
                    grant_id=matched.grant_id,
                    kind=request.kind,
                )
                await _resolve_agentsh(
                    source, agentsh_approval_id, Decision.allow, reason="standing grant"
                )
                return

        if state["shutting_down"] or not await transport.healthy():
            return  # leave it pending in agentsh; a later poll retries

        delivery_id = uuid.uuid4().hex  # colon-free, channel-safe (R3)
        delivered = request.model_copy(update={"id": delivery_id})
        try:
            resp = await _deliver(
                delivery_id, delivered, source=source, agentsh_approval_id=agentsh_approval_id
            )
        except RegistrationFailed:
            return
        except _DeliveryError:
            return
        # If the source was removed mid-flight, drain already issued the local
        # deny + best-effort wire deny — don't double-resolve (R9).
        if resp.responder == DRAINED_RESPONDER:
            return
        await _resolve_agentsh(source, agentsh_approval_id, resp.decision, reason=resp.reason)

    def _poller_factory(source: Source) -> SourcePoller:
        if source.permanent and config.agentsh is not None:
            base = config.agentsh.poll_interval_seconds  # seed keeps 008 cadence (R7)
        else:
            base = config.sources.poll_base_interval_seconds
        return SourcePoller(
            source,
            dispatch=_dispatch,
            base_interval=base,
            backoff_factor=config.sources.poll_backoff_factor,
            backoff_cap=config.sources.poll_backoff_cap_seconds,
            backoff_jitter=config.sources.poll_backoff_jitter,
        )

    sources = SourceRegistry(
        max_sources=config.sources.max_sources,
        pending=pending,
        poller_factory=_poller_factory,
        client_factory=source_client_factory,
    )

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

        # Optional permanent seed source (FR-005/R7): the injected client wins;
        # otherwise build one from [agentsh] config if present.
        seed_client: AgentshClient | None = agentsh
        if seed_client is None and config.agentsh is not None:
            try:
                seed_client = AgentshClient(
                    api_url=config.agentsh.api_url, api_key=config.agentsh.read_api_key()
                )
            except (ValueError, OSError) as exc:
                _log.error("seed_key_read_failed", error=str(exc))
                seed_client = None
        if seed_client is not None:
            seed_id = config.agentsh.source_id if config.agentsh is not None else "seed"
            try:
                await sources.add_seed(seed_id, seed_client)
            except Exception as exc:  # noqa: BLE001 - a bad seed must never block the app
                _log.error("seed_register_failed", error=str(exc))

        tasks: list[asyncio.Task] = []
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
            await sources.drain_all()
            pending.drain(
                ApprovalDecision(
                    decision=Decision.deny, responder="system:shutdown", reason="shutdown"
                )
            )
            await transport.stop()

    app = FastAPI(title="remo-notifier", version=__version__, lifespan=lifespan)

    @app.get("/v1/health")
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=__version__,
            transport=transport.name,
            agentsh_connected=sources.any_polling(),
            uptime_seconds=int(time.monotonic() - float(state["start_time"])),  # type: ignore[arg-type]
            pending_approvals=pending.count(),
            sources=sources.count(),
        )

    @app.post("/v1/sources")
    async def register_source(request: Request):
        """Register a source and hold its presence connection open (FR-006).

        The open stream *is* the registration; its drop de-registers the source.
        """
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return _err(400, "bad_request", detail="invalid JSON body")
        try:
            reg = SourceRegistration.model_validate(body)
        except ValidationError as exc:
            return _err(400, "bad_request", detail=str(exc))
        try:
            source = await sources.register(reg)
        except AtCapacity as exc:
            _log.warning(
                "source_rejected_at_capacity",
                source_id=reg.source_id,
                max_sources=exc.max_sources,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": "at_capacity",
                    "detail": str(exc),
                    "max_sources": exc.max_sources,
                },
            )

        source_id = reg.source_id
        epoch = source.epoch
        keepalive = config.sources.keepalive_interval_seconds

        async def _keepalive_stream():
            try:
                yield f": keepalive {datetime.now(timezone.utc).isoformat()}\n".encode()
                while True:
                    try:
                        await asyncio.sleep(keepalive)
                    except asyncio.CancelledError:
                        break
                    # Detect an ungraceful drop within the keepalive cadence
                    # (< idle_timeout, FR-008) even if a write isn't yet due.
                    if await request.is_disconnected():
                        break
                    yield f": keepalive {datetime.now(timezone.utc).isoformat()}\n".encode()
            finally:
                # Epoch-guarded so a stale connection's cleanup never removes the
                # current registration (FR-007).
                await sources.remove(source_id, epoch)

        return StreamingResponse(_keepalive_stream(), media_type="text/event-stream")

    @app.get("/v1/sources")
    async def list_sources() -> JSONResponse:
        rows = sources.snapshot()
        return JSONResponse(
            status_code=200,
            content={
                "count": len(rows),
                "sources": [r.model_dump(mode="json") for r in rows],
            },
        )

    if config.agentsh is not None and config.agentsh.webhook_enabled:
        @app.post("/v1/webhook")
        async def webhook(request: Request) -> JSONResponse:
            # Untrusted "poll now" trigger only (agentsh webhook is unsigned and
            # carries no resolvable id). The body is ignored; we wake all pollers.
            sources.wake_all()
            return JSONResponse(status_code=202, content={"status": "scheduled"})

    @app.post("/v1/test")
    async def test_approval(request: Request) -> JSONResponse:
        # Local synthetic-approval injection (cli `test`): deliver a test-labeled
        # approval through the installed channel WITHOUT contacting agentsh, and
        # report the human's tap. Source-unaware; never resolves to agentsh.
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
            resp = await _deliver(synthetic.id, synthetic)
        except RegistrationFailed as exc:
            detail = "duplicate" if exc.reason is RegisterError.duplicate else "at capacity"
            return _err(503, "unavailable", detail=detail)
        except _DeliveryError:
            return _err(503, "unavailable", detail="notification delivery failed")
        return JSONResponse(status_code=200, content=resp.model_dump(mode="json"))

    app.state.registry = pending  # exposed for tests (pending-approvals registry)
    app.state.sources = sources  # exposed for tests (source registry)
    app.state.grant_store = grant_store  # exposed for tests
    return app
