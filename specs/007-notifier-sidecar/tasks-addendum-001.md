# Tasks — Addendum 001: Standing Grants ("Always")

**Input**: `addendum-001-standing-grants.md`, `plan-addendum-001.md`,
`research-addendum-001.md`, `data-model-addendum-001.md`,
`contracts/grant-schema.md`, `contracts/openapi.yaml` (updated), `quickstart.md`.

**Tests**: INCLUDED — the parent spec requests tests (§9) and SC-G1…SC-G5
require them. Test tasks precede their implementation within each story.

**Scope**: Additive to the shipped v1 notifier. Task IDs are namespaced `TA###`
to avoid collision with the base `tasks.md` (T001–T046). All paths repo-relative.

**Shared-file note**: `transports/telegram.py` is touched by US-G1, US-G2, and
US-G3; `server.py` by Foundational + US-G3; `tests/notifier/test_telegram.py` by
US-G1/US-G2/US-G3. Same-file tasks are sequenced, not parallel — flagged below.

---

## Phase 1: Setup

- [X] TA001 Add the `[grants]` config block to `GrantsConfig` planning surface: extend `src/remo_cli/notifier/config.py` with a `GrantsConfig` Pydantic model (`extra="forbid"`: `enabled` bool=true, `default_ttl_seconds` int≥1=28800, `max_grants` int≥1=100, `allow_global_scope` bool=true, `digest_interval_seconds` int≥0=3600) and add `grants: GrantsConfig = Field(default_factory=GrantsConfig)` to `NotifierConfig`. (data-model-addendum §GrantsConfig; FR-G12)
- [X] TA002 [P] Add the rendered `[grants]` section to `ansible/roles/remo_notifier/templates/notifier.toml.j2` (enabled/default_ttl_seconds/max_grants/allow_global_scope/digest_interval_seconds from role vars).
- [X] TA003 [P] Add grant defaults to `ansible/roles/remo_notifier/defaults/main.yml` (`remo_notifier_grants_enabled: true`, `_default_ttl_seconds: 28800`, `_max_grants: 100`, `_allow_global_scope: true`, `_digest_interval_seconds: 3600`).
- [X] TA003a [P] Add `GrantsConfig` validation cases to `tests/notifier/test_config.py`: a `[grants]` block parses with defaults; an unknown `[grants]` key is rejected (`extra="forbid"`); `default_ttl_seconds < 1` and `max_grants < 1` are rejected; `digest_interval_seconds = 0` is accepted (disables digest). (validates TA001; C1 / Constitution IV parity)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The grant domain model + store + matcher + proposer that every story
depends on. **No story work begins until this is complete.**

- [X] TA004 Create `src/remo_cli/notifier/grants.py` models: enums (`GrantScopeType`, `ArgMatchType`, `HostMatchType`), `GrantPredicate`, `GrantScope`, `Grant`, `CandidateGrant` (Pydantic v2; reuse `OperationKind` from `models.py`). Include `predicate.matches(operation, policy_rule_name)`, `scope.matches(request)`, `grant.active(now)`, `grant.matches(request, now)` — all deterministic and fail-closed per data-model-addendum + grant-schema. (FR-G4/G5)
- [X] TA005 Implement `GrantStore` in `src/remo_cli/notifier/grants.py`: in-memory `dict[str,Grant]` + `asyncio.Lock` + `max_grants` + `paused`; methods `match(request, now)`, `create(grant)` (raises `GrantLimitReached` at cap), `list()`, `revoke(id)`, `set_paused()`, `sweep(now)`. (FR-G4/G7/G8/G9/G10; research RG1/RG4/RG5)
- [X] TA006 Implement `GrantStore.propose(request) -> list[CandidateGrant]` in `src/remo_cli/notifier/grants.py` (pure): deterministic tightest-first templates per kind + `policy_rule_name` broadest rung, each paired with the narrowest covering scope plus one widened rung; **return at most 4 candidates**. (FR-G6; research RG2)
- [X] TA007 [P] Add optional `grant_id: str | None = None` to `ApprovalResponse` in `src/remo_cli/notifier/models.py`. (data-model-addendum; backward-compatible)
- [X] TA008 [P] Unit tests `tests/notifier/test_grants.py`: predicate match per kind (command exact/prefix/glob, file path-glob+op, network exact/suffix+port, signal, policy_rule_name rung); scope equality + missing-field fail-closed; `active()`/expiry; `match()` returns None when paused/expired/revoked/mismatch; `create()` cap → `GrantLimitReached`; `revoke()`; `sweep()`; `propose()` ordering tightest-first; concurrent `create()` respects cap via `asyncio.gather`; **a freshly constructed `GrantStore` is empty (SC-G5 restart fail-closed: a new process holds no grants → re-prompts).** (validates TA004–TA006)

**Checkpoint**: grant model, store, matcher, proposer importable and unit-tested.

---

## Phase 3: User Story G1 — Human grants "Always" for a class (Priority: P1) 🎯 MVP

**Goal**: Tapping **Always…** approves this request and creates a standing grant; a subsequent matching request is auto-approved server-side with no Telegram.

**Independent Test**: Tap Always on a request; send a second matching request → `200 allow` in <500 ms, `responder: rule:{id}`, `grant_id` set, no Telegram, audit line logged.

### Tests for US-G1 (write first)

- [X] TA009 [P] [US-G1] Server short-circuit tests in `tests/notifier/test_server.py`: with a grant pre-seeded in `app.state.grant_store`, a matching `POST /v1/approve` returns 200 `allow` with `responder` `rule:{id}` + `grant_id`, **no** transport send and **no** pending slot reserved; a non-matching request falls through to the existing flow; with grants disabled or paused the short-circuit is skipped. (validates TA011; FR-G1/G2/G3, research RG7)
- [X] TA010 [P] [US-G1] Telegram "Always" flow tests in `tests/notifier/test_telegram.py` (Bot mock): approval keyboard now includes `always:{id}`; tapping it renders a picker with `pick:{id}:{index}` candidates from `propose()`; selecting one calls `grant_store.create(...)` and resolves the approval `allow` (+grant_id); `pick:{id}:cancel` restores Approve/Deny; unauthorized chat / unknown id ignored; at `max_grants` the approval still allows once but reports the limit. (validates TA012/TA013; grant-schema "Telegram surface")

### Implementation for US-G1

- [X] TA011 [US-G1] Wire the intake short-circuit into `src/remo_cli/notifier/server.py`: construct `GrantStore` in `create_app` (expose as `app.state.grant_store`), inject it into the transport; at the top of `POST /v1/approve` (before shutdown/health gate and `reserve`), if grants enabled and not paused, `match()` → on hit return 200 `allow` (+`grant_id`, responder `rule:{id}`), increment `uses_count`, emit the structural audit log; on miss fall through unchanged. Start the TTL `sweep` loop in the lifespan. (FR-G1/G2/G3/G11; research RG4/RG6/RG7)
- [X] TA012 [US-G1] Add the third button + picker to `src/remo_cli/notifier/transports/telegram.py`: render `[✅ Approve] [⏩ Always…] [❌ Deny]`; on `always:{id}` compute `grant_store.propose(request)`, stash candidates keyed by approval id, render `pick:{id}:{index}` + Cancel buttons (labels include scope); keep `callback_data` ≤64 bytes. (research RG3; grant-schema)
- [X] TA013 [US-G1] Handle the pick callback in `transports/telegram.py`: on `pick:{id}:{index}` create the grant (TTL applied via config), resolve the approval `allow` with `grant_id`, edit the message to confirm (`⏩ Always: <label> · <ttl> · by @user`); on `pick:{id}:cancel` restore the original keyboard; surface `GrantLimitReached` as a "grant limit reached" notice while still allowing this one. (FR-G6/G7/G8; grant-schema)

**Checkpoint**: full Always → auto-approve loop works end-to-end (local bot).

---

## Phase 4: User Story G2 — Review & revoke standing grants (Priority: P1)

**Goal**: `/rules` lists active grants with revoke buttons; `/revoke <id>`, `/pause`, `/resume` take effect immediately.

**Independent Test**: Create a grant, `/revoke` it (or tap Revoke), send a matching request → prompted again. `/pause` → all matches prompt; `/resume` → restored.

### Tests for US-G2 (write first)

- [X] TA014 [US-G2] Command tests in `tests/notifier/test_telegram.py` (sequential — shared file, after TA010): `/rules` lists active grants with inline `[Revoke]` buttons and a not-empty/empty rendering; `/revoke <id>` and a Revoke-button callback remove the grant (`grant_store.revoke` called); `/pause` sets paused, `/resume` clears it; all reject a non-authorized chat. (validates TA015; FR-G10)

### Implementation for US-G2

- [X] TA015 [US-G2] Add slash/command + revoke-callback handlers to `transports/telegram.py` (sequential — shared file, after TA013): register `CommandHandler`s for `/rules`, `/revoke`, `/pause`, `/resume` and a `CallbackQueryHandler` for `revoke:{id}`, all gated to the authorized chat; render grant summaries (id, class label, scope, age, TTL, uses) and call into the injected `GrantStore`. (FR-G10; grant-schema "Slash commands")

**Checkpoint**: grants are listable, revocable, and globally pausable from Telegram.

---

## Phase 5: User Story G3 — Auto-approvals stay visible (Priority: P2)

**Goal**: Auto-approvals are individually logged by `grant_id` and a periodic digest summarizes activity so standing grants are never silent.

**Independent Test**: Trigger several auto-approvals → each emits a structural INFO log with `grant_id` and no secrets; after the digest interval the chat receives a count summary.

### Tests for US-G3 (write first)

- [X] TA016 [P] [US-G3] Audit/digest tests: in `tests/notifier/test_server.py` assert each auto-approval logs `auto_approved` with `grant_id` and **no** token/body/workspace (capture structlog output); in `tests/notifier/test_telegram.py` assert the digest sender messages the chat a count summary when activity > 0 and stays silent at 0. (validates TA017/TA018; FR-G11/FR-017, SC-G4)

### Implementation for US-G3

- [X] TA017 [US-G3] Emit the structural auto-approval audit log in `server.py` (extend TA011's hit path): `auto_approved {approval_id, grant_id, kind, summary, latency_ms}` at INFO via the redaction-safe logger, where `summary` is a redacted one-line op descriptor (kind + command **name** / host / directory only — never args, bodies, secrets, or full workspace paths). (FR-G11/FR-017; research RG6)
- [X] TA018 [US-G3] Add the periodic digest sender. **The server owns the activity counter** (incremented on each short-circuit hit in `src/remo_cli/notifier/server.py`) and runs a lifespan task at interval `digest_interval_seconds` (0 disables); the Telegram transport exposes `async def send_digest(summary: str)` in `src/remo_cli/notifier/transports/telegram.py` that messages the authorized chat. When the counter > 0 since the last digest, the server calls `send_digest` and resets the counter. (FR-G11; research RG6)

**Checkpoint**: every auto-approval is auditable and a digest lands; all stories functional.

---

## Phase 6: Polish & Cross-Cutting

- [X] TA019 [P] Update `src/remo_cli/notifier/docs/wire-protocol.md`: document the `/v1/approve` short-circuit, `responder: rule:{id}`, the `grant_id` field, and the fail-closed rule. (FR-G1/G2; spec §10)
- [X] TA020 [P] Update `src/remo_cli/notifier/docs/config-schema.md` with the `[grants]` block and grant lifetime/scope semantics.
- [X] TA021 [P] Update top-level `README.md` notifier section: brief "Always / standing grants" note (`/rules`, `/revoke`, `/pause`, 8h default TTL, fail-closed on restart).
- [X] TA022 Constitution pre-commit check on the touched role files: `grep -rn '\.rc ==' ansible/roles/remo_notifier/` and `grep -rn '\.stdout' ansible/roles/remo_notifier/`; ensure `| default()` on any match. (Constitution I)
- [X] TA023 [P] Run `ruff check` and `mypy` on `src/remo_cli/notifier/`; resolve all findings. (AC-9 parity)
- [X] TA024 [P] Run `pytest tests/notifier/ --cov=remo_cli.notifier`; keep >85% coverage including `grants.py`; add tests for any gap. (AC-8 parity)
- [X] TA025 Build the image (`docker build -f notifier/Dockerfile .`) and smoke `GET /v1/health` with a config that includes `[grants]`; confirm it still starts and serves. (regression on AC-2)
- [X] TA026 Mark the addendum success criteria (SC-G1…SC-G5) checked in `specs/007-notifier-sidecar/addendum-001-standing-grants.md` once verified locally, noting any deferred to a live host/bot.

---

## Dependencies & Execution Order

- **Setup (TA001–TA003a)** → no deps; TA002/TA003/TA003a parallel after TA001.
- **Foundational (TA004–TA008)** → after Setup. TA004 → TA005 → TA006 (same file `grants.py`, sequential); TA007 [P] independent; TA008 after TA004–TA006. **Blocks all stories.**
- **US-G1 (P1, MVP)** → after Foundational. TA011 (server) and TA012/TA013 (telegram) can proceed in parallel across the two files; TA012 → TA013 sequential (same file). Tests TA009/TA010 written first.
- **US-G2 (P1)** → after US-G1 (shares `telegram.py`: TA015 after TA013; TA014 after TA010 in the shared test file).
- **US-G3 (P2)** → after US-G1 (extends TA011's hit path + adds a digest task); independent of US-G2.
- **Polish (TA019–TA026)** → after the stories they document/verify.

### Shared-file serialization
- `transports/telegram.py`: TA012 → TA013 → TA015.
- `tests/notifier/test_telegram.py`: TA010 → TA014.
- `server.py`: TA011 → TA017 (→ TA018 if server-owned counter).

---

## Parallel Opportunities

- Setup: TA002 ‖ TA003.
- Foundational: TA007 ‖ the TA004→TA005→TA006 chain; TA008 after.
- US-G1: TA009 ‖ TA010 (distinct files); then TA011 ‖ TA012 (server vs telegram).
- Polish: TA019 ‖ TA020 ‖ TA021 ‖ TA023 ‖ TA024.

### Parallel example — US-G1 tests
```bash
Task: "Server short-circuit tests in tests/notifier/test_server.py"   # TA009
Task: "Telegram Always-flow tests in tests/notifier/test_telegram.py" # TA010
```

---

## Implementation Strategy

### MVP (US-G1 only)
1. Setup (TA001–TA003) → Foundational (TA004–TA008) → US-G1 (TA009–TA013).
2. **STOP & VALIDATE**: tap Always locally, confirm a repeat request auto-approves
   < 500 ms with no Telegram and a `grant_id` (SC-G1, SC-G2). Demonstrable MVP.

### Incremental delivery
1. MVP (US-G1) → auto-approve works.
2. US-G2 → list/revoke/pause (operational safety: the backstop for grants
   outside agentsh policy).
3. US-G3 → audit + digest (never-silent).
4. Polish → docs + quality gates + image regression.

### Notes
- The matcher (TA004/TA005) is the **allow-capable** crown jewel — test it as
  adversarially as the fail-secure paths (TA008). Any ambiguity ⇒ no-match.
- No new runtime deps; no CLI changes (OQ4); no bridge read endpoint.
- External **OQ1** (agentsh signed-policy promotion) is out of scope here.

## Task count

- Setup: 4 (TA001–TA003, TA003a)
- Foundational: 5 (TA004–TA008)
- US-G1 (P1, MVP): 5 (TA009–TA013)
- US-G2 (P1): 2 (TA014–TA015)
- US-G3 (P2): 3 (TA016–TA018)
- Polish: 8 (TA019–TA026)

**Total: 27 tasks** (26 + TA003a from analysis remediation C1).
