# Tasks: Discovery Resilience — Tolerate Intermittent Instance Connectivity

**Input**: Design documents from `/specs/024-discovery-resilience/`
**Prerequisites**: plan.md, spec.md, research.md (decisions D1-D10), data-model.md, contracts/instance-out-stale.md, quickstart.md

**Tests**: Included — the spec's FRs demand test-backed verification and Constitution VI requires every skip/fail path covered. Backend async tests MUST carry explicit `@pytest.mark.asyncio` (strict mode).

**Organization**: By user story. US1 (P1) is the MVP: backend retention + timeout budget + contract regeneration alone stops terminal teardown during a blink.

## Phase 1: Setup

- [X] T001 Verify toolchains: `uv sync --all-extras` and `cd frontend && npm ci` complete; baseline `uv run pytest tests/unit/web/test_discovery.py` and `cd frontend && npx vitest run src/components/railModel.test.ts` are green before any change (record any pre-existing failures) — baseline: 12/12 backend passed, 19/19 frontend passed, no pre-existing failures

## Phase 2: Foundational (blocking prerequisites for all stories)

- [X] T002 [P] Extend `DiscoverySnapshot` in `src/remo_cli/models/discovery.py` with additive fields `stale: bool = False`, `last_ok_at: str | None = None`, `consecutive_failures: int = 0` per data-model.md; fix the stale "immutable" docstring claim to describe the real contract (treated as immutable by convention: stored snapshots are never mutated in place, always replaced)
- [X] T003 [P] Add `discovery_offline_grace_s` to `WebSettings` in `src/remo_cli/web/config.py`: `field(default_factory=lambda: _env_float("DISCOVERY_OFFLINE_GRACE_S", 120.0))` (env var `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S`), plus `__post_init__` validation `>= 0` raising `WebConfigError` (follow the `host_stats_ttl_s` precedent at config.py:220-224); docstring cites spec FR-004
- [X] T004 [P] Config tests in `tests/unit/web/test_config.py` (create the file if it does not exist; else extend the existing config test module): default 120.0, env override parses, blank/garbage env falls back to default, negative value raises `WebConfigError`, zero is accepted

**Checkpoint**: `uv run pytest tests/unit/web/` green.

## Phase 3: User Story 1 — Open terminals survive a discovery blink (P1) — MVP

**Goal**: One retryable discovery failure never clears targets, never de-authorizes terminal open/reattach, and serves a stale-marked `ok` instance.
**Independent test**: quickstart.md "Targeted test runs" backend line + drift gates.

- [X] T005 [US1] In `src/remo_cli/web/discovery.py` add module constant `_RETRYABLE_STATUSES = frozenset({InstanceStatus.TIMEOUT, InstanceStatus.UNREACHABLE})` and a pure, unit-testable merge function (e.g. `_merge_snapshot(prior, fresh, *, now_monotonic, last_ok_monotonic, grace_s) -> DiscoverySnapshot`) implementing data-model.md's state transitions exactly: fresh success → as-is + `last_ok_at`=its `refreshed_at`, counter 0; retryable + prior OK + grace open (budget > 0 AND `now - last_ok <= grace_s`) → NEW retained snapshot (prior's `capability`/`targets`/`region`/`last_ok_at`; fresh's `error`/`refreshed_at`/identity fields; `status=OK`, `stale=True`, counter = prior.consecutive_failures + 1); retryable otherwise → fresh failure carrying forward `last_ok_at` and counter+1, `stale=False`; non-retryable → fresh as-is with `stale=False`, counter 0, `last_ok_at` carried forward. NEVER mutate `prior` (readers alias it — research.md D2)
- [X] T006 [US1] Wire retention into `DiscoveryService`: add `_last_ok_monotonic: dict[str, float]` and an injectable monotonic clock (constructor param `monotonic: Callable[[], float] = time.monotonic`) for tests; in `_run_and_store` (discovery.py:476-480), under the existing `self._lock`, pass the fresh snapshot through the merge against `self._snapshots.get(instance_id)` before storing, record `_last_ok_monotonic` on success; drop the bookkeeping entry in `evict` (discovery.py:506) and in the deregistered-host prune (discovery.py:487-496). Keep reads lock-free and `_rebuild_target_index`'s fresh-dict + single-swap pattern untouched
- [X] T007 [US1] Fix the outer timeout budget in `_discover_one` (discovery.py:255-258): `asyncio.wait_for(..., timeout=2 * settings.discovery_timeout_s + 5.0)` via a small named helper (e.g. `_total_probe_budget_s(settings)`), and update the timeout `TypedError` message (discovery.py:266) to name the total budget instead of the per-call value; per-call timeouts at discovery.py:139-140 unchanged (research.md D5)
- [X] T008 [US1] Extend `InstanceOut` in `src/remo_cli/web/api/hosts.py` (lines 101-109) with `stale: bool = False`, `last_ok_at: str | None = None`, `consecutive_failures: int = 0`, and map them from the snapshot in `_instance_out` (hosts.py:193-223); field docstrings cite contracts/instance-out-stale.md
- [X] T009 [US1] Regenerate the generated artifacts (Principle IV): `uv run python scripts/export_openapi.py` then `cd frontend && npm run generate:types`; verify `frontend/src/api/generated/openapi.json` + `schema.d.ts` show the three new fields and `terminal-frames.*` are byte-unchanged
- [X] T010 [P] [US1] Retention tests in `tests/unit/web/test_discovery.py`: (a) retryable failure with prior OK inside grace serves `status=ok`, `stale=True`, prior targets + capability retained, counter increments, `error` carries the fresh failure; (b) `find_target`/`get_targets` still resolve the retained targets (the terminals-authorization seam); (c) recovery: subsequent success clears `stale`, resets counter, updates `last_ok_at`, replaces targets; (d) the stored prior snapshot object is not mutated (aliasing guard); (e) a manual force-refresh cycle whose probe fails retryably is retained identically — retention lives at the store site, not the trigger (spec edge case); use the injectable monotonic clock, direct `WebSettings(...)` construction, explicit `@pytest.mark.asyncio`
- [X] T011 [P] [US1] Timeout-budget test in `tests/unit/web/test_discovery.py`: with `discovery_timeout_s` small, a probe whose first call consumes ~1x the per-call budget and whose second call completes promptly yields OK (previously spurious TIMEOUT); assert the outer budget helper returns `2 * t + 5.0` and the timeout error message names the total budget
- [X] T012 [P] [US1] Mapping tests in `tests/unit/web/test_hosts_mapping.py`: `_instance_out` maps `stale`/`last_ok_at`/`consecutive_failures` through; defaults (`False`/`None`/`0`) for a plain snapshot
- [X] T013 [US1] Drift gates green: `uv run pytest tests/unit/test_schema_drift.py` and `cd frontend && npm run check:types-fresh`

**Checkpoint**: US1 independently shippable — backend keeps targets alive through a blink; console (unchanged yet) simply stops seeing the error because status stays ok.

## Phase 4: User Story 2 — Staleness reads as a badge, not an outage (P2)

**Goal**: Rail renders a stale instance dimmed with rows intact and a "not responding · retrying" chip; error presentation untouched for real failures.
**Independent test**: `cd frontend && npx vitest run src/components/railModel.test.ts src/components/SessionRail.test.tsx`.

- [X] T014 [US2] Add `isStale: boolean` to `RailGroup` in `frontend/src/components/railModel.ts` (types at :64-78), computed in `buildRailModel` as `instance.status === "ok" && instance.stale === true`; `isError` (:167) byte-identical; the generated `DiscoveryInstance` type (via `frontend/src/api/client.ts`) already carries `stale` after T009
- [X] T015 [US2] Render staleness in `frontend/src/components/SessionRail.tsx`: when `group.isStale`, add a dimmed modifier class on the group container and a compact chip with the exact copy "not responding · retrying"; rows render unchanged; add the supporting CSS (dimming via opacity/muted tokens) in the stylesheet SessionRail already uses — follow the existing `.rail-inst-error` naming (e.g. `.rail-inst-stale`), light/dark-safe via existing theme tokens
- [X] T016 [P] [US2] Tests in `frontend/src/components/railModel.test.ts`: stale-ok instance → `isStale=true`, `isError=false`, rows populated, targets openable; non-ok instance with error → `isError=true`, `isStale=false` (unchanged path); fresh ok → both false
- [X] T017 [P] [US2] Tests in `frontend/src/components/SessionRail.test.tsx`: stale group renders the chip text "not responding · retrying" + dimmed class + all rows; errored group still renders the `.rail-inst-error` block; recovered group renders neither

**Checkpoint**: rail never shows the red block for a graced blip; `npm run lint` clean.

## Phase 5: User Story 3 — Panes never depend on discovery liveness (P2)

**Goal**: An open pane survives its target vanishing from discovery — during grace, after it, forever — with the card's own `connectionState` as the liveness truth.
**Independent test**: `cd frontend && npx vitest run src/components/WorkspacePane.test.tsx`.

- [X] T018 [US3] Sticky last-known-target map in `frontend/src/components/WorkspacePane.tsx`: a `useRef<Map<string, SessionTarget>>`; in the resolution memo (:140-151), refresh the sticky entry for every attached id that resolves via `targetsById`, and fall back to the sticky entry when the live lookup misses (apply the same fallback to `activeTarget` (:199) and `maximized` (:172-173) resolution); delete the entry when a pane is closed (the detach/close handler); ids with neither live nor sticky record keep today's filtered behavior; never persisted (spec clarification 3)
- [X] T019 [US3] Degraded-state styling for a mounted card: verify `TerminalCard`'s existing `data-connection-state` hook (TerminalCard.tsx:671) has CSS dimming the terminal for `disconnected`/`error` states (greyed/dimmed body, existing state pill + reconnect button untouched); add the missing CSS rules only if absent — no TerminalCard behavior change (research.md D8)
- [X] T020 [P] [US3] Tests in `frontend/src/components/WorkspacePane.test.tsx`: (a) a pane whose target disappears from `targetsById` after first resolve stays mounted (extends the existing "never remounts a terminal across layout changes" guard at :144); (b) the workspace does NOT fall to the "Select a session" empty state while a sticky-resolved pane exists; (c) closing a pane drops its sticky record (target gone + pane closed → id fully filtered on next render); (d) an id never resolved (restored layout, no discovery yet) keeps today's filtered behavior

**Checkpoint**: pane survival is discovery-independent; workspace suite green.

## Phase 6: User Story 4 — Real failures still surface honestly (P3)

**Goal**: Grace exhaustion and deterministic failures behave exactly as today (error status, cleared targets) with advisory fields populated.
**Independent test**: `uv run pytest tests/unit/web/test_discovery.py`.

- [X] T021 [P] [US4] Failure-path tests in `tests/unit/web/test_discovery.py` (Constitution VI): (a) grace exhaustion — advance the injected monotonic clock past `discovery_offline_grace_s`, next retryable failure serves the real failure status, targets cleared from snapshot AND `_targets_by_id`, `stale=False`, `last_ok_at`/counter still populated; (b) zero budget disables retention (first retryable failure surfaces immediately); (c) each non-retryable status (`AUTH_FAILED`, `NO_REMO_HOST`, `INCOMPATIBLE_PROTOCOL`, `MALFORMED`) surfaces immediately even with a fresh prior OK snapshot, `stale=False`, counter 0; (d) first-ever failure with no prior snapshot stored as-is; (e) `evict` and the deregistered-host prune drop `_last_ok_monotonic` bookkeeping (no leak; a re-registered host starts fresh); (f) per-instance isolation — one host's retention never touches another's snapshot
- [X] T022 [P] [US4] Rail honest-error test in `frontend/src/components/railModel.test.ts`: instance with `status: "timeout"`, `error` set, `stale: false` (post-grace shape) renders `isError=true`, `isStale=false` — the presentation path US2 must never soften

**Checkpoint**: all four stories independently verified.

## Phase 7: Polish & Cross-Cutting

- [X] T023 [P] Document `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S` in `docs/web-session-interface.md` beside the other `REMO_WEB_DISCOVERY_*` settings (semantics: elapsed-time budget, default 120, 0 disables, retryable-only), and grep `docker/compose.example.yml` + `docker/` for env-var listings that should mention it (Principle VIII)
- [X] T024 [P] Verify CLAUDE.md's 024 entries (Active Technologies + Recent Changes, written at plan time) still match the implemented reality; adjust wording if implementation deviated; `uv run pytest tests/unit/test_docs_structure.py` green
- [X] T025 Full gates (foreground): `uv run pytest` (serial — no xdist), `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli`, `cd frontend && npm run lint && npm run test && npm run build && npm run check:types-fresh`; record counts and any pre-existing baseline failures verbatim — backend: 2589 passed, 19 skipped, 0 failed (ruff/mypy clean); frontend: 370 passed (28 files), lint/build/check:types-fresh clean. One non-baseline failure surfaced and was fixed in-scope: `tests/unit/web/test_payload_equivalence.py::test_hosts_response_byte_identical` — its `hosts_response.json` fixture needed the three new additive `InstanceOut` fields appended (spec-mandated wire change, contracts/instance-out-stale.md); fixture + docstring updated, no other byte moved. No other pre-existing failures.

## Dependencies

- Phase 2 (T002-T004) blocks everything; T002 blocks T005/T008; T003 blocks T006.
- US1: T005 → T006; T007 independent of T005/T006 (same file — sequential, not [P]); T008 needs T002; T009 needs T008; T013 needs T009; T010-T012 need T005-T008.
- US2 (T014-T017) needs T009 (generated types carry `stale`). US3 (T018-T020) is independent of US1/US2 backend — only needs the existing `SessionTarget` type. US4 tests (T021-T022) need T005-T006 / T014.
- Polish needs all stories.

## Parallel opportunities

- Phase 2: T002 ∥ T003 ∥ (T004 after T003).
- After T009: US2 (T014-T017) ∥ US3 (T018-T020) ∥ US4 backend tests (T021).
- Test-writing tasks marked [P] within a story parallelize against each other (different files or separable test classes).

## Implementation strategy

MVP = Phase 1-3 (US1): the backend alone stops the teardown. Then US2 + US3 in parallel (independent files), US4 tests, polish. Each checkpoint runs its story's targeted suite in the foreground; `uv run pytest` full run only at T025.
