# Contract: Telegram message & callback

Defines the exact human-facing surface for the Telegram transport. Parse mode: `MarkdownV2` (configurable). All dynamic values MUST be MarkdownV2-escaped before insertion (Telegram requires escaping `_ * [ ] ( ) ~ \` > # + - = | { } . !`).

## Outgoing approval message

```
🔐 Approval requested

*Project:* {project}
*Operation:* {operation.kind}: {operation.command} {operation.args}
*Rule:* {policy_rule_name}
*Message:* {policy_message}
*Instance:* {instance_id}

Decide within {timeout_seconds // 60} minutes.
```

Field rendering:
- `{operation.args}` rendered space-joined; long arg lists may be truncated with an ellipsis for readability (the full args are never required in the message — agentsh holds ground truth).
- Missing optional fields (`project`, `command`) render as a placeholder (`—`) rather than the literal `None`.
- `timeout_seconds` is the **effective** (clamped) timeout; if `< 60`, show seconds instead of minutes.

## Inline keyboard

Two buttons on one row:

| Button text | `callback_data` |
|-------------|-----------------|
| ✅ Approve | `approve:{approval_id}` |
| ❌ Deny | `deny:{approval_id}` |

`callback_data` MUST stay ≤ 64 bytes (Telegram limit); a UUID approval_id (36 chars) + verb fits.

## Callback handling

1. Reject if `callback_query.message.chat.id != authorized_chat_id` → answer the callback quietly, take no action (FR-011).
2. Parse `verb:approval_id`. Unknown verb → ignore.
3. If `approval_id` is not currently pending → answer "already decided / expired", no state change (FR-012).
4. Else resolve: `allow` for `approve`, `deny` for `deny`; responder = `telegram:{username or user_id}`; reason empty.
5. `answerCallbackQuery` to clear the client spinner.

## Message edits on terminal outcome (FR-013)

| Outcome | Edited message suffix (replaces buttons) |
|---------|------------------------------------------|
| Approved | `✅ Approved by @{user} at {HH:MM}` |
| Denied | `❌ Denied by @{user} at {HH:MM}` |
| Timeout | `⌛ Timed out — denied (fail-secure)` |
| Cancelled (external/shutdown) | `🚫 Cancelled — resolved elsewhere` |

Edits remove the inline keyboard so the message can't be re-tapped. Edit failures (e.g. message deleted by the user) are logged at DEBUG and otherwise ignored — they never block resolving the caller.

## Test command surface (`remo notifier test`)

Sends an `ApprovalRequest` with `policy_rule_name: "test"`, `policy_message: "This is a test approval — please tap Approve or Deny to confirm wiring."`, `project: "remo-notifier-selftest"`, a short `timeout_seconds` (e.g. 120), and `operation: {kind: command, command: "echo", args: ["wiring-check"]}`. The CLI prints the returned decision (FR-027).
