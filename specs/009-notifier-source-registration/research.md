# Phase 0 Research: Notifier Source Registration

All NEEDS CLARIFICATION items from the spec were resolved in the `/speckit.clarify`
session (transport, key conveyance, capacity-rejection behavior, drain semantics).
This document records the remaining design decisions and the concrete default
values the clarify session deferred to planning.

Format per decision: **Decision** / **Rationale** / **Alternatives considered**.

---

## R1 — Presence connection mechanics in FastAPI/uvicorn

**Decision**: Implement the presence connection as `POST /v1/sources` whose request
body is the JSON registration payload and whose **response is a long-lived
`StreamingResponse`** (chunked HTTP/1.1) emitting application-level keepalive
ticks (a single comment/newline line) every `keepalive_interval` seconds. The
async generator that produces the ticks owns the source's lifetime: on entry it
registers the source and starts its poller; on `GeneratorExit`/cancellation or a
failed write it de-registers. Disconnect is detected two ways — a write to a
dead socket raises (uvicorn/h11 propagates it into the generator), and a periodic
`await request.is_disconnected()` check bounds detection by `idle_timeout` even
if no tick is due.

**Rationale**: Reuses the existing FastAPI/uvicorn stack with no new server or
HTTP/2 configuration (clarify decision). The generator's lifecycle gives an exact
"connection open ⇔ source registered" coupling — the registration is created and
torn down in the same scope as the stream, so the two cannot diverge (US2 core
property). Body-in / stream-out cleanly separates the one-time registration data
from the indefinite liveness signal.

**Alternatives considered**: HTTP/2 long stream + PING frames (rejected by
clarify — needs h2 server/client deps, no benefit on a local bridge); WebSocket
(extra protocol surface, same outcome); a `GET` long-poll with registration in
query/headers (loses a clean JSON body; headers are awkward for `labels`).

---

## R2 — Per-source poll-loop supervision

**Decision**: `SourceRegistry` holds one `Source` per `source_id`; each `Source`
owns an `AgentshClient` and an `asyncio.Task` running its `SourcePoller`. The
registry is guarded by an `asyncio.Lock` (same discipline as `PendingApprovals`)
so the capacity gate and duplicate-`source_id` reconcile are race-free.
Registration: `register(source)` checks capacity, reconciles a duplicate (cancel
old task, install new — "latest connection wins" via a monotonically increasing
per-`source_id` **epoch**), starts the task. De-registration: `remove(source_id,
epoch)` is epoch-guarded so a *stale* connection's cleanup never removes the
*current* registration. Poller task cancellation awaits the agentsh client's
`stop()`.

**Rationale**: One task per source isolates failure and backoff (one wedged
agentsh never stalls another source). The epoch makes reconcile and stale-cleanup
correct under reconnect races (US1 scenario 4). The lock reuses a proven pattern
already in `state.py`.

**Alternatives considered**: A single loop multiplexing all sources (rejected —
one slow endpoint's backoff would couple to others; cancellation is messier);
per-source threads (rejected — the codebase is asyncio-native).

---

## R3 — Approval identity & routing across sources

**Decision**: Routing is correct by construction — each `SourcePoller` closes over
its own `Source`, so a decision resolves against *that* source's `AgentshClient`;
there is no shared resolve path to cross-route through. For the **shared**
`PendingApprovals` registry and the channel callback space, the core assigns each
delivered approval a fresh colon-free **delivery id** (`uuid4().hex`) and keeps a
map `delivery_id → (source_id, epoch, agentsh approval id)`. The `AgentshRequest`
handed to the channel is `request.model_copy(update={"id": delivery_id})`; on the
human's tap the core looks up the mapping and resolves the **real** agentsh id
against the owning source.

**Rationale**: agentsh approval ids are unique only *within* one agentsh, so two
sources can in principle present the same id; keying the global registry/channel
on the raw id risks a false duplicate-gate hit and a mis-keyed callback. A
core-minted delivery id removes that risk entirely. It is also **colon-free**,
which matters: the Telegram channel encodes `callback_data` as
`verb:approval_id` and the "always/pick" path splits with `maxsplit=2`
(`src/remo_cli/notifier/channels/telegram/transport.py`) — a `source:uuid`
composite id would corrupt that parse and can exceed the 64-byte `callback_data`
limit. A bare `uuid4().hex` (32 chars) is safe on both counts and requires no
channel change (FR-019: channels stay source-unaware).

**Alternatives considered**: Namespacing the id as `source_id:agentsh_id`
(rejected — breaks Telegram's `:`-delimited callback parsing and risks the 64-byte
cap); per-source `PendingApprovals` instances (rejected — `max_pending_approvals`
is better kept as one global delivery cap, and the channel is shared anyway).

---

## R4 — Per-source poll-health backoff policy

**Decision**: Exponential backoff with full jitter on poll failure (connection
error, non-2xx, or auth rejection) **while the connection is up**:
`delay = min(cap, base * factor**failures)`, then sampled uniformly in
`[delay*(1-jitter), delay*(1+jitter)]`. A successful poll resets `failures = 0`
and returns to `base` (the normal poll interval). Backoff throttles the poll only;
it never de-registers (FR-014). Every backoff state is fail-secure: no successful
poll ⇒ no approvals delivered, never an allow (FR-015).

**Rationale**: Standard, well-understood policy; jitter avoids a synchronized
retry pulse when many sources' endpoints recover together. Reusing the existing
`poll_interval_seconds` as `base` keeps the healthy cadence identical to 008.

**Alternatives considered**: Fixed-interval retry (rejected — hammers a wedged
endpoint); decorrelated jitter (marginal benefit; full jitter is simpler and
sufficient here).

---

## R5 — Default configuration values (deferred from clarify)

**Decision**: Ship these defaults in `[sources]` (all configurable; validated):

| Key | Default | Meaning |
|-----|---------|---------|
| `max_sources` | `64` | Hard cap on concurrently registered sources (FR-004) |
| `keepalive_interval_seconds` | `15` | Server tick cadence on the presence stream (FR-008) |
| `idle_timeout_seconds` | `45` | No tick/disconnect-check success within this ⇒ drop (≈3 missed ticks) |
| `poll_base_interval_seconds` | `5` | Backoff base = healthy poll cadence (matches 008 `poll_interval_seconds`) |
| `poll_backoff_factor` | `2.0` | Exponential growth factor (FR-014) |
| `poll_backoff_cap_seconds` | `300` | Maximum backoff interval |
| `poll_backoff_jitter` | `0.2` | ±20% jitter band |

Feature-side reconnect defaults (in the connector, not the notifier): base `1 s`,
factor `2`, cap `30 s`, full jitter (FR-012, reconnect-storm edge case).

**Rationale**: `64` covers realistic per-host devcontainer counts with headroom;
`15 s`/`45 s` detect an ungraceful drop within ~45 s (SC-002) without chatty
keepalives; backoff base/factor/cap match common service-client defaults and the
existing healthy cadence. Validation: `idle_timeout > keepalive_interval`,
`cap >= base`, `0 <= jitter <= 1`, `factor >= 1` (fail-fast, Constitution IV).

**Alternatives considered**: Lower `max_sources` (e.g. 16) — too tight for busy
hosts; tighter idle timeout (e.g. 10 s) — risks false drops on a briefly stalled
container. All remain operator-tunable.

---

## R6 — Capacity rejection signal & reconnect-storm avoidance

**Decision**: When `register()` is at `max_sources`, `POST /v1/sources` returns
**HTTP 503** with a JSON body `{"error": "at_capacity", "detail": "...",
"max_sources": N}` **before** any stream is held open, and logs the rejection.
The Feature connector treats `503 at_capacity` (and any reconnect) as a
retry-with-backoff condition — never terminal — applying full-jitter exponential
backoff so a freed slot is eventually claimed without a tight loop or a thundering
herd after a notifier restart.

**Rationale**: An explicit, distinguishable signal (clarify decision) lets the
operator diagnose saturation and lets the connector pace itself; returning it
before holding the stream open keeps a rejected attempt cheap. Jittered backoff
addresses the reconnect-storm edge case directly.

**Alternatives considered**: Silent drop indistinguishable from a normal
disconnect (rejected by clarify); terminal/fatal rejection requiring operator
action (rejected by clarify — a slot may free up).

---

## R7 — Seed source coexistence

**Decision**: When `[agentsh]` is configured, register exactly one **seed**
`Source` at startup (`source_id = "seed"` by default, configurable via
`[agentsh].source_id`) with `epoch = 0`, no presence connection, and a
"permanent" flag so connection-drop logic never removes it; it counts toward
`max_sources`. A dynamic `POST /v1/sources` carrying the seed's `source_id`
reconciles to a connection-backed source (latest wins) per the normal rule. When
`[agentsh]` is absent, the notifier starts with an empty registry and serves only
dynamic sources.

**Rationale**: Preserves 008 single-devcontainer/back-compat setups with no
connection step (FR-005) while making the dynamic registry the primary path. The
permanent flag is the only special case and is small and explicit.

**Alternatives considered**: Auto-opening a loopback presence connection for the
seed (rejected — needless; the seed has no client to reconnect it); dropping
`[agentsh]` entirely (rejected — breaks back-compat, violates FR-005).

---

## R8 — Devcontainer Feature mechanics

**Decision**: Ship `features/remo-notifier-source/` as a standard devcontainer
Feature: `devcontainer-feature.json` (options below), an idempotent `install.sh`
that drops `remo-source-connect.sh` into the image and registers a background
startup hook, and the connector itself — a POSIX `sh` loop that holds
`POST /v1/sources` open with `curl --no-buffer` (streaming the keepalive ticks)
and, on any exit, reconnects with full-jitter exponential backoff. Options:
`notifier_address` (default the bridge `host:port`, e.g. `172.17.0.1:18181`),
`agentsh_api_url` (notifier-reachable), `api_key` **or** `api_key_file`
(sent **inline** in the payload per the clarify decision; `api_key_file` is read
at connect time so the secret need not sit in `devcontainer.json`), optional
`labels`, and `source_id` (default the container hostname). The connector runs as
the container user; it preflights its required options and exits non-zero with a
clear message if `notifier_address` / `agentsh_api_url` / a key source is missing.

**Rationale**: `sh` + `curl` are universally present in Debian/Ubuntu devcontainer
bases, so the Feature adds no runtime language dependency. The connector's
reconnect loop is exactly the source-side responsibility in FR-012; backoff/jitter
satisfies the reconnect-storm edge case. `api_key_file` keeps the inline-on-the-
wire key out of committed `devcontainer.json` while still sending it inline over
the trusted bridge (consistent with the clarified key-conveyance decision).

**Alternatives considered**: A Python connector (rejected — heavier, and not all
bases ship a suitable Python; `curl` is leaner); a systemd/service-manager unit
inside the container (rejected — devcontainers don't reliably run an init);
modifying agentsh to self-register (rejected — Out of Scope, FR-017).

---

## R9 — In-flight drain semantics on source removal

**Decision**: Per the clarify decision, removal **locally abandons** the source's
in-flight approvals: cancel each pending channel prompt and resolve its Future to
a fail-secure deny so no allow is ever delivered — a guarantee that holds
regardless of agentsh reachability. The notifier additionally attempts a
best-effort `POST` deny to that source's agentsh only if the endpoint is still
reachable; failure of that attempt does not affect removal or the local deny.
`PendingApprovals` gains a `drain_source(source_id)` that resolves only that
source's entries (found via the delivery-id map, R3).

**Rationale**: Matches the clarified semantics exactly; keeps the fail-secure
guarantee purely local (a dropped devcontainer usually took its agentsh with it,
so an outbound resolve would fail anyway). Source-scoped drain avoids touching
other sources' pending approvals.

**Alternatives considered**: Always attempt an outbound deny (rejected by
clarify — wasteful when agentsh is gone); pure local abandon with no outbound
attempt ever (rejected — a best-effort deny is cheap and tidy when reachable).

---

## R10 — Status / observability surface

**Decision**: Add `GET /v1/sources` returning `{count, sources: [SourceStatus]}`
where `SourceStatus = {source_id, labels, poll_state ("polling"|"backing_off"),
last_success_at, consecutive_failures, permanent}`; extend `HealthResponse` with
a `sources` count (keep `agentsh_connected` meaning "any source polling OK" for
back-compat). Removed sources simply vanish from the snapshot. CLI adds
`remo notifier sources <host>` (curls `GET /v1/sources` over SSH, like `status`).

**Rationale**: Satisfies FR-020/US4 with a dedicated detail endpoint plus a count
on the existing health body; reuses the existing SSH-curl CLI pattern with no new
laptop deps.

**Alternatives considered**: Folding the full source list into `/v1/health`
(rejected — bloats the health probe Ansible polls); a streaming status feed
(over-engineered for a day-2 read).
