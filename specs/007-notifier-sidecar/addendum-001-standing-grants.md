# Addendum 001 — Standing Grants ("Always" auto-approval)

**Parent spec**: [`spec.md`](./spec.md) (Notifier Sidecar, spec 007)
**Status**: Draft (design only — no implementation)
**Created**: 2026-05-31
**Depends on**: the implemented v1 notifier (FR-001…FR-034, FR-003a, FR-010a).

## Purpose

Add a third decision to the approval flow: alongside **Approve** / **Deny**, the
human can choose **Always** — auto-approving the *class* of operation this
request belongs to, so future matching requests are answered without a Telegram
round-trip.

This extends the per-event model (today every tap approves exactly one
`approval_id`) with a **standing grant**: a human-authored, deterministic rule
the notifier matches on intake.

## Architecture decision (why the grant lives in the notifier, for now)

agentsh's `api` approval mode **delegates the approve decision to the notifier**
(it is the registered REST approver). An approver answering from a human's prior
standing instruction is within that delegated authority — it is not a shadow
policy, it is the approver having memory. agentsh has no path today for an
approver to hand a rule back into its signed/versioned policy, so the only
buildable home for the grant is the notifier.

Consequences this addendum accepts deliberately:

- The notifier becomes **`allow`-capable**. Today a notifier fault fails secure
  (no prompt → deny); with grants, a matcher fault can manufacture `allow`. The
  matcher is therefore the most security-critical code in the service and MUST
  be deterministic and exact (see FR-G4).
- Standing grants live **outside** agentsh's audited policy, so the notifier's
  own list / revoke / audit surface is the backstop (FR-G7, FR-G8).
- The v1 grant store is **in-memory, scoped, and TTL-bounded** — it fails closed
  on restart (you get re-asked), avoiding an on-disk security datastore. This
  keeps the "no durable state" property of the parent spec intact.

### Forward compatibility (promotion to agentsh policy)

The grant predicate is **shaped like an agentsh policy rule** (its
`paths`/`operations`/`match`/host+port grammar). If agentsh later grows an
"approver returns a signed rule" capability, a notifier grant promotes straight
into agentsh's signed, versioned, audited policy with no reshaping — moving the
grant from "the approver remembers" up to "it's in the audited policy," the
better long-term home. This addendum does not require that agentsh change; it
only keeps the door open.

## Decisions captured (from design discussion)

- **D1**: Grant enforcement lives in the notifier (in-memory) for v1, under the
  delegated-approver model above. Not agentsh-side yet (no such capability).
- **D2**: **Everything is generalizable** in v1 — no category that is ineligible
  for "Always." The schema carries an `eligible` flag so a denylist
  (destructive / egress / secrets) can be added later via config with no
  protocol change.
- **D3**: Grants are **in-memory, scoped, TTL-bounded, fail-closed**. No
  cross-restart persistence in v1.
- **D4**: Grant predicates are agentsh-rule-shaped for future promotion (D-fwd).
- **D5**: The matcher is deterministic/exact; the *intelligence* (proposing the
  class) happens once, at tap-time, with the human confirming.

## Clarifications

### Session 2026-06-01

- Q: Does a single "Always" cover a bundle of related syscall-level events, and how is the bundle identified? → A: No bundling in v1. The matcher is strictly per-operation; breadth comes from the human choosing a broader predicate, with `policy_rule_name` available as the broadest candidate rung (folds into FR-G6, not a new primitive).
- Q: Should "Always" be offered on first encounter or only after the class repeats N times? → A: Offer on every prompt; rely on tightest-first candidates (FR-G6) and narrow default scope (FR-G7) as guardrails. Repeat-gated offering is a future enhancement.
- Q: Single global default TTL or per-grant lifetime choice? → A: Single configurable `default_ttl_seconds` (default 8h) applied to every grant; no "until revoked" / indefinite grants in v1. Per-grant lifetime choice deferred.
- Q: How are active grants listed — bridge read endpoint, or Telegram/SSH only? → A: No bridge read endpoint in v1 (avoids disclosing the auto-approve set to co-located containers). Telegram `/rules` is the only list+revoke surface; `GET /v1/grants` and the `remo notifier rules` CLI are dropped/deferred (loopback-only admin port is the future option).

## User Scenarios

### US-G1 — Human grants "Always" for a class (Priority: P1)

A request arrives the human has seen before. Instead of Approve, they tap
**Always…**, pick a generalization + scope from a short list, and confirm. This
request is approved *and* a standing grant is created. The next matching request
returns `allow` instantly with no Telegram message.

**Independent test**: Tap Always on a request; send a second matching request →
it returns `allow` in well under a second with no notification, and the audit
log records the grant id.

### US-G2 — Human reviews and revokes standing grants (Priority: P1)

The human runs `/rules` in Telegram and sees active grants (class, scope, age,
TTL, use count). They `/revoke <id>` (or tap a revoke button); subsequent
matching requests prompt again.

**Independent test**: Create a grant, `/revoke` it, send a matching request →
the human is prompted again.

### US-G3 — Auto-approvals stay visible (Priority: P2)

Auto-approved operations are logged with their grant id, and a periodic digest
("auto-approved N ops via M rules") is delivered so standing grants are never
silent.

## Functional Requirements (additive)

### Intake short-circuit

- **FR-G1**: On `POST /v1/approve`, before reserving a slot or notifying, the
  notifier MUST evaluate the request against active standing grants. On a match,
  it MUST return `200` with `decision: allow` immediately, without sending any
  notification and without occupying a pending slot.
- **FR-G2**: An auto-approval response MUST identify its source: `responder` =
  `rule:{grant_id}` and `reason` = a fixed marker (e.g. `auto-approved via
  standing grant`). The `grant_id` MUST appear in the response so callers and
  audit can correlate.
- **FR-G3**: On no match, the existing flow (reserve → notify → await, FR-001…
  FR-010a) applies unchanged.

### The matcher (allow-capable — highest criticality)

- **FR-G4**: A request matches a grant **iff** all hold, evaluated
  deterministically (no fuzzy/semantic matching at runtime): (a) the grant is
  active (not expired, not revoked); (b) the request scope satisfies the grant
  scope (FR-G9); (c) the operation satisfies the grant predicate exactly
  (FR-G5). Any ambiguity or evaluation error MUST be treated as **no match**
  (fail-closed → prompt the human).
- **FR-G5**: The grant predicate MUST be a structured, inspectable rule over the
  operation fields (kind; for `command`: command + an args matcher of exact /
  prefix / glob; for `file`: path globs + operations; for `network`: host
  exact-or-suffix + port; for `signal`: signal + target). It MUST NOT contain
  free-form/learned matching. Predicate grammar mirrors agentsh policy rules
  (D4).

### Deriving the class (proposal, at tap-time)

- **FR-G6**: When the human taps **Always…**, the notifier MUST present a small
  ordered set of candidate generalizations — **at most 4** (tightest first) — derived from the
  request via deterministic templates per operation kind, each paired with a
  scope choice. The broadest candidate rung MAY be a `policy_rule_name`-based
  predicate (auto-approve operations agentsh attributes to the same rule, within
  scope). The human selects exactly one. The notifier MUST NOT create a grant
  broader than what the human selected, and there is **no bundling primitive** —
  every grant is matched per-operation (FR-G4/G5).

### Lifecycle, scope, limits

- **FR-G7**: Grants MUST carry a **scope** (`session` | `workspace` | `project`
  | `instance` | `global`) and the notifier MUST default new grants to the
  **narrowest scope that covers the request** unless the human explicitly widens
  it. (`global` is permitted in v1 per D2 but is never the default.)
- **FR-G8**: Grants MUST carry a **TTL** and MUST stop matching once expired.
  v1 applies a single configurable `default_ttl_seconds` (default 8h) to every
  grant; there is **no indefinite / "until revoked" grant** (per-grant lifetime
  choice is deferred). Revocation (FR-G10) is always available before expiry.
- **FR-G9**: Grant state MUST be **in-memory only**; a restart loses all grants
  and subsequent requests fail closed (re-prompt). The notifier MUST enforce a
  configurable **maximum number of active grants**, rejecting new grants beyond
  it with a clear Telegram message (mirrors the pending-approval cap, FR-034).

### Visibility, revocation, audit

- **FR-G10**: The human MUST be able to **list** active grants and **revoke** any
  grant, from Telegram (`/rules`, `/revoke <id>`), with effect on subsequent
  requests being immediate. A **global pause** ("disable all auto-approval") MUST
  be available.
- **FR-G11**: Every auto-approval MUST be logged with its `grant_id` and the
  matched operation's structural metadata (never secrets/bodies — FR-017), and
  the notifier SHOULD deliver a periodic digest of auto-approval activity.

### Configuration

- **FR-G12**: Configuration MUST include: grants enabled/disabled,
  `default_ttl_seconds`, `max_grants`, `allow_global_scope`, and
  `digest_interval_seconds` (0 disables the digest). The grant schema MUST carry
  an `eligible` flag per class so a future ineligibility denylist (D2) can be
  applied without a protocol change.

## Wire-protocol changes (additive, backward-compatible)

### `ApprovalResponse` — new optional fields

- `responder` already exists; auto-approvals set it to `rule:{grant_id}`.
- Add optional `grant_id` (string) — present on auto-approved (FR-G2) and on the
  response that *created* a grant (the "Always" tap), echoing the new grant's id.

### `Grant` (new object)

Returned to the caller on the response that creates a grant, and the internal
record the matcher evaluates. Agentsh-rule-shaped (D4):

```json
{
  "grant_id": "uuid",
  "created_at": "RFC3339",
  "created_by": "telegram:paulofallon",
  "expires_at": "RFC3339",
  "source_approval_id": "uuid",
  "scope": { "type": "project", "value": "myproj" },
  "eligible": true,
  "predicate": {
    "kind": "command | file | network | signal",
    "command": "git",
    "args_match": { "type": "prefix", "value": ["push"] },
    "paths": ["{{workspace}}/**"],
    "operations": ["delete"],
    "host": { "type": "suffix", "value": ".github.com" },
    "port": 443
  },
  "uses_count": 0,
  "last_used_at": null
}
```

(Only the predicate fields relevant to `kind` are present.)

### Endpoints

- `POST /v1/approve` — gains the FR-G1 short-circuit; response may carry
  `grant_id`. No breaking change for callers that ignore it.
- **No** new externally-mutating endpoint. Grant creation happens only via a
  human Telegram tap; grant revocation happens via Telegram (`/revoke`). This
  keeps mutation authenticated to the authorized chat and off the unauthenticated
  bridge port.
- **No `GET /v1/grants` in v1** (resolved OQ4): a bridge-bound read endpoint
  would disclose the auto-approve set to co-located containers — the very agents
  being gated. Listing is **Telegram `/rules` only**. A future host-loopback-only
  admin port could back a CLI lister without bridge exposure.

## Telegram UX

Inline keyboard becomes a two-tap flow:

```
[✅ Approve]   [⏩ Always…]   [❌ Deny]
```

- **Always…** edits the message into a granularity + scope picker built from
  FR-G6 candidates, e.g. for `git push origin main` in project `myproj`:

  ```
  Always allow:
  [git push *  · this project]
  [git *       · this project]
  [git push *  · everywhere]
  [Cancel]
  ```

  with the default TTL shown (e.g. "expires in 8h"). v1 keeps a single
  configurable TTL; per-grant TTL choice is an open question (OQ2).
- Selecting a candidate approves *this* request (allow) **and** creates the grant
  (FR-G7/G8). The message is edited to confirm (`⏩ Always: git push * · myproj ·
  8h · by @paulofallon`).
- `/rules` lists active grants with inline `[Revoke]` buttons; `/revoke <id>`
  and a `/pause` global toggle (FR-G10).

## CLI additions

- **None in v1** (resolved OQ4). Grant listing and revocation are Telegram-only
  (`/rules`, `/revoke`, `/pause`). A `remo notifier rules <host>` command is
  deferred until a host-loopback-only admin surface exists, to avoid exposing the
  grant set on the bridge.

## Config additions (sketch)

```toml
[grants]
enabled = true
default_ttl_seconds = 28800   # 8h
max_grants = 100
allow_global_scope = true     # D2: everything generalizable in v1
```

## Security considerations

- **Allow-capable matcher** is the crown-jewel risk. Keep it exact and
  deterministic; treat any uncertainty as no-match (FR-G4). Test the matcher as
  adversarially as the fail-secure paths.
- **Scope defaults narrow** (FR-G7) and **TTL bounds every grant** (FR-G8): the
  two compensating controls that carry the safety budget now that everything is
  eligible (D2). `global` is opt-in, never default.
- **Fail-closed everywhere**: grant store unavailable, ambiguous match, expired,
  or post-restart → prompt, never auto-allow.
- **Revocation + visibility** (FR-G10) is the human's backstop since grants sit
  outside agentsh's audited policy.
- **No secrets in grant logs** (FR-017 still applies).
- **Authority**: in v1's single-chat model, the one authorized chat mints and
  revokes grants. Multi-chat authority is out of scope (parent spec constraint).

## Out of scope (this addendum)

- Cross-restart / on-disk grant persistence (revisit only if "forgot my Always
  after redeploy" proves painful).
- Promotion of grants into agentsh's signed policy (future; requires an agentsh
  capability that does not exist today — see Forward compatibility).
- A category **ineligibility** denylist (schema-ready via `eligible`, but no
  enforced denylist in v1 per D2).
- LLM/semantic class proposal. Candidate generation is deterministic templates
  in v1; an LLM could later *suggest* labels but MUST NOT enter the runtime
  matcher.
- "Offer Always only after N repeats" behavioral heuristic (nice-to-have; see
  OQ3).

## Open questions

- **OQ1 (agentsh)**: Does agentsh's `api` approval response have any extension
  point, and what is its signing model for programmatically-added rules? This
  determines if/when grants can be promoted into signed policy.
- **OQ2 (TTL)**: ✅ Resolved (2026-06-01) — single configurable
  `default_ttl_seconds` (default 8h) for every grant; no indefinite grants in
  v1. See Clarifications.
- **OQ3 (offer timing)**: ✅ Resolved (2026-06-01) — offer on every prompt;
  guardrails are tightest-first candidates + narrow default scope. Repeat-gated
  offering deferred. See Clarifications.
- **OQ4 (read exposure)**: ✅ Resolved (2026-06-01) — no bridge read endpoint;
  listing is Telegram `/rules` only; CLI lister deferred to a future
  loopback-only admin port. See Clarifications.
- **OQ5 (match granularity)**: ✅ Resolved (2026-06-01) — no bundling in v1; the
  matcher is per-operation, with `policy_rule_name` available as the broadest
  candidate predicate rung. See Clarifications.

## Success criteria

- [x] **SC-G1**: After a human grants Always for a class, a subsequent matching
  request returns `allow` with no Telegram traffic. *(Verified: short-circuit
  returns allow with no transport send — `test_grant_short_circuit_allows`. The
  <500 ms bound is an in-memory dict+predicate match, not separately asserted —
  accepted finding G2.)*
- [x] **SC-G2**: No request is ever auto-approved except by an active, unexpired,
  unrevoked, human-created grant whose deterministic predicate + scope it
  satisfies; every other path still prompts or fails closed. *(Verified:
  `test_grants.py` matcher suite + server fall-through/disabled/paused tests.)*
- [x] **SC-G3**: A human can list and revoke any standing grant and globally pause
  auto-approval, with immediate effect. *(Verified: `test_cmd_rules_revoke_pause`.)*
- [x] **SC-G4**: Every auto-approval is individually auditable by `grant_id`, with
  no secrets in the record. *(Verified: `test_auto_approval_audit_log_has_no_secrets`.)*
- [x] **SC-G5**: A restart clears all grants and the system fails closed
  (re-prompts). *(Verified: `test_fresh_store_is_empty_restart_fail_closed`.)*

All SC-G verified via the local test suite (108 notifier tests, 91% coverage).
No live-host/bot acceptance was required for grants — the loop is fully
exercised against a fake transport and a mocked Bot.
