"""Telegram notification transport (long-polling).

Built on python-telegram-bot's Application, driven via the low-level
initialize/start/updater.start_polling API so it shares the FastAPI/uvicorn
event loop (research R1) — never run_polling(), never webhook mode (FR-014).
See contracts/telegram-message.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from remo_cli.notifier.grants import CandidateGrant, Grant, GrantLimitReached, GrantStore
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
    request: ApprovalRequest | None = None
    candidates: list[CandidateGrant] = field(default_factory=list)


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
        # Standing grants (Addendum 001) — bound by the server via bind_grants().
        self._grant_store: GrantStore | None = None
        self._grant_ttl = 28800
        # `application` is injectable for tests (a Bot mock).
        self._app = application or Application.builder().token(token).build()
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        self._app.add_handler(CommandHandler("rules", self._cmd_rules))
        self._app.add_handler(CommandHandler("revoke", self._cmd_revoke))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

    def bind_grants(self, store: GrantStore, *, default_ttl_seconds: int) -> None:
        """Attach the grant store so the Always flow + /rules /revoke /pause work."""
        self._grant_store = store
        self._grant_ttl = default_ttl_seconds

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
        row = [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{approval_id}"),
        ]
        if self._grant_store is not None:
            row.insert(1, InlineKeyboardButton("⏩ Always…", callback_data=f"always:{approval_id}"))
        keyboard = InlineKeyboardMarkup([row])
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
            request=request,
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
    @staticmethod
    def _responder(query: Any) -> str:
        user = getattr(query, "from_user", None)
        return f"telegram:{user.username or user.id}" if user is not None else "telegram:unknown"

    @staticmethod
    def _username(query: Any) -> str:
        user = getattr(query, "from_user", None)
        return user.username if user is not None else "?"

    def _authorized(self, query: Any) -> bool:
        msg = getattr(query, "message", None)
        chat = getattr(msg, "chat", None)
        return chat is not None and chat.id == self._authorized_chat_id

    async def _on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None or query.data is None:
            return
        if not self._authorized(query):  # FR-011
            await query.answer("Not authorized.")
            return
        data = query.data
        if data.startswith(("approve:", "deny:")):
            await self._handle_decision(query, data)
        elif data.startswith("always:"):
            await self._handle_always(query, data.split(":", 1)[1])
        elif data.startswith("pick:"):
            await self._handle_pick(query, data)
        elif data.startswith("revoke:"):
            await self._handle_revoke_cb(query, data.split(":", 1)[1])
        else:
            await query.answer()

    async def _handle_decision(self, query: Any, data: str) -> None:
        verb, approval_id = data.split(":", maxsplit=1)
        sent = self._sent.pop(approval_id, None)
        if sent is None:
            await query.answer("Already decided or expired.")  # FR-012
            return
        decision = Decision.allow if verb == "approve" else Decision.deny
        now = datetime.now(timezone.utc)
        word, icon = ("Approved", "✅") if decision is Decision.allow else ("Denied", "❌")
        user = getattr(query, "from_user", None)
        await self._edit(sent, f"{icon} {word} by @{user.username if user else '?'} at {now:%H:%M}")
        await query.answer()
        sent.on_response(ApprovalDecision(decision=decision, responder=self._responder(query), decided_at=now))
        self._log.info("approval_decided", approval_id=approval_id, decision=decision.value)

    async def _handle_always(self, query: Any, approval_id: str) -> None:
        sent = self._sent.get(approval_id)  # keep pending; only show the picker
        if sent is None or sent.request is None or self._grant_store is None:
            await query.answer("Expired.")
            return
        sent.candidates = self._grant_store.propose(sent.request)
        rows = [
            [InlineKeyboardButton(c.label, callback_data=f"pick:{approval_id}:{i}")]
            for i, c in enumerate(sent.candidates)
        ]
        rows.append([InlineKeyboardButton("Cancel", callback_data=f"pick:{approval_id}:cancel")])
        await self._app.bot.edit_message_reply_markup(
            chat_id=sent.chat_id, message_id=sent.message_id, reply_markup=InlineKeyboardMarkup(rows)
        )
        await query.answer()

    async def _handle_pick(self, query: Any, data: str) -> None:
        _, approval_id, sel = data.split(":", maxsplit=2)
        sent = self._sent.get(approval_id)
        if sent is None or self._grant_store is None:
            await query.answer("Expired.")
            return
        if sel == "cancel":
            await self._restore_keyboard(sent, approval_id)
            await query.answer("Cancelled.")
            return
        try:
            candidate = sent.candidates[int(sel)]
        except (ValueError, IndexError):
            await query.answer()
            return
        now = datetime.now(timezone.utc)
        created_by = self._responder(query)
        grant = Grant.create(
            predicate=candidate.predicate, scope=candidate.scope,
            ttl_seconds=self._grant_ttl, created_by=created_by,
            source_approval_id=approval_id, now=now,
        )
        try:
            await self._grant_store.create(grant)
            note = f"⏩ Always: {candidate.label} · by @{self._username(query)}"
            grant_id = grant.grant_id
        except GrantLimitReached:
            note = "⚠️ Grant limit reached — approved once, not remembered."
            grant_id = None
        self._sent.pop(approval_id, None)
        await self._edit(sent, note)
        await query.answer()
        sent.on_response(
            ApprovalDecision(decision=Decision.allow, responder=created_by, decided_at=now, grant_id=grant_id)
        )
        self._log.info("grant_created", approval_id=approval_id, grant_id=grant_id)

    async def _restore_keyboard(self, sent: _Sent, approval_id: str) -> None:
        row = [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval_id}"),
            InlineKeyboardButton("⏩ Always…", callback_data=f"always:{approval_id}"),
            InlineKeyboardButton("❌ Deny", callback_data=f"deny:{approval_id}"),
        ]
        try:
            await self._app.bot.edit_message_reply_markup(
                chat_id=sent.chat_id, message_id=sent.message_id, reply_markup=InlineKeyboardMarkup([row])
            )
        except Exception:  # noqa: BLE001
            self._log.debug("restore_keyboard_failed", message_id=sent.message_id)

    # -- slash commands (authorized chat only) ------------------------------
    def _cmd_authorized(self, update: Update) -> bool:
        chat = getattr(update, "effective_chat", None)
        return chat is not None and chat.id == self._authorized_chat_id

    async def _reply(self, update: Update, text: str) -> None:
        msg = getattr(update, "effective_message", None)
        if msg is not None:
            await msg.reply_text(text)

    async def _cmd_rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._cmd_authorized(update) or self._grant_store is None:
            return
        grants = self._grant_store.list_active()
        if not grants:
            await self._reply(update, "No active standing grants.")
            return
        lines = []
        for g in grants:
            lines.append(
                f"{g.grant_id[:8]} · {g.predicate.kind.value} · scope={g.scope.type.value} "
                f"· uses={g.uses_count} · /revoke {g.grant_id}"
            )
        await self._reply(update, "Active grants:\n" + "\n".join(lines))

    async def _cmd_revoke(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._cmd_authorized(update) or self._grant_store is None:
            return
        args = getattr(context, "args", None) or []
        if not args:
            await self._reply(update, "Usage: /revoke <grant_id>")
            return
        ok = await self._grant_store.revoke(args[0])
        await self._reply(update, "Revoked." if ok else "No such grant.")

    async def _handle_revoke_cb(self, query: Any, grant_id: str) -> None:
        if self._grant_store is not None:
            await self._grant_store.revoke(grant_id)
        await query.answer("Revoked.")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._cmd_authorized(update) or self._grant_store is None:
            return
        self._grant_store.set_paused(True)
        await self._reply(update, "Auto-approval paused. Use /resume to re-enable.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._cmd_authorized(update) or self._grant_store is None:
            return
        self._grant_store.set_paused(False)
        await self._reply(update, "Auto-approval resumed.")

    async def send_digest(self, summary: str) -> None:
        """Proactively message the authorized chat an auto-approval summary."""
        await self._app.bot.send_message(
            chat_id=self._authorized_chat_id, text=escape_md_v2(summary), parse_mode=self._parse_mode
        )

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
