# Phase 0 Research — Addendum 001: Standing Grants

Product-level questions were settled in `/speckit.clarify` (see the addendum's
Clarifications: no bundling, offer-always, single 8h TTL, Telegram-only listing).
This document resolves the remaining **implementation** decisions. No
`NEEDS CLARIFICATION` markers remain except the external OQ1 (agentsh signing),
which only gates the *future* promotion path, not v1.

## RG1. Where the GrantStore lives and how the transport reaches it

**Decision**: `GrantStore` is instantiated in `create_app()` (one per process,
alongside the `PendingApprovals` registry). The **server** performs the
allow-capable match at intake. The store is **injected into the transport**
(constructor arg) so the Telegram transport can (a) ask `grants.propose(request)`
for picker candidates, (b) call `grants.create(grant)` on selection, and (c) back
`/rules`/`/revoke`/`/pause`. Matching (enforcement) never happens in the
transport.

**Rationale**: Keeps the security-critical match in one transport-agnostic place
(`server.py`), while the grant UI is a Telegram concern. One store owner avoids
split state. Mirrors how the registry is already owned by `create_app`.

**Alternatives**: store inside the transport (rejected — couples enforcement to a
specific transport, and the server needs it for the short-circuit); global
singleton (rejected — breaks test isolation, which relies on per-app instances).

## RG2. Candidate-generalization templates (the proposer)

**Decision**: `grants.propose(request) -> list[CandidateGrant]` returns an
ordered, **tightest-first** list of `(predicate, scope, label)` built by
deterministic templates per `operation.kind`:

- `command`: ① exact command+args → ② command + arg-prefix (`git push *`) →
  ③ command only (`git *`) → ④ (broadest) `policy_rule_name` predicate.
- `file`: ① exact path+op → ② path's directory glob + op (`{workspace}/sub/** ·
  delete`) → ③ workspace-rooted glob + op → ④ `policy_rule_name`.
- `network`: ① exact host+port → ② host **suffix** + port (`*.github.com:443`) →
  ③ `policy_rule_name`.
- `signal`: ① exact signal+target → ② `policy_rule_name`.

Each candidate is paired with the **narrowest scope covering the request** by
default (FR-G7); the picker also offers one widened scope rung. The proposer is
pure (no I/O), so it is unit-testable in isolation.

**Rationale**: Deterministic, inspectable, matches the FR-G6 ladder and the
no-bundling decision (policy_rule_name is the broadest *rung*, not a bundle).

**Alternatives**: LLM-proposed labels (deferred — must never enter the runtime
matcher; could later enrich labels only); fixed single generalization (rejected —
removes human control over breadth).

## RG3. Telegram multi-step picker without busting callback_data

**Decision**: Telegram `callback_data` is ≤ 64 bytes, too small for a predicate.
So: on **Always…** tap, the transport looks up the request's already-stored
`_Sent` entry, computes candidates via `grants.propose()`, stashes them in a
short-lived `_pending_picker[approval_id] = [candidates]`, and renders buttons
with `callback_data = pick:{approval_id}:{index}`. On the pick callback it reads
`_pending_picker[approval_id][index]`, creates the grant, resolves the approval
`allow`, and edits the message to confirm. A `pick:{approval_id}:cancel` returns
to the original Approve/Deny choice.

**Rationale**: Keeps `callback_data` tiny and within Telegram limits; the
candidate set lives in process memory keyed by the approval, consistent with how
the transport already tracks `_Sent`.

**Alternatives**: encode predicate in callback_data (rejected — 64-byte limit);
inline-query/web-app picker (rejected — overkill, needs more bot setup).

## RG4. TTL expiry + max-grants enforcement

**Decision**: Expiry is enforced **lazily at match time** (an expired grant never
matches — FR-G8) *and* swept periodically by a lightweight asyncio task started
in the FastAPI lifespan (default every 60 s) to reclaim memory and keep
`/rules` honest. `create()` enforces `max_grants` under the store lock; over the
cap it raises a typed error the transport surfaces in Telegram ("grant limit
reached"). On shutdown the store is simply dropped (no drain needed — grants are
advisory, losing them just re-prompts).

**Rationale**: Lazy check guarantees correctness even if the sweeper lags; the
sweeper is for hygiene, not correctness. Cap mirrors the pending-approval cap
(FR-034) for symmetric backpressure.

**Alternatives**: per-grant timer tasks (rejected — needless task churn);
sweep-only without lazy check (rejected — a lag window could match an expired
grant, violating FR-G8).

## RG5. Scope derivation and matching

**Decision**: A grant's scope is one of `session|workspace|project|instance|
global` with a captured `value`. Matching compares against the request fields:
`session`→`session_id`, `workspace`→`workspace`, `project`→`project`,
`instance`→`instance_id` (falling back to configured instance id), `global`→
always. A grant matches only if the request's corresponding field **equals** the
grant's captured value (or scope is `global`). If the request lacks the field the
scope keys on, it is **no match** (fail-closed).

**Rationale**: Exact-equality scope keeps matching deterministic and prevents a
grant created in project A from firing in project B. Fail-closed on missing
fields avoids accidental broadening.

**Alternatives**: hierarchical/prefix scope (deferred — adds matching complexity
not needed in v1); ignore scope (rejected — removes the primary blast-radius
control).

## RG6. Auto-approval audit + digest

**Decision**: Each short-circuit auto-approval emits a structured INFO log
`auto_approved {approval_id, grant_id, kind, command|host|path-summary,
latency_ms}` (no secrets/bodies — FR-017) and increments the grant's
`uses_count`/`last_used_at`. A digest task (lifespan, default hourly; configurable
or off) sends the authorized chat a summary ("auto-approved N ops via M grants in
the last hour") when count > 0. Digest cadence reuses the same lightweight task
loop pattern as the sweeper.

**Rationale**: Satisfies FR-G11 (no silent standing grants) without a per-event
Telegram message (which would defeat the purpose). Structural-only fields honor
the logging redaction already built.

**Alternatives**: per-auto-approval Telegram message (rejected — noise, negates
the feature); no digest (rejected — violates the "never silent" intent).

## RG7. Interaction with the existing intake flow

**Decision**: The short-circuit is the **first** step of `POST /v1/approve`,
before the shutdown/health gate and before `reserve()`. Order: parse → (if grants
enabled and not paused) `match` → on hit return `allow` (+`grant_id`) and log;
on miss fall through to the existing shutdown/health → clamp → reserve → send →
await pipeline unchanged. Timeout/capacity/duplicate logic is untouched on the
miss path.

**Rationale**: Auto-approved requests should cost nothing (no slot, no transport
call), and a paused/disabled grant system must transparently fall back to the
proven v1 behavior.

**Alternatives**: match after reserve (rejected — wastes a slot and a send for an
auto-approvable request).

## Resolved summary

| Topic | Resolution |
|-------|-----------|
| Store ownership / access | app-owned, injected into transport; match in server (RG1) |
| Candidate templates | deterministic tightest-first per kind + policy_rule_name rung (RG2) |
| Telegram picker | stash candidates, `pick:{id}:{index}` callback_data (RG3) |
| TTL + cap | lazy-at-match + periodic sweep; cap on create (RG4) |
| Scope matching | exact-equality per scope field, fail-closed on missing (RG5) |
| Audit + digest | structural INFO log + periodic digest, no secrets (RG6) |
| Intake ordering | short-circuit first, miss falls through unchanged (RG7) |
| Agentsh promotion/signing | external OQ1 — deferred, not a v1 blocker |

All v1 implementation unknowns resolved — ready for Phase 1 / tasks.
