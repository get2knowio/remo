"""Tests for the Telegram transport (T012).

The PTB Application is replaced with a mock exposing an async ``bot`` and
``add_handler``; no network or real token is involved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from remo_cli.notifier.models import Decision
from remo_cli.notifier.transports.telegram import TelegramTransport, escape_md_v2

from .conftest import make_request


CHAT_ID = 987654321
ABC = "11111111-1111-1111-1111-111111111111"


def _make_transport() -> tuple[TelegramTransport, MagicMock]:
    app = MagicMock()
    app.bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=42))
    app.bot.edit_message_text = AsyncMock()
    transport = TelegramTransport(
        token="x:y",
        authorized_chat_id=CHAT_ID,
        instance_id="inst-1",
        application=app,
    )
    return transport, app


def _callback_update(data: str, *, chat_id: int = CHAT_ID, username: str = "paul") -> SimpleNamespace:
    return SimpleNamespace(
        callback_query=SimpleNamespace(
            data=data,
            message=SimpleNamespace(chat=SimpleNamespace(id=chat_id)),
            from_user=SimpleNamespace(username=username, id=1),
            answer=AsyncMock(),
        )
    )


def test_escape_md_v2() -> None:
    assert escape_md_v2("a.b-c!") == "a\\.b\\-c\\!"


async def test_send_builds_message_and_keyboard() -> None:
    transport, app = _make_transport()
    req = make_request(approval_id=ABC, project="proj", timeout_seconds=300)
    captured = {}
    transport._sent.clear()

    await transport.send_approval_request(req, on_response=lambda d: captured.setdefault("d", d))

    app.bot.send_message.assert_awaited_once()
    kwargs = app.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == CHAT_ID
    assert "Approval requested" in kwargs["text"]
    keyboard = kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == f"approve:{ABC}"
    assert keyboard[0][1].callback_data == f"deny:{ABC}"
    assert ABC in transport._sent


async def test_callback_approve_resolves_and_edits() -> None:
    transport, app = _make_transport()
    req = make_request(approval_id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))

    await transport._on_callback(_callback_update(f"approve:{ABC}"), MagicMock())

    assert got["d"].decision is Decision.allow
    assert got["d"].responder == "telegram:paul"
    app.bot.edit_message_text.assert_awaited()  # message edited
    assert ABC not in transport._sent  # consumed


async def test_callback_deny_resolves() -> None:
    transport, _ = _make_transport()
    req = make_request(approval_id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))
    await transport._on_callback(_callback_update(f"deny:{ABC}"), MagicMock())
    assert got["d"].decision is Decision.deny


async def test_callback_from_foreign_chat_ignored() -> None:
    transport, _ = _make_transport()
    req = make_request(approval_id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))
    await transport._on_callback(_callback_update(f"approve:{ABC}", chat_id=111), MagicMock())
    assert "d" not in got  # unauthorized -> no resolution
    assert ABC in transport._sent  # still pending


async def test_callback_unknown_id_is_noop() -> None:
    transport, _ = _make_transport()
    update = _callback_update("approve:ghost")
    # No send was made for "ghost"; should answer and not raise.
    await transport._on_callback(update, MagicMock())
    update.callback_query.answer.assert_awaited()


async def test_cancel_timeout_edits_message() -> None:
    transport, app = _make_transport()
    req = make_request(approval_id=ABC)
    await transport.send_approval_request(req, on_response=lambda d: None)
    await transport.cancel(ABC, outcome="timeout")
    app.bot.edit_message_text.assert_awaited()
    assert ABC not in transport._sent


async def test_cancel_unknown_is_noop() -> None:
    transport, app = _make_transport()
    await transport.cancel("nope")
    app.bot.edit_message_text.assert_not_awaited()


async def test_lifecycle_start_stop() -> None:
    transport, app = _make_transport()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    app.updater = SimpleNamespace(start_polling=AsyncMock(), stop=AsyncMock())

    await transport.start()
    assert transport._started is True
    assert await transport.healthy() is True
    app.initialize.assert_awaited_once()
    app.updater.start_polling.assert_awaited_once()

    await transport.stop()
    assert transport._started is False
    app.shutdown.assert_awaited_once()


async def test_set_token_stages_pending() -> None:
    transport, _ = _make_transport()
    transport.set_token("new:token")
    assert transport._pending_token == "new:token"
