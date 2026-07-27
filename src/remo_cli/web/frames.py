"""``remo-terminal.v1`` control-frame models (FR-021, FR-021a).

Single canonical definition of the six WebSocket control frames exchanged by
``web/api/terminals.py``, per ``specs/020-openapi-type-generation/contracts/
terminal-frames-v1.md``. The service constructs and parses frames through
these models exclusively -- no ad-hoc dict literals (F-2, SC-012).

``ErrorClass`` is defined once in :mod:`remo_cli.web.terminal` (it also
drives ``classify_exit()`` there) and re-exported here rather than
duplicated.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from remo_cli.web.terminal import ErrorClass

__all__ = [
    "ErrorClass",
    "ResizeFrame",
    "PingFrame",
    "ReadyFrame",
    "ExitFrame",
    "ErrorFrame",
    "PongFrame",
    "InboundFrame",
    "OutboundFrame",
]


# ---------------------------------------------------------------------------
# Browser -> service
# ---------------------------------------------------------------------------


class ResizeFrame(BaseModel):
    v: Literal[1] = 1
    type: Literal["resize"] = "resize"
    # Defaults match today's `payload.get("cols", 80)` / `payload.get("rows",
    # 24)` fallback in `_handle_control` -- a resize frame missing one field
    # must keep validating (zero behavior change), not be dropped.
    cols: int = 80
    rows: int = 24


class PingFrame(BaseModel):
    v: Literal[1] = 1
    type: Literal["ping"] = "ping"


# ---------------------------------------------------------------------------
# Service -> browser
# ---------------------------------------------------------------------------


class ReadyFrame(BaseModel):
    v: Literal[1] = 1
    type: Literal["ready"] = "ready"


class ExitFrame(BaseModel):
    v: Literal[1] = 1
    type: Literal["exit"] = "exit"
    code: int


class ErrorFrame(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    v: Literal[1] = 1
    type: Literal["error"] = "error"
    class_: ErrorClass = Field(alias="class")
    message: str


class PongFrame(BaseModel):
    v: Literal[1] = 1
    type: Literal["pong"] = "pong"


# ---------------------------------------------------------------------------
# Discriminated unions (TypeAdapter-friendly, per §4 of the contract)
# ---------------------------------------------------------------------------

InboundFrame = Annotated[Union[ResizeFrame, PingFrame], Field(discriminator="type")]
OutboundFrame = Annotated[
    Union[ReadyFrame, ExitFrame, ErrorFrame, PongFrame], Field(discriminator="type")
]

# Reused by _handle_control so it isn't rebuilt on every inbound message.
INBOUND_FRAME_ADAPTER: TypeAdapter[InboundFrame] = TypeAdapter(InboundFrame)
