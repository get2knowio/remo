"""Round-trip + lenient-inbound tests for the ``remo-terminal.v1`` control
frames (T046, T047).

See specs/020-openapi-type-generation/contracts/terminal-frames-v1.md:

* F-4 (byte-identical outbound bytes) -- the round-trip tests below.
* F-3 (lenient inbound: malformed JSON / non-object / unknown type must be
  silently dropped, never raise, never close the socket) -- the
  ``test_handle_control_*`` tests below, exercised directly against
  ``_handle_control`` the same way ``tests/unit/web/test_terminals_api.py``
  exercises the WS pump (a trivial in-process ``TerminalSession``-shaped
  stub, no real PTY/ssh needed since ``_handle_control`` only touches
  ``session.resize`` on the resize path).
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from remo_cli.web.api.terminals import _handle_control, _send_control
from remo_cli.web.frames import (
    INBOUND_FRAME_ADAPTER,
    ErrorClass,
    ErrorFrame,
    ExitFrame,
    PingFrame,
    PongFrame,
    ReadyFrame,
    ResizeFrame,
)

# ---------------------------------------------------------------------------
# Byte-identity round-trip (F-4)
# ---------------------------------------------------------------------------


class _RecordingWebSocket:
    """Minimal stand-in for FastAPI's WebSocket, capturing sent text frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


async def _sent_bytes(frame) -> str:
    ws = _RecordingWebSocket()
    await _send_control(ws, frame)
    assert len(ws.sent) == 1
    return ws.sent[0]


@pytest.mark.asyncio
async def test_resize_frame_round_trip_matches_todays_dict_literal():
    # resize is inbound-only in practice, but the model itself must still
    # serialize identically to a hand-built dict for symmetry with the
    # other five.
    expected = json.dumps({"v": 1, "type": "resize", "cols": 100, "rows": 40})
    frame = ResizeFrame(cols=100, rows=40)
    actual = json.dumps(frame.model_dump(mode="json", by_alias=True))
    assert actual == expected


@pytest.mark.asyncio
async def test_ping_frame_round_trip_matches_todays_dict_literal():
    expected = json.dumps({"v": 1, "type": "ping"})
    frame = PingFrame()
    actual = json.dumps(frame.model_dump(mode="json", by_alias=True))
    assert actual == expected


@pytest.mark.asyncio
async def test_ready_frame_round_trip_matches_todays_dict_literal():
    expected = json.dumps({"v": 1, "type": "ready"})
    assert await _sent_bytes(ReadyFrame()) == expected


@pytest.mark.asyncio
async def test_exit_frame_round_trip_matches_todays_dict_literal():
    expected = json.dumps({"v": 1, "type": "exit", "code": 137})
    assert await _sent_bytes(ExitFrame(code=137)) == expected


@pytest.mark.asyncio
async def test_error_frame_round_trip_matches_todays_dict_literal():
    expected = json.dumps(
        {
            "v": 1,
            "type": "error",
            "class": "auth",
            "message": "Authentication to the instance failed.",
        }
    )
    frame = ErrorFrame(
        class_=ErrorClass.AUTH, message="Authentication to the instance failed."
    )
    assert await _sent_bytes(frame) == expected


@pytest.mark.asyncio
async def test_pong_frame_round_trip_matches_todays_dict_literal():
    expected = json.dumps({"v": 1, "type": "pong"})
    assert await _sent_bytes(PongFrame()) == expected


def test_error_frame_constructible_via_dict_form_class_alias():
    """`class` is the wire key; the model must accept it via alias too."""
    frame = ErrorFrame.model_validate(
        {"v": 1, "type": "error", "class": "network", "message": "boom"}
    )
    assert frame.class_ is ErrorClass.NETWORK


# ---------------------------------------------------------------------------
# Inbound validation directly through the TypeAdapter (F-3)
# ---------------------------------------------------------------------------


def test_inbound_adapter_accepts_resize_missing_cols_and_rows():
    """A resize frame missing cols/rows must still validate (zero behavior
    change vs. today's `payload.get("cols", 80)` / `payload.get("rows", 24)`
    fallback), defaulting to 80/24."""
    frame = INBOUND_FRAME_ADAPTER.validate_python({"v": 1, "type": "resize"})
    assert isinstance(frame, ResizeFrame)
    assert (frame.cols, frame.rows) == (80, 24)


def test_inbound_adapter_accepts_resize_with_only_cols():
    frame = INBOUND_FRAME_ADAPTER.validate_python({"v": 1, "type": "resize", "cols": 200})
    assert isinstance(frame, ResizeFrame)
    assert (frame.cols, frame.rows) == (200, 24)


def test_inbound_adapter_accepts_ping():
    frame = INBOUND_FRAME_ADAPTER.validate_python({"v": 1, "type": "ping"})
    assert isinstance(frame, PingFrame)


def test_inbound_adapter_rejects_unknown_type():
    with pytest.raises(ValidationError):
        INBOUND_FRAME_ADAPTER.validate_python({"v": 1, "type": "nonexistent"})


# ---------------------------------------------------------------------------
# _handle_control: silent-drop invariant (F-3) -- the single most important
# test in this file.
# ---------------------------------------------------------------------------


class _ResizeRecordingSession:
    """A TerminalSession stand-in that only needs `resize()` to exist."""

    def __init__(self) -> None:
        self.resize_calls: list[tuple[int, int]] = []

    def resize(self, cols: int, rows: int) -> None:
        self.resize_calls.append((cols, rows))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "not json at all {",  # malformed JSON
        "[1, 2, 3]",  # JSON array, not an object
        '"just a string"',  # bare JSON string
        "42",  # bare JSON number
        "null",  # JSON null
        json.dumps({"v": 1, "type": "nonexistent"}),  # unknown type
        json.dumps({"v": 2, "type": "resize", "cols": 10, "rows": 10}),  # wrong v
        json.dumps({"type": "missing_type_field_variant"}),  # unknown type, no v
    ],
)
async def test_handle_control_silently_drops_bad_inbound_frames(text):
    """F-3: malformed JSON / non-object / unknown-type frames must be
    silently dropped -- no exception, no socket close, no side effect."""
    ws = _RecordingWebSocket()
    session = _ResizeRecordingSession()
    await _handle_control(ws, session, text)  # must not raise
    assert session.resize_calls == []
    assert ws.sent == []


@pytest.mark.asyncio
async def test_handle_control_valid_resize_still_resizes():
    ws = _RecordingWebSocket()
    session = _ResizeRecordingSession()
    await _handle_control(ws, session, json.dumps({"v": 1, "type": "resize", "cols": 120, "rows": 40}))
    assert session.resize_calls == [(120, 40)]


@pytest.mark.asyncio
async def test_handle_control_valid_ping_sends_pong():
    ws = _RecordingWebSocket()
    session = _ResizeRecordingSession()
    await _handle_control(ws, session, json.dumps({"v": 1, "type": "ping"}))
    assert ws.sent == [json.dumps({"v": 1, "type": "pong"})]
