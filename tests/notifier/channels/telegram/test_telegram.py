"""Tests for the Telegram transport (relocated, spec 008 — renders agentsh Request).

The PTB Application is replaced with a mock exposing an async ``bot`` and
``add_handler``; no network or real token is involved.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from remo_cli.notifier.channels.telegram.transport import TelegramTransport, escape_md_v2
from remo_cli.notifier.grants import (
    Grant,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    GrantStore,
    TargetMatchType,
)
from remo_cli.notifier.models import Decision

from ...conftest import make_request

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
    req = make_request(id=ABC, kind="file_delete", target="/ws/scratch.txt")
    transport._sent.clear()

    await transport.send_approval_request(req, on_response=lambda d: None)

    app.bot.send_message.assert_awaited_once()
    kwargs = app.bot.send_message.call_args.kwargs
    assert kwargs["chat_id"] == CHAT_ID
    assert "Approval requested" in kwargs["text"]
    assert "file\\_delete" in kwargs["text"]  # agentsh kind, MarkdownV2-escaped
    assert "scratch" in kwargs["text"]  # agentsh target rendered
    keyboard = kwargs["reply_markup"].inline_keyboard
    assert keyboard[0][0].callback_data == f"approve:{ABC}"
    assert keyboard[0][1].callback_data == f"deny:{ABC}"
    assert ABC in transport._sent


async def test_callback_approve_resolves_and_edits() -> None:
    transport, app = _make_transport()
    req = make_request(id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))

    await transport._on_callback(_callback_update(f"approve:{ABC}"), MagicMock())

    assert got["d"].decision is Decision.allow
    assert got["d"].responder == "telegram:paul"
    app.bot.edit_message_text.assert_awaited()
    assert ABC not in transport._sent


async def test_callback_deny_resolves() -> None:
    transport, _ = _make_transport()
    req = make_request(id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))
    await transport._on_callback(_callback_update(f"deny:{ABC}"), MagicMock())
    assert got["d"].decision is Decision.deny


async def test_callback_from_foreign_chat_ignored() -> None:
    transport, _ = _make_transport()
    req = make_request(id=ABC)
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))
    await transport._on_callback(_callback_update(f"approve:{ABC}", chat_id=111), MagicMock())
    assert "d" not in got  # unauthorized -> no resolution
    assert ABC in transport._sent


async def test_callback_unknown_id_is_noop() -> None:
    transport, _ = _make_transport()
    update = _callback_update("approve:ghost")
    await transport._on_callback(update, MagicMock())
    update.callback_query.answer.assert_awaited()


async def test_cancel_timeout_edits_message() -> None:
    transport, app = _make_transport()
    req = make_request(id=ABC)
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


def test_reread_secret_from_file(tmp_path) -> None:
    p = tmp_path / "tok"
    p.write_text("99:NEW")
    transport, _ = _make_transport()
    transport._token_file = str(p)
    transport.reread_secret()
    assert transport._pending_token == "99:NEW"


# --- Standing grants (Addendum 001, reworked) -------------------------------
def _make_transport_with_grants(max_grants: int = 100):
    transport, app = _make_transport()
    app.bot.edit_message_reply_markup = AsyncMock()
    store = GrantStore(max_grants=max_grants, instance_id="inst-1", allow_global_scope=True)
    transport.bind_grants(store, default_ttl_seconds=3600)
    return transport, app, store


def _cmd_update(*, chat_id: int = CHAT_ID):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )


def _any_command_grant() -> Grant:
    return Grant.create(
        predicate=GrantPredicate(kind="command", target_match=TargetMatchType.any),
        scope=GrantScope(type=GrantScopeType.glob), ttl_seconds=3600,
        created_by="t", source_approval_id="x",
    )


async def test_always_button_present_when_grants_bound() -> None:
    transport, app, _ = _make_transport_with_grants()
    req = make_request(id=ABC, kind="command", target="git push")
    await transport.send_approval_request(req, on_response=lambda d: None)
    row = app.bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in row] == [f"approve:{ABC}", f"always:{ABC}", f"deny:{ABC}"]


async def test_always_then_pick_creates_grant_and_allows() -> None:
    transport, app, store = _make_transport_with_grants()
    req = make_request(id=ABC, kind="command", target="git push origin", session_id="s1")
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))

    await transport._on_callback(_callback_update(f"always:{ABC}"), MagicMock())
    app.bot.edit_message_reply_markup.assert_awaited()
    assert ABC in transport._sent and transport._sent[ABC].candidates

    await transport._on_callback(_callback_update(f"pick:{ABC}:0"), MagicMock())
    assert store.count() == 1
    assert got["d"].decision is Decision.allow
    assert got["d"].grant_id is not None
    assert ABC not in transport._sent


async def test_pick_cancel_restores_keyboard() -> None:
    transport, app, store = _make_transport_with_grants()
    req = make_request(id=ABC, kind="command", target="git push", session_id="s1")
    await transport.send_approval_request(req, on_response=lambda d: None)
    await transport._on_callback(_callback_update(f"always:{ABC}"), MagicMock())
    await transport._on_callback(_callback_update(f"pick:{ABC}:cancel"), MagicMock())
    assert store.count() == 0
    assert ABC in transport._sent


async def test_pick_at_capacity_allows_once_without_grant() -> None:
    transport, app, store = _make_transport_with_grants(max_grants=1)
    await store.create(_any_command_grant())  # different kind below stays pending
    req = make_request(id=ABC, kind="network", target="example.com", session_id="s1")
    got = {}
    await transport.send_approval_request(req, on_response=lambda d: got.setdefault("d", d))
    await transport._on_callback(_callback_update(f"always:{ABC}"), MagicMock())
    await transport._on_callback(_callback_update(f"pick:{ABC}:0"), MagicMock())
    assert got["d"].decision is Decision.allow
    assert got["d"].grant_id is None
    assert store.count() == 1


async def test_cmd_rules_revoke_pause() -> None:
    transport, app, store = _make_transport_with_grants()
    g = _any_command_grant()
    await store.create(g)

    upd = _cmd_update()
    await transport._cmd_rules(upd, MagicMock())
    assert g.grant_id[:8] in upd.effective_message.reply_text.call_args.args[0]

    upd2 = _cmd_update()
    await transport._cmd_revoke(upd2, SimpleNamespace(args=[g.grant_id]))
    assert store.count() == 0

    await transport._cmd_pause(_cmd_update(), MagicMock())
    assert store.paused is True
    await transport._cmd_resume(_cmd_update(), MagicMock())
    assert store.paused is False


async def test_cmd_rejects_unauthorized_chat() -> None:
    transport, _, store = _make_transport_with_grants()
    await transport._cmd_pause(_cmd_update(chat_id=999), MagicMock())
    assert store.paused is False


async def test_send_digest_messages_chat() -> None:
    transport, app, _ = _make_transport_with_grants()
    await transport.send_digest("Auto-approved 3 operation(s).")
    app.bot.send_message.assert_awaited()
    assert "3 operation" in app.bot.send_message.call_args.kwargs["text"]
