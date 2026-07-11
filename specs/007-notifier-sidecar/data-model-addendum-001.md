# Phase 1 Data Model — Addendum 001: Standing Grants

Pydantic v2 models, `extra="forbid"` where they parse external/config input. All
times aware-UTC internally, RFC3339 on the wire. **Nothing is persisted** — these
describe in-memory state and additive wire fields. New module:
`src/remo_cli/notifier/grants.py`.

## Enums (extend existing or add)

- `OperationKind` — reused from `models.py` (`command|file|network|signal`).
- `GrantScopeType` — `session | workspace | project | instance | global`.
- `ArgMatchType` — `exact | prefix | glob`.
- `HostMatchType` — `exact | suffix`.

## GrantPredicate (`grants.py`)

The deterministic, inspectable match rule. Only fields relevant to `kind` are
set. Agentsh-rule-shaped (forward-compat).

| Field | Type | Applies to | Notes |
|-------|------|-----------|-------|
| `kind` | OperationKind | all | must equal the request operation kind |
| `command` | str \| None | command, signal | exact command string |
| `args` | list[str] | command | the reference args |
| `args_match` | ArgMatchType | command | how `args` compares (exact list / prefix / glob) |
| `paths` | list[str] | file | glob list; `{workspace}` placeholder allowed |
| `operations` | list[str] | file | e.g. `read`/`write`/`delete` |
| `host` | str \| None | network | reference host |
| `host_match` | HostMatchType | network | exact or domain-suffix |
| `port` | int \| None | network | 1–65535 |
| `signal` | str \| None | signal | signal name |
| `policy_rule_name` | str \| None | any | broadest rung: match by agentsh rule name |

**Match semantics** (`predicate.matches(operation, policy_rule_name) -> bool`),
deterministic, fail-closed on anything unexpected:
- If `policy_rule_name` is set on the predicate → match iff request
  `policy_rule_name` equals it (kind still must match). This is the broadest rung.
- Else by kind:
  - `command`: request kind==command AND command equal AND args satisfy
    `args_match` (exact: equal lists; prefix: request args start with `args`;
    glob: each pattern matches positionally).
  - `file`: kind==file AND request path matches any `paths` glob (after
    `{workspace}` expansion) AND request op ∈ `operations`.
  - `network`: kind==network AND host matches (`exact` equal / `suffix`
    `request_host.endswith(host)`) AND `port` equal (or predicate port None ⇒ any).
  - `signal`: kind==signal AND signal equal.
- Any missing/None field the rule keys on, or an unparseable glob → **False**.

## GrantScope (`grants.py`)

| Field | Type | Notes |
|-------|------|-------|
| `type` | GrantScopeType | |
| `value` | str | the captured session/workspace/project/instance id; ignored for `global` |

`scope.matches(request) -> bool`: equality of the corresponding request field
(`session`→`session_id`, etc.); `global`→True; missing field → **False** (FR-G5/RG5).
**Exception:** for `instance` scope, a request with no `instance_id` falls back
to the notifier's configured instance id (the instance is known from config, not
fail-closed); this is the one intentional deviation from the missing-field rule.

## Grant (`grants.py` + additive wire object)

| Field | Type | Notes |
|-------|------|-------|
| `grant_id` | str (uuid) | server-generated |
| `created_at` | datetime | |
| `created_by` | str | e.g. `telegram:paulofallon` |
| `expires_at` | datetime | `created_at + default_ttl_seconds` (FR-G8) |
| `source_approval_id` | str | the approval the human tapped Always on |
| `scope` | GrantScope | |
| `predicate` | GrantPredicate | |
| `eligible` | bool | always True in v1 (D2); reserved for future denylist (FR-G12) |
| `uses_count` | int | incremented on each auto-approval |
| `last_used_at` | datetime \| None | |

`grant.active(now) -> bool`: `not revoked and now < expires_at`.
`grant.matches(request, now) -> bool`: `active(now) and scope.matches(request)
and predicate.matches(request.operation, request.policy_rule_name)`.

## CandidateGrant (`grants.py`, transient)

Returned by `propose(request)` to build the Telegram picker. Not persisted, not
on the wire.

| Field | Type | Notes |
|-------|------|-------|
| `label` | str | human-facing, e.g. `git push * · this project` |
| `predicate` | GrantPredicate | |
| `scope` | GrantScope | |

## GrantStore (`grants.py`, in-memory)

State: `dict[str, Grant]` + `asyncio.Lock` + `max_grants` + `paused: bool`.

| Method | Behaviour |
|--------|-----------|
| `match(request, now) -> Grant \| None` | first active grant whose `matches()` is true; None if paused/disabled/none. Allow-capable — exact, deterministic (FR-G4). |
| `create(grant) -> Grant` | under lock; raise `GrantLimitReached` if at `max_grants`; else store. |
| `propose(request) -> list[CandidateGrant]` | deterministic tightest-first templates (RG2), at most 4 candidates. Pure. |
| `list() -> list[Grant]` | active grants (for `/rules`). |
| `revoke(grant_id) -> bool` | remove; True if existed. |
| `set_paused(bool)` / `paused` | global pause (FR-G10). |
| `sweep(now) -> int` | drop expired; return count (RG4 hygiene). |

State transitions:

```text
            create()                          revoke() / expire / restart
 (none) ───────────────▶ ACTIVE ───────────────────────────────────────▶ (gone)
                           │  ▲
              match() hit  │  │ uses_count++ , last_used_at=now
                           └──┘   (stays ACTIVE)
   paused=true ⇒ match() returns None for ALL grants (not a state change)
```

Invariants:
- `match()` returns a grant only if `active(now)` and scope+predicate match.
- An expired grant is never returned even if the sweeper hasn't run (lazy check).
- `len(store) <= max_grants`.
- No `allow` is produced by the store except via a `match()` hit on a
  human-created grant (parent FR-008, evolved).

## Wire/config additions

### `ApprovalResponse` (modify `models.py`)
- Add `grant_id: str | None = None`. Present when the response was auto-approved
  (`responder = rule:{grant_id}`) or when this response *created* a grant (the
  Always tap echoes the new grant id). Backward-compatible (optional).

### `GrantsConfig` (modify `config.py`, `extra="forbid"`)

| Key | Type | Default | Notes |
|-----|------|---------|-------|
| `enabled` | bool | true | master switch |
| `default_ttl_seconds` | int ≥ 1 | 28800 | 8h; applied to every grant (FR-G8) |
| `max_grants` | int ≥ 1 | 100 | cap (FR-G9) |
| `allow_global_scope` | bool | true | D2; if false, `global` scope is refused |
| `digest_interval_seconds` | int ≥ 0 | 3600 | 0 disables the digest |

Add `grants: GrantsConfig = Field(default_factory=GrantsConfig)` to
`NotifierConfig`.

## Relationships

- `GrantStore` 1—N `Grant`; each `Grant` 1—1 `GrantPredicate` + `GrantScope`.
- A short-circuited `ApprovalRequest` maps to exactly one matched `Grant`
  (echoed as `grant_id`); a non-matched request flows through the parent v1
  pipeline unchanged.
- `propose()` turns one `ApprovalRequest` into N `CandidateGrant`s for the picker;
  the human's pick becomes one persisted-in-memory `Grant`.
