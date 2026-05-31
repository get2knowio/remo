# Quickstart: Notifier Sidecar

Two audiences: an **operator** standing the notifier up on a host, and a **developer** working on the notifier code. Both assume the existing remo dev setup (`uv sync`, Python ≥3.11).

## Operator: zero → working approval loop

### 1. Create a Telegram bot
- In Telegram, message `@BotFather`, run `/newbot`, follow the prompts. Save the bot token it returns.

### 2. Find your chat id
- Message `@userinfobot` from your own account; it replies with your numeric user id. That is your `authorized_chat_id`.

### 3. Message your new bot once
- Open a chat with your bot and send any message. A bot cannot DM you until you've initiated a chat.

### 4. Export credentials on the laptop (where `remo` runs)
```bash
export REMO_NOTIFIER_TELEGRAM_BOT_TOKEN="12345:ABC...your-token"
export REMO_NOTIFIER_TELEGRAM_CHAT_ID="987654321"
```

### 5. Deploy to a host
```bash
remo notifier deploy <host>     # omit <host> to fuzzy-pick from known hosts
```
This applies the `remo_notifier` Ansible role: pre-flights the credentials, renders `/etc/notifier/notifier.toml`, writes the token to `/etc/notifier/secrets/telegram_bot_token` (0400), builds the image on the host, installs and starts `remo-notifier.service`, and waits for `/v1/health` to return 200.

### 6. Verify end-to-end
```bash
remo notifier test <host>
```
You should get a Telegram message within ~2 s; tapping **Approve** prints `decision: allow`, **Deny** prints `decision: deny`.

### Day-2
```bash
remo notifier status <host>            # /v1/health JSON
remo notifier logs <host> --follow     # journalctl -u remo-notifier.service -f
remo notifier restart <host>           # systemctl restart
remo notifier deploy <host> --rebuild  # force image rebuild
```

### Rotate the bot token
Update the secret on the host and restart:
```bash
remo notifier restart <host>   # after rewriting /etc/notifier/secrets/telegram_bot_token
```
(The service also re-reads the token on `SIGHUP`.)

## Developer: run and test locally

### Install with the notifier extra
```bash
uv pip install -e ".[notifier]"      # adds FastAPI, uvicorn, pydantic, python-telegram-bot, structlog
remo-notifier --help
```

### Run the server against a local config
```toml
# /tmp/notifier.toml
[server]
listen_host = "127.0.0.1"
listen_port = 18181
log_level = "debug"

[approval]
default_timeout_seconds = 300
max_timeout_seconds = 1800
max_pending_approvals = 50

[transport]
type = "telegram"

[transport.telegram]
bot_token_file = "/tmp/telegram_token"   # echo your token into this file
authorized_chat_id = 987654321
message_parse_mode = "MarkdownV2"

[instance]
id = "dev-local"
```
```bash
printf '%s' "$REMO_NOTIFIER_TELEGRAM_BOT_TOKEN" > /tmp/telegram_token
chmod 0400 /tmp/telegram_token
remo-notifier serve --config /tmp/notifier.toml
curl -s http://127.0.0.1:18181/v1/health | jq
```

### Exercise the wire protocol
```bash
# Times out → 408 fail-secure deny after ~5s
curl -i -X POST http://127.0.0.1:18181/v1/approve \
  -H 'content-type: application/json' \
  -d '{"operation":{"kind":"command","command":"rm","args":["-rf","/tmp/x"]},
       "policy_rule_name":"demo","policy_message":"approve rm?","timeout_seconds":5}'
```

### Build & size-check the image
```bash
docker build -t remo-notifier:0.1.0 -f notifier/Dockerfile .
docker images remo-notifier:0.1.0       # expect < 250 MB
docker run --rm -p 18181:18181 \
  -v /tmp/notifier.toml:/etc/notifier/notifier.toml:ro \
  -v /tmp/telegram_token:/run/secrets/telegram_bot_token:ro \
  remo-notifier:0.1.0
```

### Tests, lint, types
```bash
uv run pytest tests/notifier/ --cov=remo_cli.notifier   # target > 85%
uv run ruff check src/remo_cli/notifier/
uv run mypy src/remo_cli/notifier/
# Ansible safety (constitution): expect no bare attribute access
grep -r '\.rc ==' ansible/roles/remo_notifier/ ; grep -r '\.stdout' ansible/roles/remo_notifier/
```

## Acceptance smoke (maps to spec Success Criteria)

| Check | Spec |
|-------|------|
| Tap Approve → caller gets `allow` < 5 s | SC-001 |
| No tap, 5 s timeout → 408 `{decision: deny, reason: timeout}` in 5–6 s | SC-002, AC-7 |
| `deploy` → service `active (running)`, health 200 < 5 s | SC-003, AC-3 |
| Zero → working via documented steps + deploy + test | SC-004 |
| Only human Approve yields `allow` (timeout/400/503/shutdown never do) | SC-005 |
| No token/body/workspace at INFO+ logs | SC-006 |
| Existing `remo` commands unchanged; laptop install lacks notifier deps | SC-007 |
| Wire protocol fully documented (contracts/) | SC-008 |
| Image < 250 MB; health 200 < 5 s in container | AC-2 |
| `pytest tests/notifier/` > 85% coverage | AC-8 |
| `ruff` + `mypy` clean on notifier package | AC-9 |
