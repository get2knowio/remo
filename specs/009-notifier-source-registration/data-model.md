# Phase 1 Data Model: Notifier Source Registration

All entities are **in-memory only** (FR-001/FR-013; 007 FR-009). Wire payloads are
Pydantic v2 models (`extra="forbid"` for inbound trust boundaries); runtime
registry objects are dataclasses holding asyncio state. New types live in
`src/remo_cli/notifier/models.py` (wire/response) and
`src/remo_cli/notifier/sources/` (runtime).

---

## SourceRegistration (wire payload — inbound)

The JSON body of `POST /v1/sources`. Validated strictly; this is the
trust-boundary input from a (co-located, unauthenticated) source.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `source_id` | `str` | yes | Stable id, 1:1 with a devcontainer. `^[A-Za-z0-9._-]{1,64}$`. Used for reconcile (FR-003). |
| `api_url` | `str` (URL) | yes | Notifier-reachable agentsh approvals base URL (e.g. `http://proj-a:8080`). |
| `api_key` | `str` | yes | Approver-role `X-API-Key`, sent **inline** (clarified). Held in-memory only; never logged, never persisted. |
| `labels` | `dict[str, str]` | no | Optional human-facing labels for the status surface (FR-020). Bounded (≤16 keys). |

**Validation / rules**: `extra="forbid"`; `source_id` pattern-checked; `api_url`
must be http(s); `api_key` non-empty. A malformed payload ⇒ `400` (the connection
is never registered). `api_key` is excluded from all structured logs and from
`SourceStatus`.

---

## Source (runtime — `sources/source.py`)

One registered agentsh endpoint and its live poll machinery. Dataclass; not
serialized.

| Field | Type | Notes |
|-------|------|-------|
| `source_id` | `str` | Key in the registry. |
| `api_url` | `str` | From registration (or `[agentsh]` for the seed). |
| `api_key` | `str` | Held in-memory; redacted in `repr`. |
| `labels` | `dict[str, str]` | Optional. |
| `epoch` | `int` | Monotonic per-`source_id` generation; "latest connection wins" + stale-cleanup guard (R2). Seed = `0`. |
| `permanent` | `bool` | `True` only for the seed source — connection-drop logic never removes it (FR-005/R7). |
| `client` | `AgentshClient` | Per-source approver client (`api_url`,`api_key`). |
| `health` | `PollHealth` | Poll-health state (below). |
| `task` | `asyncio.Task \| None` | The running `SourcePoller` loop. |

**Lifecycle**: created on `register()`; `task` started immediately; removed when
its presence connection drops (non-permanent) — `task` cancelled, `client.stop()`
awaited, in-flight approvals drained (FR-009/R9). Lives exactly as long as its
presence connection (or forever, if `permanent`).

---

## PollHealth (runtime — embedded in `Source`)

Per-source poll-health bookkeeping (FR-014/FR-015).

| Field | Type | Notes |
|-------|------|-------|
| `poll_state` | `"polling" \| "backing_off"` | Derived from `consecutive_failures`. |
| `consecutive_failures` | `int` | Reset to 0 on a successful poll. |
| `current_backoff_seconds` | `float` | `min(cap, base*factor**failures)` pre-jitter; `base` when healthy. |
| `last_success_at` | `datetime \| None` | Last successful `GET /api/v1/approvals`. |

**Transitions**: success ⇒ `polling`, `failures=0`, backoff=`base`. Failure ⇒
`failures+=1`, `backing_off`, backoff grows to `cap`. Never affects registration
(only the presence connection does).

---

## SourceRegistry (runtime — `sources/registry.py`)

The in-memory, bounded, lock-guarded set of sources on one notifier.

- **State**: `dict[str, Source]`, `asyncio.Lock`, `max_sources`, a per-`source_id`
  epoch counter, and a reference to the shared `PendingApprovals` (for
  source-scoped drain).
- **`register(reg) -> Source`**: atomic. If `source_id` exists ⇒ reconcile
  (bump epoch, cancel old task, supersede — latest wins, FR-003); else if
  `len >= max_sources` ⇒ raise `AtCapacity` (caller returns `503`, FR-004);
  else create + start. Returns the live `Source` with its `epoch`.
- **`remove(source_id, epoch)`**: epoch-guarded. No-op if the current source's
  epoch ≠ `epoch` (a stale connection's cleanup) or if `permanent`. Otherwise
  cancel task, stop client, `pending.drain_source(source_id)` to fail-secure deny
  (R9), delete.
- **`snapshot() -> list[SourceStatus]`** and **`count()`** for the status surface.
- **`drain_all()`** on shutdown.
- **Never persisted.** Starts empty on restart (FR-013).

---

## PresenceConnection (conceptual — the held-open stream)

Not a stored object: it *is* the `POST /v1/sources` `StreamingResponse` generator
(R1). Its existence ⇔ the source's registration; its drop (FIN/RST, failed
keepalive write, or `is_disconnected()` past `idle_timeout`) ⇔ de-registration.
Carries the `SourceRegistration` (request body) and the source's `epoch` (so its
cleanup is epoch-guarded). Guarded by the transport keepalive/idle timeout — the
only liveness timer (FR-007/FR-008).

---

## DeliveryMapping (runtime — in `PendingApprovals`, R3)

Maps a core-minted, colon-free **delivery id** to its origin so a human's tap
resolves against the correct source.

| Field | Type | Notes |
|-------|------|-------|
| `delivery_id` | `str` | `uuid4().hex`; the id the channel/registry sees. |
| `source_id` | `str` | Owning source. |
| `epoch` | `int` | Owning source's epoch (drop late callbacks for a superseded source). |
| `agentsh_approval_id` | `str` | The **real** agentsh id used to resolve on the wire. |

`drain_source(source_id)` resolves every pending entry whose mapping matches
`source_id`.

---

## SourceStatus (wire/response — outbound)

One row of `GET /v1/sources`. Pydantic; **excludes `api_key` and `api_url`**.

| Field | Type | Notes |
|-------|------|-------|
| `source_id` | `str` | |
| `labels` | `dict[str, str]` | |
| `poll_state` | `str` | `"polling"` / `"backing_off"`. |
| `last_success_at` | `datetime \| None` | |
| `consecutive_failures` | `int` | |
| `permanent` | `bool` | Seed source marker. |

---

## SourcesConfig (config — `[sources]` in notifier.toml)

New strict (`extra="forbid"`) config section. Defaults per research R5.

| Key | Type | Default | Validation |
|-----|------|---------|------------|
| `max_sources` | `int` | `64` | `ge=1` |
| `keepalive_interval_seconds` | `int` | `15` | `ge=1` |
| `idle_timeout_seconds` | `int` | `45` | `> keepalive_interval_seconds` |
| `poll_base_interval_seconds` | `int` | `5` | `ge=1` |
| `poll_backoff_factor` | `float` | `2.0` | `ge=1.0` |
| `poll_backoff_cap_seconds` | `int` | `300` | `>= poll_base_interval_seconds` |
| `poll_backoff_jitter` | `float` | `0.2` | `0.0 <= x <= 1.0` |

`[agentsh]` (008) becomes **optional**: when present it seeds one permanent source
(`source_id` defaults to `"seed"`, configurable); when absent the registry starts
empty. `[agentsh].poll_interval_seconds` continues to mean the seed's base poll
interval.

---

## Modified existing types

- **`HealthResponse`** (`models.py`): add `sources: int` (count of registered
  sources). `agentsh_connected` retained, redefined as "≥1 source currently
  polling successfully" for back-compat with the existing health probe.
- **`PendingApprovals`** (`state.py`): add the delivery-id mapping (R3) and
  `drain_source(source_id)`; `reserve()` keyed by `delivery_id`; the global
  `max_pending` cap is retained across all sources.
- **`AgentshConfig`** (`config.py`): made optional in `NotifierConfig`; gains an
  optional `source_id` (default `"seed"`).
