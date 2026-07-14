"""Terminal REST + WebSocket endpoints (T038).

Implements ``POST/GET/DELETE /api/v1/terminals`` and the
``WS /api/v1/terminals/{id}`` stream per ``contracts/rest-api.md`` and
``contracts/terminal-websocket.md``:

* ``POST`` accepts ONLY an opaque ``session_target_id`` + dims (FR-015),
  re-authorizes the target against the live discovery cache (FR-050), enforces
  caps (FR-022), and returns a single-use WS token in the body — never a URL
  (FR-049). It does NOT spawn the PTY/ssh yet; that happens at WS upgrade.
* The WS handshake carries the protocol id + token as two
  ``Sec-WebSocket-Protocol`` values (token never in the URL, FR-049), validates
  ``Origin`` (FR-048), atomically consumes the token, re-checks the bound
  target (FR-050), then spawns the PTY + ssh and pumps binary=PTY /
  text=control frames both ways.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect, WebSocketState

from remo_cli.web.discovery import DiscoveryService
from remo_cli.web.models import TerminalState
from remo_cli.web.terminal import (
    MAX_DIMENSION,
    ErrorClass,
    TerminalSession,
    build_attach_argv,
)
from remo_cli.web.terminal_registry import CapReachedError, TerminalRegistry

logger = logging.getLogger("remo_cli.web.terminals")

router = APIRouter()

PROTOCOL_ID = "remo-terminal.v1"

# WS close codes (contracts/terminal-websocket.md).
_WS_POLICY_VIOLATION = 1008
_WS_INTERNAL_ERROR = 1011
_WS_TRY_AGAIN_LATER = 1013

# Human-safe, secret-free error messages per class (FR-028).
_ERROR_MESSAGES: dict[ErrorClass, str] = {
    ErrorClass.AUTH: "Authentication to the instance failed.",
    ErrorClass.NETWORK: "Could not reach the instance over SSH.",
    ErrorClass.REMOTE_CAPABILITY: (
        "The instance's Remo host tools are missing or incompatible."
    ),
    ErrorClass.MISSING_PROJECT: "The project is no longer available on the instance.",
    ErrorClass.REMOTE_LAUNCH: "The project session failed to launch on the instance.",
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class CreateTerminalRequest(BaseModel):
    session_target_id: str
    cols: int
    rows: int


class CreateTerminalResponse(BaseModel):
    terminal_id: str
    ws_token: str
    ws_subprotocol: str
    expires_in: int
    state: str


class TerminalOut(BaseModel):
    terminal_id: str
    session_target_id: str
    state: str
    created_at: str
    last_activity_at: str


class TerminalsListResponse(BaseModel):
    terminals: list[TerminalOut]


# ---------------------------------------------------------------------------
# App-state accessors
# ---------------------------------------------------------------------------


def _registry(app) -> TerminalRegistry:  # noqa: ANN001
    registry = getattr(app.state, "terminal_registry", None)
    if registry is None:
        registry = TerminalRegistry(app.state.settings)
        app.state.terminal_registry = registry
    return registry


def _discovery(app) -> DiscoveryService:  # noqa: ANN001
    service = getattr(app.state, "discovery_service", None)
    if service is None:
        service = DiscoveryService(getattr(app.state, "settings", None))
        app.state.discovery_service = service
    return service


def _client_id(request_or_ws) -> str:  # noqa: ANN001
    client = getattr(request_or_ws, "client", None)
    if client is not None and client.host:
        return client.host
    return "unknown"


def _error(status_code: int, code: str, message: str, *, remediation: str, retryable: bool):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "remediation": remediation,
            }
        },
    )


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.post("/terminals", status_code=201)
async def create_terminal(request: Request, body: CreateTerminalRequest):
    settings = request.app.state.settings

    # Graceful shutdown (NFR-007/SC-014): once web/app.py's lifespan shutdown
    # phase has started, stop accepting new terminals so every attachment
    # that exists when close_all() runs is one it actually reaps.
    if getattr(request.app.state, "shutting_down", False):
        return _error(
            503,
            "shutting_down",
            "The service is shutting down and is not accepting new terminals.",
            remediation="Retry against a running instance.",
            retryable=True,
        )

    # Validate dims (FR-060): reject zero/negative outright; clamp the upper
    # bound so an oversized-but-positive request is accepted (clamped), not
    # rejected.
    if body.cols < 1 or body.rows < 1:
        return _error(
            400,
            "invalid_dimensions",
            "cols and rows must be positive integers.",
            remediation="Send cols/rows within the supported range.",
            retryable=False,
        )
    cols = min(body.cols, MAX_DIMENSION)
    rows = min(body.rows, MAX_DIMENSION)

    # Re-authorize the target against the CURRENT discovery cache (FR-050): a
    # client never supplies a raw target, and a stale/undiscovered id is a 404.
    target = _discovery(request.app).find_target(body.session_target_id)
    if target is None:
        return _error(
            404,
            "unknown_target",
            "The requested session target is not currently discovered.",
            remediation="Refresh discovery and pick a currently available target.",
            retryable=True,
        )

    try:
        attachment, token = await _registry(request.app).register(
            body.session_target_id, cols, rows, _client_id(request)
        )
    except CapReachedError as exc:
        return _error(
            429,
            "cap_reached",
            f"The {exc.scope} terminal limit ({exc.limit}) has been reached.",
            remediation="Close an existing terminal and try again.",
            retryable=True,
        )

    return JSONResponse(
        status_code=201,
        content=CreateTerminalResponse(
            terminal_id=attachment.terminal_id,
            ws_token=token.value,
            ws_subprotocol=PROTOCOL_ID,
            expires_in=int(settings.ws_token_ttl_s),
            state=attachment.state.value,
        ).model_dump(),
    )


@router.get("/terminals", response_model=TerminalsListResponse)
async def list_terminals(request: Request) -> TerminalsListResponse:
    registry = _registry(request.app)
    return TerminalsListResponse(
        terminals=[
            TerminalOut(
                terminal_id=a.terminal_id,
                session_target_id=a.session_target_id,
                state=a.state.value,
                created_at=a.created_at,
                last_activity_at=a.last_activity_at,
            )
            for a in registry.list_for_client(_client_id(request))
        ]
    )


@router.delete("/terminals/{terminal_id}", status_code=204)
async def delete_terminal(request: Request, terminal_id: str):
    registry = _registry(request.app)
    if registry.get(terminal_id) is None:
        return _error(
            404,
            "unknown_terminal",
            "No such terminal.",
            remediation="List your terminals to find a valid id.",
            retryable=False,
        )
    await registry.close(terminal_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/terminals/{terminal_id}")
async def terminal_ws(websocket: WebSocket, terminal_id: str) -> None:
    app = websocket.app
    settings = app.state.settings
    registry = _registry(app)
    discovery = _discovery(app)

    subprotocols = list(websocket.scope.get("subprotocols", []))

    # 1. Subprotocol id must be present.
    if PROTOCOL_ID not in subprotocols:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    # 2. Origin allowlist (FR-048). Host is enforced by TrustedHostMiddleware.
    origin = websocket.headers.get("origin")
    if origin is None or origin not in settings.allowed_origins:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    # The token is the OTHER subprotocol value (never a URL/query param, FR-049).
    token_value = next((s for s in subprotocols if s != PROTOCOL_ID), None)
    if not token_value:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    # 3. Atomically consume the token; it must be bound to this terminal, and
    #    the bound target must still resolve in the current cache (FR-050).
    token = await registry.consume_token(token_value, terminal_id)
    if token is None:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    attachment = registry.get(terminal_id)
    if attachment is None:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    target = discovery.find_target(attachment.session_target_id)
    if target is None:
        registry.set_state(terminal_id, TerminalState.ERROR)
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    host = discovery.find_host(target.instance_type, target.instance_name)
    if host is None:
        registry.set_state(terminal_id, TerminalState.ERROR)
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    # 4. Accept, echoing back ONLY the protocol id (never the token).
    await websocket.accept(subprotocol=PROTOCOL_ID)
    registry.set_state(terminal_id, TerminalState.CONNECTING)

    # 5. Spawn the PTY + ssh attach. build_attach_argv() re-validates the
    # project name (T059, defense-in-depth) before constructing the remote
    # command, so a failure there is handled the same as any other
    # spawn/launch failure below.
    session: TerminalSession | None = None
    try:
        argv = build_attach_argv(host, target.project, control_dir=settings.ssh_control_dir)
        session = TerminalSession(argv, cols=attachment.cols, rows=attachment.rows)
        await session.start()
    except Exception:  # noqa: BLE001 - any spawn failure is surfaced + reaped.
        await _send_control(
            websocket,
            {
                "v": 1,
                "type": "error",
                "class": ErrorClass.REMOTE_LAUNCH.value,
                "message": _ERROR_MESSAGES[ErrorClass.REMOTE_LAUNCH],
            },
        )
        if session is not None:
            await session.close()
        registry.set_state(terminal_id, TerminalState.ERROR)
        await _safe_close(websocket, _WS_INTERNAL_ERROR)
        return

    registry.attach_session(terminal_id, session)
    await _send_control(websocket, {"v": 1, "type": "ready"})
    registry.set_state(terminal_id, TerminalState.READY)

    # 6/7. Pump until the process exits, the client disconnects, or a stall.
    send_task = asyncio.create_task(_send_loop(websocket, session, registry, terminal_id))
    recv_task = asyncio.create_task(_recv_loop(websocket, session, registry, terminal_id))
    stall_task = asyncio.create_task(_stall_watchdog(websocket, session))
    pump_tasks = [send_task, recv_task, stall_task]

    outcome = "client_disconnect"
    try:
        done, _pending = await asyncio.wait(pump_tasks, return_when=asyncio.FIRST_COMPLETED)
        outcome = next((t.result() for t in done if not t.cancelled()), "client_disconnect")
    finally:
        # Reaping the local ssh/PTY (FR-019) MUST happen even if this handler
        # task is being cancelled — some servers cancel the WS coroutine on
        # client disconnect rather than surfacing a disconnect frame. Shield
        # the cleanup so the process group is always reaped.
        await _shielded_cleanup(pump_tasks, registry, terminal_id)

    await _safe_close(websocket, _WS_TRY_AGAIN_LATER if outcome == "stalled" else 1000)


# ---------------------------------------------------------------------------
# Pump orchestration
# ---------------------------------------------------------------------------


async def _shielded_cleanup(
    pump_tasks: list[asyncio.Task],
    registry: TerminalRegistry,
    terminal_id: str,
) -> None:
    """Cancel the pumps and reap the terminal, resilient to our own cancellation.

    Reaping leaves the remote Zellij session running (killing the local ssh
    only detaches, FR-019) and marks the attachment ``disconnected``.
    """

    async def _do() -> None:
        for task in pump_tasks:
            task.cancel()
        await asyncio.gather(*pump_tasks, return_exceptions=True)
        await registry.mark_disconnected(terminal_id)

    cleanup = asyncio.ensure_future(_do())
    while True:
        try:
            await asyncio.shield(cleanup)
            return
        except asyncio.CancelledError:
            # Swallow our own cancellation until the reap has finished, so a
            # cancelled handler can't leak an orphaned ssh/PTY process group.
            if cleanup.done():
                return


async def _send_loop(
    websocket: WebSocket,
    session: TerminalSession,
    registry: TerminalRegistry,
    terminal_id: str,
) -> str:
    while True:
        chunk = await session.read_output()
        if chunk == b"":
            rc = await session.wait()
            err = session.error_class
            registry.record_exit(terminal_id, rc, err.value if err else None)
            if err is not None:
                await _send_control(
                    websocket,
                    {
                        "v": 1,
                        "type": "error",
                        "class": err.value,
                        "message": _ERROR_MESSAGES[err],
                    },
                )
            else:
                await _send_control(websocket, {"v": 1, "type": "exit", "code": rc})
            return "process_exit"
        try:
            await websocket.send_bytes(chunk)
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnect"


async def _recv_loop(
    websocket: WebSocket,
    session: TerminalSession,
    registry: TerminalRegistry,
    terminal_id: str,
) -> str:
    while True:
        try:
            message = await websocket.receive()
        except (WebSocketDisconnect, RuntimeError):
            return "client_disconnect"
        if message.get("type") == "websocket.disconnect":
            return "client_disconnect"

        data = message.get("bytes")
        if data is not None:
            await session.write_input(data)
            registry.touch(terminal_id)
            continue

        text = message.get("text")
        if text is not None:
            await _handle_control(websocket, session, text)
            registry.touch(terminal_id)


async def _handle_control(websocket: WebSocket, session: TerminalSession, text: str) -> None:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    msg_type = payload.get("type")
    if msg_type == "resize":
        session.resize(payload.get("cols", 80), payload.get("rows", 24))
    elif msg_type == "ping":
        await _send_control(websocket, {"v": 1, "type": "pong"})


async def _stall_watchdog(websocket: WebSocket, session: TerminalSession) -> str:
    while True:
        await asyncio.sleep(0.5)
        if session.is_stalled:
            return "stalled"


# ---------------------------------------------------------------------------
# WS send helpers
# ---------------------------------------------------------------------------


async def _send_control(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_text(json.dumps(payload))
    except (WebSocketDisconnect, RuntimeError):
        pass


async def _safe_close(websocket: WebSocket, code: int) -> None:
    if websocket.client_state == WebSocketState.DISCONNECTED:
        return
    try:
        await websocket.close(code=code)
    except (WebSocketDisconnect, RuntimeError):
        pass
