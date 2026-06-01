# Quickstart: Notifier Channels

Two audiences: an **operator** installing a notifier by channel, and a **channel author** adding a new channel.

## Operator: install a notifier by channel

**Prerequisite — agentsh**: the host's agentsh runs with `approvals.mode=api` and auth enabled (`auth.type=api_key`), and you have an **approver**-role API key for it (its approvals API is disabled when auth is off). The notifier connects to agentsh as an approver client — the human's decision flows through the notifier, never directly to agentsh.

1. See what channels exist and what each needs:

   ```bash
   remo notifier channels
   # CHANNEL    LABEL      REQUIRED ENV
   # telegram   Telegram   REMO_NOTIFIER_TELEGRAM_BOT_TOKEN (secret), REMO_NOTIFIER_TELEGRAM_CHAT_ID
   ```

2. Export the agentsh connection (channel-independent) and the selected channel's credentials:

   ```bash
   # agentsh approver connection — required for every channel
   export REMO_NOTIFIER_AGENTSH_API_URL="http://172.17.0.1:8080"   # agentsh approvals API base
   export REMO_NOTIFIER_AGENTSH_API_KEY="<approver-role-api-key>"

   # the chosen channel's delivery credentials
   export REMO_NOTIFIER_TELEGRAM_BOT_TOKEN="12345:ABC...your-token"
   export REMO_NOTIFIER_TELEGRAM_CHAT_ID="987654321"
   ```

3. Deploy — name the channel, or omit `--channel` to pick interactively:

   ```bash
   remo notifier deploy my-host --channel telegram
   # or, interactively:
   remo notifier deploy my-host          # fuzzy-pick a channel from the catalog
   ```

   The command runs the Telegram credential preflight, builds `remo-notifier-telegram:<version>` on the host, renders config + secret, installs/starts the single `remo-notifier.service` bound to the Docker bridge, and waits for `/v1/health`. If a different channel was previously installed on this host, it is replaced.

4. Verify and exercise:

   ```bash
   remo notifier status my-host    # health summary; "transport" shows the active channel
   remo notifier test my-host      # round-trips a test approval through the installed channel
   ```

### Notes
- The notifier is **never** installed during host provisioning; it is stood up only by `remo notifier deploy` (FR-009a).
- Switching channels (`remo notifier deploy my-host --channel <other>`) replaces the running channel on the same bind/port; in-flight approvals and standing grants are lost across the switch (it is a restart).
- Missing credentials fail the preflight loudly, naming exactly the variables that channel needs — nothing is deployed.

## Channel author: add a new channel (e.g. Slack)

Add exactly these; touch nothing else (see contracts/channel-extension.md):

1. `src/remo_cli/notifier/channels/slack/` with `transport.py` (a `NotificationTransport` + `build()`), `config.py` (strict Pydantic model for `[transport.slack]`), and `descriptor.py` (the `ChannelDescriptor`).
2. Append the descriptor to `CHANNELS` in `channels/catalog.py`.
3. Add the `notifier-slack` extra in `pyproject.toml` (`notifier-core` + the Slack SDK).

Then, with no further changes:

```bash
remo notifier channels                       # slack now appears
export REMO_NOTIFIER_SLACK_BOT_TOKEN=...      # whatever the descriptor declares
export REMO_NOTIFIER_SLACK_CHANNEL_ID=...
remo notifier deploy my-host --channel slack  # builds remo-notifier-slack:<version>, deploys
```

Guardrail: the diff for a new channel must not touch the core (`server.py`, `state.py`, `models.py`, `grants.py`, `config.py`), the transport ABC, or any other channel — enforced by `tests/notifier/channels/test_stub_channel.py`.

## Pre-commit (Ansible — Constitution I)

```bash
grep -r '\.rc ==' ansible/roles/remo_notifier/ ; grep -r '\.stdout' ansible/roles/remo_notifier/
# any matches must use | default()
```
