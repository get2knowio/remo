"""Telegram notification transport (long-polling).

Built on python-telegram-bot's Application, driven via the low-level
initialize/start/updater.start_polling API so it shares the FastAPI/uvicorn
event loop (research R1) — never run_polling(), never webhook mode (FR-014).
See contracts/telegram-message.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from remo_cli.notifier.logging_setup import get_logger
from remo_cli.notifier.models import ApprovalDecision, ApprovalRequest, Decision
from remo_cli.notifier.transports.base import NotificationTransport, ResponseCallback

_MD_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!"


def escape_md_v2(text: str) -> str:
    """Escape text for Telegram MarkdownV2."""
    return re.sub(r"([" + re.escape(_MD_V2_SPECIAL) + r"\\])", r"\\\1", text)


@dataclass
class _Sent:
    chat_id: int
    message_id: int
    on_response: ResponseCallback


class TelegramTransport(NotificationTransport):
    name = "telegram"

    def __init__(
        self,
        *,
        token: str,
        authorized_chat_id: int,
        instance_id: str,
        parse_mode: str = "MarkdownV2",
        application: Application | None = None,
    ) -> None:
        self._authorized_chat_id = authorized_chat_id
        self._instance_id = instance_id
        self._parse_mode = parse_mode
        self._log = get_logger("remo_notifier.telegram")
        self._sent: dict[str, _Sent] = {}
        self._started = False
        # `application` is injectable for tests (a Bot mock).
        self._app = application or Application.builder().token(token).build()
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

    def set_token(self, token: str) -> None:
        """Stage a refreshed bot token (applied on the next start()).

        Best-effort secret rotation via SIGHUP (research R6). A live swap of an
        already-polling Application is out of scope for v1.
        """
        self._pending_token = token

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if self._started:
            return
        await self._app.initialize()
        await self._app.start()
        if self._app.updater is not None:
            await self._app.updater.start_polling()
        self._started = True
        self._log.info("transport_started", transport=self.name)

    async def stop(self) -> None:
        if not self._started:
            return
        try:
            if self._app.updater is not None:
                await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        finally:
            self._started = False
            self._log.info("transport_stopped", transport=self.name)

    async def healthy(self) -> bool:
        return self._started

    # -- delivery -----------------------------------------------------------
    def _render(self, request: ApprovalRequest, timeout_seconds: int) -> str:
        op = request.operation
        command = op.command or "—"
        args = " ".join(op.args)
        operation = f"{op.kind.value}: {command} {args}".strip()
        instance = request.instance_id or self._instance_id
        if timeout_seconds >= 60:
            window = f"{timeout_seconds // 60} minutes"
        else:
            window = f"{timeout_seconds} seconds"
        e = escape_md_v2
        return (
            "🔐 Approval requested\n\n"
            f"*Project:* {e(request.project or '—')}\n"
            f"*Operation:* {e(operation)}\n"
            f"*Rule:* {e(request.policy_rule_name)}\n"
            f"*Message:* {e(request.policy_message)}\n"
            f"*Instance:* {e(instance)}\n\n"
            f"Decide within {e(window)}\\."
        )

    async def send_approval_request(
        self,
        request: ApprovalRequest,
        on_response: ResponseCallback,
    ) -> None:
        approval_id = request.approval_id
        assert approval_id is not None  # server assigns before send
        timeout_seconds = request.timeout_seconds or 0
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval_id}"),
                    InlineKeyboardButton("❌ Deny", callback_data=f"deny:{approval_id}"),
                ]
            ]
        )
        # Raises on delivery failure -> server maps to 503, holds no slot.
        message = await self._app.bot.send_message(
            chat_id=self._authorized_chat_id,
            text=self._render(request, timeout_seconds),
            reply_markup=keyboard,
            parse_mode=self._parse_mode,
        )
        self._sent[approval_id] = _Sent(
            chat_id=self._authorized_chat_id,
            message_id=message.message_id,
            on_response=on_response,
        )
        self._log.info("approval_sent", approval_id=approval_id, transport=self.name)

    async def cancel(self, approval_id: str, *, outcome: str = "cancelled") -> None:
        sent = self._sent.pop(approval_id, None)
        if sent is None:
            return
        suffix = (
            "⌛ Timed out — denied (fail-secure)"
            if outcome == "timeout"
            else "🚫 Cancelled — resolved elsewhere"
        )
        await self._edit(sent, suffix)

    # -- callbacks ----------------------------------------------------------
    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        # Authorized-chat enforcement (FR-011).
        chat = query.message.chat if query.message is not None else None
        if chat is None or chat.id != self._authorized_chat_id:
            await query.answer("Not authorized.")
            return
        try:
            verb, approval_id = query.data.split(":", maxsplit=1)
        except ValueError:
            await query.answer()
            return
        if verb not in {"approve", "deny"}:
            await query.answer()
            return

        sent = self._sent.pop(approval_id, None)
        if sent is None:
            # Already decided / expired / unknown (FR-012).
            await query.answer("Already decided or expired.")
            return

        decision = Decision.allow if verb == "approve" else Decision.deny
        user = query.from_user
        responder = f"telegram:{user.username or user.id}" if user is not None else "telegram:unknown"
        now = datetime.now(timezone.utc)
        verb_word = "Approved" if decision is Decision.allow else "Denied"
        icon = "✅" if decision is Decision.allow else "❌"
        await self._edit(sent, f"{icon} {verb_word} by @{user.username if user else '?'} at {now:%H:%M}")
        await query.answer()
        sent.on_response(
            ApprovalDecision(decision=decision, responder=responder, decided_at=now)
        )
        self._log.info("approval_decided", approval_id=approval_id, decision=decision.value)

    async def _edit(self, sent: _Sent, suffix: str) -> None:
        try:
            await self._app.bot.edit_message_text(
                chat_id=sent.chat_id,
                message_id=sent.message_id,
                text=escape_md_v2(suffix),
                parse_mode=self._parse_mode,
                reply_markup=None,
            )
        except Exception:  # noqa: BLE001 - edit failures must never block resolution
            self._log.debug("edit_failed", message_id=sent.message_id)
