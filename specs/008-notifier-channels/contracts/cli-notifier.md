# Contract: `remo notifier` CLI surface (channel-aware)

Deltas from spec 007. All host-taking subcommands keep the existing fuzzy host picker when no host is named (FR-031, unchanged).

## `remo notifier channels`  (NEW — FR-006a)

Lists the catalog. No host, no network.

```
$ remo notifier channels
CHANNEL    LABEL      REQUIRED ENV
telegram   Telegram   REMO_NOTIFIER_TELEGRAM_BOT_TOKEN (secret), REMO_NOTIFIER_TELEGRAM_CHAT_ID
```

- Output lists every `ChannelDescriptor`: id, label, and each `required_env` (marking secrets).
- Exit 0 always (pure local read).

## `remo notifier deploy [HOST] [--channel ID] [--rebuild] [-v]`  (MODIFIED)

Channel selection rules:

| Condition | Behavior |
|-----------|----------|
| `--channel ID` given, ID in catalog | Deploy that channel; no picker. |
| `--channel ID` given, ID not in catalog | Exit non-zero: `unknown channel 'ID'; available: telegram` and deploy nothing (FR-010). |
| No `--channel`, interactive TTY | Fuzzy picker over the catalog (consistent with the host picker). |
| No `--channel`, interactive TTY, catalog has exactly one channel | MAY auto-select the sole channel (edge case: single-channel catalog). |
| No `--channel`, non-interactive (no TTY) | Exit non-zero with actionable message: name a channel with `--channel` (FR-011). |

Per-channel preflight (FR-012), after the channel is resolved:
- For each `required_env` of the selected channel, the env var MUST be set and non-empty.
- On any missing var: exit non-zero naming exactly the missing vars and their purpose; deploy nothing — even if a *different* channel's vars are present (SC-007).

On success, invokes `notifier_deploy.yml` with extra-vars: `remo_notifier_channel=<id>`, the channel's secret/non-secret values, and the descriptor-rendered transport TOML fragment. The notifier ends up running that one channel on the unchanged bind/port; a prior channel on the host is replaced (FR-013).

## `remo notifier status / logs / restart / test [HOST]`  (UNCHANGED behavior)

- Operate over SSH against the single `remo-notifier.service` / bridge bind / port, exactly as 007.
- `status` shows the health summary; its `transport` field now reads as the **active channel** (R6) — no schema change.
- `test` verifies the full delivery path through whatever channel is installed. Note: 007's `test` POSTed to `/v1/approve`, which is **removed** — `test` must instead drive a local test-injection path (a synthetic approval delivered to the channel without contacting agentsh), since approvals now originate in agentsh. Mechanism to be settled in implementation; the operator-facing behavior (a test-labeled approval round-trips to the human) is unchanged.

## Removed

- The provisioning/configure deployment of the notifier (`configure_remo_notifier` toggle) is removed: the notifier is installed **only** via `remo notifier deploy` (FR-009a).
