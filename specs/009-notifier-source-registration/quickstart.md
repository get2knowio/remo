# Quickstart: Notifier Source Registration

How one host's notifier serves many devcontainers, each running its own agentsh,
gated by per-source presence connections. Builds on the 008 notifier (channel +
agentsh approver model). All state is in-memory; recovery is by reconnection.

---

## Operator: deploy the notifier (once per host)

Unchanged from 008 — the notifier is still one container per host on the same
bind/port:

```bash
export REMO_NOTIFIER_AGENTSH_API_URL=...   # optional: seeds a back-compat source
export REMO_NOTIFIER_AGENTSH_API_KEY=...
export REMO_NOTIFIER_TELEGRAM_BOT_TOKEN=...
export REMO_NOTIFIER_TELEGRAM_CHAT_ID=...
remo notifier deploy myhost --channel telegram
```

New in 009: the notifier now also accepts **dynamic source registrations** on
`POST /v1/sources`. Tune the registry in the rendered `notifier.toml`
(`[sources]`): `max_sources` (default 64), `keepalive_interval_seconds` (15),
`idle_timeout_seconds` (45), and the per-source backoff
(`poll_base_interval_seconds` 5, `poll_backoff_factor` 2.0,
`poll_backoff_cap_seconds` 300, `poll_backoff_jitter` 0.2). If `[agentsh]` is
configured it becomes a permanent **seed source**; if omitted the notifier serves
only dynamic sources.

---

## Project author: opt in via the devcontainer Feature

Add the Feature to the project's `.devcontainer/devcontainer.json`:

```jsonc
{
  "features": {
    "./features/remo-notifier-source": {
      "notifierAddress": "172.17.0.1:18181",
      "agentshApiUrl": "http://proj-a:8080",
      "apiKeyFile": "/run/secrets/agentsh_approver_key",
      "labels": "project=proj-a"
    }
  }
}
```

Prerequisite: the notifier and this container must share a network path so the
notifier can reach `agentshApiUrl` and the container can reach `notifierAddress`
(a user-defined Docker network or published ports). On container start the Feature
opens the presence connection and keeps it up; a project that omits the Feature is
never connected.

A project that doesn't use the Feature can still register manually for testing:

```bash
curl --no-buffer -sS -X POST http://172.17.0.1:18181/v1/sources \
  -H 'content-type: application/json' \
  -d '{"source_id":"proj-a","api_url":"http://proj-a:8080","api_key":"'"$KEY"'","labels":{"project":"proj-a"}}'
# stays open, streaming keepalive ticks; Ctrl-C drops it → source removed
```

---

## Operator: observe connected sources

```bash
remo notifier sources myhost
# {
#   "count": 2,
#   "sources": [
#     {"source_id":"proj-a","labels":{"project":"proj-a"},
#      "poll_state":"polling","last_success_at":"...","consecutive_failures":0,"permanent":false},
#     {"source_id":"seed","labels":{},"poll_state":"backing_off",
#      "last_success_at":null,"consecutive_failures":3,"permanent":true}
#   ]
# }

remo notifier status myhost   # health summary now includes "sources": <count>
```

A source whose agentsh is unreachable shows `backing_off` but stays listed (its
connection is up). A source whose connection drops disappears.

---

## What to verify (acceptance)

| Check | Expected | Spec |
|-------|----------|------|
| Two sources connect to two fake agentsh endpoints; raise one approval on each | Each delivered and resolved against the **correct** source, concurrently | SC-001 / US1 |
| Close a source connection (Ctrl-C) | `remo notifier sources` no longer lists it; its poll loop stops | SC-002 / US2#2 |
| `kill -9` the source (no FIN) | Source removed within ~`idle_timeout_seconds` (45) | SC-002 / US2#3 |
| Restart the notifier | `sources` starts empty; each Feature reconnects and is served again; no approval auto-allowed in the gap | SC-003 / US2#4 |
| Point a source's `api_url` at an unreachable agentsh | That source shows `backing_off`, stays registered, recovers on its own; others unaffected | SC-004 / US4#2 |
| Add the Feature to a project; restart the notifier | Source connects, survives the restart via reconnect; removed when the container stops | SC-005 / US3 |
| Saturate to `max_sources`, then connect one more | `503 at_capacity`; existing sources unaffected; rejected source backs off and retries | FR-004 |

---

## Channel author / contributor notes

Nothing changes for channels (008): channels remain **source-unaware**. The core
fans approvals from all connected sources into the single installed channel and
routes each human decision back to the originating source via a core-minted
delivery id (so two sources' approvals never collide in the channel's callback
space). To add a channel, follow `specs/008-notifier-channels/contracts/channel-extension.md`.
