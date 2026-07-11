# Contract — Standing Grants (Addendum 001)

Defines the grant object, the deterministic match predicate grammar, the intake
short-circuit behavior, and the Telegram interaction surface. Companion to
[`openapi.yaml`](./openapi.yaml) (which carries the additive `grant_id` field)
and the data model in `../data-model-addendum-001.md`.

## Intake short-circuit (server)

On `POST /v1/approve`, **before** the shutdown/health gate and `reserve()`:

1. If grants are disabled or globally paused → skip to the normal pipeline.
2. Else evaluate `GrantStore.match(request, now)`.
3. On a match → respond `200` immediately with `ApprovalResponse`:
   - `decision: "allow"`, `responder: "rule:{grant_id}"`,
     `reason: "auto-approved via standing grant"`, `grant_id: "{id}"`,
     `latency_ms` measured, `decided_at` now.
   - No notification is sent; no pending slot is reserved.
   - A structural audit line is logged; the grant's `uses_count` increments.
4. On no match → the parent v1 pipeline runs unchanged (clamp → reserve → send →
   await → 200/408/409/503).

**Fail-closed**: any error evaluating a grant, an expired/revoked grant, a
scope/predicate mismatch, or a missing request field the rule keys on ⇒ **no
match** ⇒ the human is prompted.

## Grant object

```json
{
  "grant_id": "uuid",
  "created_at": "2026-06-01T14:00:00Z",
  "created_by": "telegram:paulofallon",
  "expires_at": "2026-06-01T22:00:00Z",
  "source_approval_id": "uuid",
  "scope": { "type": "project", "value": "myproj" },
  "eligible": true,
  "predicate": { "kind": "command", "command": "git",
                 "args": ["push"], "args_match": "prefix" },
  "uses_count": 0,
  "last_used_at": null
}
```

`expires_at = created_at + grants.default_ttl_seconds` (default 8h). No
indefinite grants in v1. `eligible` is always `true` in v1 (reserved for a future
ineligibility denylist).

## Predicate grammar (deterministic match)

A request matches iff the grant is **active** (`now < expires_at`, not revoked),
its **scope** matches, and its **predicate** matches — all exact/deterministic
(no fuzzy/semantic matching at runtime).

**Scope match** (exact equality; `global` always; missing field ⇒ no match):

| scope.type | compares request field |
|------------|------------------------|
| session | `session_id` |
| workspace | `workspace` |
| project | `project` |
| instance | `instance_id` (or configured instance id) |
| global | — (always; only if `allow_global_scope`) |

**Predicate match** by `kind` (or by `policy_rule_name` if set — broadest rung):

| kind | fields | rule |
|------|--------|------|
| any (rule rung) | `policy_rule_name` | request `policy_rule_name` equals it |
| command | `command`, `args`, `args_match` | command equal; args by `exact` (equal) / `prefix` (request starts with `args`) / `glob` (positional glob) |
| file | `paths`, `operations` | request path matches a `paths` glob (`{workspace}` expanded) AND request op ∈ `operations` |
| network | `host`, `host_match`, `port` | host `exact`/`suffix` match AND port equal (port omitted ⇒ any) |
| signal | `signal` | signal equal |

## Telegram surface

### Approval keyboard (additive third button)

```
[✅ Approve]   [⏩ Always…]   [❌ Deny]
```
`callback_data`: `approve:{id}`, `always:{id}`, `deny:{id}`.

### "Always…" picker (second tap)

On `always:{id}`, the transport computes `GrantStore.propose(request)`
(tightest-first, **at most 4 candidates**), stashes the candidate list keyed by
`{id}`, and renders:

```
Always allow:
[<candidate 0 label>]      → pick:{id}:0
[<candidate 1 label>]      → pick:{id}:1
[<candidate 2 label>]      → pick:{id}:2
[Cancel]                   → pick:{id}:cancel
```

Labels include scope, e.g. `git push * · this project`. `callback_data` stays
≤ 64 bytes by indexing into the stashed candidates (predicate never goes in
`callback_data`).

On `pick:{id}:{index}`: create the grant (TTL applied), resolve the approval
`allow` (+`grant_id`), edit the message to confirm
(`⏩ Always: git push * · myproj · 8h · by @paulofallon`). On `pick:{id}:cancel`:
restore the original Approve/Deny keyboard. Unauthorized chat, unknown id, or
expired picker ⇒ ignored (consistent with parent FR-011/FR-012). Over the
`max_grants` cap ⇒ confirm message reports "grant limit reached" and the approval
is still allowed once (the grant just isn't created).

### Slash commands (authorized chat only)

| Command | Effect |
|---------|--------|
| `/rules` | list active grants (id, class label, scope, age, TTL, uses) with inline `[Revoke]` buttons |
| `/revoke <id>` | revoke a grant; subsequent matches re-prompt |
| `/pause` / `/resume` | global pause/resume of all auto-approval (FR-G10) |

Listing is **Telegram-only** — there is no bridge HTTP read endpoint and no CLI
lister in v1 (avoids disclosing the auto-approve set to co-located containers).

## Audit & digest

- Each auto-approval logs `auto_approved {approval_id, grant_id, kind, summary,
  latency_ms}` at INFO — **no secrets, bodies, or workspace paths** (parent
  FR-017).
- A periodic digest (default hourly; `digest_interval_seconds: 0` disables)
  messages the chat a count summary when there was activity, so standing grants
  are never silent (FR-G11).
