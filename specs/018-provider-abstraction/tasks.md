# Tasks: Formal Provider Abstraction

**Input**: Design documents from `/specs/018-provider-abstraction/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included — the spec makes test artifacts first-class deliverables (FR-021/FR-022, SC-002/SC-003/SC-004/SC-008 all demand CI-enforced checks).

**Organization**: Grouped by user story. Note two deliberate engineering-order constraints from research.md R10: the CLI factory initially calls the providers' *legacy* full-sequence functions (so US1 ships behavior-preserving without waiting on US3/US4), and full Protocol conformance (`teardown`) completes in US4 when the destroy template lands. The `provider_command` wrapper passes legacy `SystemExit` through until US3 finishes; that shim is removed in the final sweep.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

**Purpose**: Green baseline + a frozen record of today's CLI surface to test preservation against.

- [X] T001 Verify green baseline: run `uv run pytest`, `uv run mypy src/remo_cli`, `uv run ruff check src/remo_cli`; record any pre-existing failures in specs/018-provider-abstraction/baseline-notes.md (expected: none)
- [X] T002 Capture the current per-provider command/flag matrix (commands, option names, short forms, defaults) from cli/providers/{incus,hetzner,aws,proxmox}.py into tests/unit/cli/surface_baseline.py as structured data, per contracts/cli-surface.md — this is the FR-009 preservation reference

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The mechanism every story builds on: error taxonomy, descriptor/registry, protocol, architecture-test harness.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 [P] Create src/remo_cli/core/errors.py: ProviderError (exit_code=1, message), MissingDependencyError, PreconditionError, OperationFailedError, UserAbortedError (exit_code=3) per contracts/errors.md; unit tests in tests/unit/core/test_errors.py
- [X] T004 Create src/remo_cli/core/provider_registry.py: NameFormat, DefaultName (literal | LOGIN_USER sentinel), OptionSpec, CommandSpec, DeprecatedOption, ConnectionSpec, ProviderDescriptor (frozen dataclasses incl. sync_options and info_options fields) plus register()/get_descriptor()/get_provider() (lazy dotted-path import + memoize, ImportError→MissingDependencyError)/all_descriptors()/is_provider_type()/temporary_registration() context manager (test isolation); lookups lazily auto-import remo_cli.providers.builtin on first use (so web-service entry points are safe without explicit imports); duplicate type_name raises; unknown type raises PreconditionError naming the type — per data-model.md
- [X] T005 [P] Add the canonical shared OptionSpec catalog (HOST, USER, DOMAIN, IMAGE, CORES, MEMORY, VOLUME_SIZE, ONLY, SKIP, USE_IP, DEVCONTAINER_RUNTIME, REGION, LOCATION, VERBOSE, NAME, …) in src/remo_cli/core/provider_registry.py per contracts/cli-surface.md (one object per shared option — SC-002 by construction)
- [X] T006 [P] Create src/remo_cli/core/provider_protocol.py: Provider Protocol (update_entry, teardown, probe, snapshot_create/restore/delete/list) per contracts/provider-protocol.md Part A
- [X] T007 Unit tests for the registry mechanism in tests/unit/core/test_provider_registry.py: registration, duplicate rejection, unknown-type error text, lazy import (fake module), MissingDependencyError for a missing optional SDK
- [X] T008 Create tests/unit/test_architecture.py: AST scan of src/remo_cli/providers/ for sys.exit calls and src/remo_cli/cli/ for `noqa: SLF001`/private `remo_cli.providers.*` attribute access, with an explicit transitional allowlist of today's known sites (15 sys.exit, 10 SLF001) so the tree stays green until the US3/US4 gate flips

**Checkpoint**: Mechanism exists and is tested; nothing user-visible changed.

---

## Phase 3: User Story 1 — Add a fifth provider without touching existing files (Priority: P1) 🎯 MVP

**Goal**: Descriptors + factory generate the full CLI for all four providers (behavior-preserving, calling legacy verb functions); hand-written CLI modules deleted; FakeProvider proves the fifth-provider journey.

**Independent Test**: `uv run pytest tests/unit/providers/test_provider_conformance.py tests/unit/cli/` — conformance passes for 4 built-ins + FakeProvider; every `remo <p> …` command from the T002 baseline still exists and behaves identically.

- [X] T009 [P] [US1] Create src/remo_cli/providers/incus_descriptor.py (metadata only: type_name, display "Incus", default "dev1", HOST_SCOPED, registry_fields, create/update options per contracts/cli-surface.md matrix, sync_options (--host/--user/--use-ip), info_options (--host/--user), bootstrap CommandSpec, create --yes DeprecatedOption)
- [X] T010 [P] [US1] Create src/remo_cli/providers/proxmox_descriptor.py (default "dev1", HOST_SCOPED, --purge destroy option, devcontainer-runtime options, sync_options (--host/--user), info_options (--host/--user), bootstrap CommandSpec, create --yes DeprecatedOption)
- [X] T011 [P] [US1] Create src/remo_cli/providers/aws_descriptor.py (default LOGIN_USER, FLAT, sdk_extra "aws", snapshot_region_scoped, sync_options (--region/--all), stop/start/reboot CommandSpecs — stop/reboot confirmable (info is the shared command, no extras), ConnectionSpec placeholder for the SSM proxy_hook wired in T046, create --yes DeprecatedOption; note: no --region on update/destroy — T002 baseline is authoritative)
- [X] T012 [P] [US1] Create src/remo_cli/providers/hetzner_descriptor.py (default "remo", FLAT, sdk_extra "hetzner", --type/--location create options, sync_options (--all), create --yes DeprecatedOption)
- [X] T013 [US1] Create src/remo_cli/providers/builtin.py registering the four descriptors (one line each); wired via the registry's lazy auto-import (T004) — explicit import from cli/main.py is an optimization only, so web-service entry points need no changes to see registered providers
- [X] T014 [P] [US1] Add entry-based adapters to src/remo_cli/providers/incus.py: update_entry(entry) (absorbs host/container split), public snapshot_create/restore/delete/list(entry, …) wrapping the existing private helpers (contracts/provider-protocol.md R-A2/R-A5)
- [X] T015 [P] [US1] Same for src/remo_cli/providers/proxmox.py (absorbs node/vmid/user-from-region resolution, replaces _lookup_proxmox_host/_list_snapshots_for_vmid reach-ins)
- [X] T016 [P] [US1] Same for src/remo_cli/providers/aws.py (entry-based snapshot verbs honoring region; update_entry)
- [X] T017 [P] [US1] Same for src/remo_cli/providers/hetzner.py
- [X] T018 [US1] Add list_all_snapshots(type_name, lister) aggregation to src/remo_cli/core/snapshot.py per contracts/lifecycle-templates.md (replaces the 4 CLI-layer loops; partial-failure flag preserved)
- [X] T019 [US1] Create src/remo_cli/cli/providers/factory.py: provider_command wrapper (ProviderError→print_error+sys.exit(exit_code); transitional legacy-SystemExit passthrough), OptionSpec→click.Option builder, name_format-derived shell completion, DeprecatedOption notice support, build_provider_group() generating create/destroy/update/list/info (with descriptor info_options)/sync (with descriptor sync_options) + snapshot group + extra_commands per contracts/cli-surface.md — destroy/list initially dispatch to the legacy provider functions (destroy sequence swap happens in T038)
- [X] T020 [US1] Rewire src/remo_cli/cli/main.py to mount generated groups via all_descriptors() (replacing the four explicit group imports)
- [X] T021 [US1] Delete src/remo_cli/cli/providers/{incus,hetzner,aws,proxmox}.py and src/remo_cli/core/completion.py; fix any lingering imports
- [X] T022 [US1] Create tests/unit/providers/test_provider_conformance.py + FakeProvider fixture (tests/unit/providers/fake_provider.py): Protocol satisfaction (teardown check marked xfail until T038), inspect.signature vs descriptor OptionSpec.param agreement, FakeProvider registers via provider_registry.temporary_registration() (no cross-test leakage) and its full `remo fake …` group mounts with standard flags — the SC-001 proof (contracts/provider-protocol.md conformance gate)
- [X] T023 [P] [US1] Create tests/unit/cli/test_startup_imports.py: build the full CLI + render top-level help; assert boto3/hcloud not in sys.modules (SC-008/FR-024)
- [X] T024 [US1] Migrate existing CLI-layer tests (tests/unit/cli/providers/test_*_snapshot.py, test_incus_sync_all.py, test_proxmox_sync_all.py) to exercise the generated commands
- [X] T025 [P] [US1] Write docs/providers.md contributor guide (implement contract → declare descriptor → register in builtin.py → conformance suite is the gate) and link from CONTRIBUTING.md (FR-023)

**Checkpoint**: MVP — four generated command groups, zero hand-written provider CLI files, fifth-provider path proven.

---

## Phase 4: User Story 2 — Identical commands behave identically everywhere (Priority: P2)

**Goal**: Uniformity is verified and pinned; the dead `--yes` prints its deprecation notice.

**Independent Test**: `uv run pytest tests/unit/cli/test_cli_uniformity.py` green; `remo <p> create --help` shows descriptor default names; `remo <p> create --yes` prints the notice.

- [X] T026 [P] [US2] Create tests/unit/cli/test_cli_uniformity.py: for every shared command across the four providers assert identical option names/short forms/metavars/help text, per-provider extras exactly match contracts/cli-surface.md, default instance name appears in create help (FR-011), destroy accepts --yes/-y with uniform auto_confirm forwarding (FR-012) — SC-002's automated comparison
- [X] T027 [US2] Implement the create --yes deprecation notice in src/remo_cli/cli/providers/factory.py ("Deprecated: --yes has no effect on create and will be removed in a future release.") + test in tests/unit/cli/test_cli_uniformity.py (FR-010)
- [X] T028 [P] [US2] Create tests/unit/cli/test_surface_preservation.py: assert every command/flag recorded in tests/unit/cli/surface_baseline.py (T002) still exists on the generated CLI (FR-009)

**Checkpoint**: Uniformity locked by CI; only declared deprecations differ from baseline.

---

## Phase 5: User Story 3 — Failures are predictable and never silent (Priority: P2)

**Goal**: Typed errors everywhere in the business layer; registry-dispatched shell update with explicit unknown-type errors; web service never sees SystemExit.

**Independent Test**: `uv run pytest tests/unit/test_architecture.py tests/unit/cli/test_shell.py tests/unit/providers/` — zero-sys.exit gate enforced with empty allowlist; unknown-type shell path exits 1 with the type named.

- [X] T029 [US3] Migrate src/remo_cli/providers/aws.py to the error contract: replace all 12 sys.exit sites and 9 RuntimeErrors with taxonomy errors; stop/start/reboot/info return None and raise (contracts/errors.md prohibitions); verbs' nonzero playbook rc → OperationFailedError (note: 9 RuntimeErrors deliberately kept as internal-only, always caught/rewrapped before reaching a public verb boundary — see task report)
- [X] T030 [P] [US3] Same for src/remo_cli/providers/incus.py (1 sys.exit, 3 RuntimeErrors)
- [X] T031 [P] [US3] Same for src/remo_cli/providers/hetzner.py (1 sys.exit, 4 RuntimeErrors)
- [X] T032 [P] [US3] Same for src/remo_cli/providers/proxmox.py (1 sys.exit, 2 RuntimeErrors)
- [X] T033 [US3] Update existing provider unit tests (tests/unit/providers/test_*_{snapshot,sync,marker,label}.py) from SystemExit/rc expectations to ProviderError expectations
- [X] T034 [US3] Replace the if/elif chain in src/remo_cli/cli/shell.py with provider_registry lookup + update_entry: unknown type → explicit error + exit 1 (closes today's silent `return 0`), type "ssh" → explicit documented skip; migrate auto_start_aws_if_stopped call path to typed errors; add unknown-type + ssh-skip tests to tests/unit/cli/test_shell.py (SC-004)
- [X] T035 [US3] Update non-CLI consumers to catch ProviderError — audited: src/remo_cli/cli/cp.py does not call any provider auto-start path (only shell.py does, fixed in T029/T034), and src/remo_cli/web/ never imports remo_cli.providers.* directly (it goes through core/ssh.py + remo_host_client; the aws/SSM coupling in core/ssh.py is T046's scope). No code changes needed; verified via grep.
- [X] T036 [P] [US3] Add missing-SDK tests in tests/unit/providers/test_missing_sdk.py: with boto3 absent, any aws command → MissingDependencyError message naming the extra, exit 1, no traceback (hetzner has no optional-SDK import in practice — it calls the Hetzner HTTP API directly via urllib, not the hcloud package, despite the descriptor's sdk_extra="hetzner" being declared for future-proofing — so hetzner has no missing-SDK path to test)
- [X] T037 [US3] Flip the sys.exit architecture gate: empty the allowlist in tests/unit/test_architecture.py; remove any ruff per-file-ignores for src/remo_cli/providers/ in pyproject.toml (SC-003 first half) — no per-file-ignores existed to remove

**Checkpoint**: Error contract fully enforced by CI; silent dispatch gone.

---

## Phase 6: User Story 4 — Maintainers change shared behavior in one place (Priority: P3)

**Goal**: The five duplicated skeletons each exist exactly once; full Protocol conformance (teardown) completes.

**Independent Test**: `uv run pytest tests/unit/core/test_lifecycle.py tests/unit/providers/test_provider_conformance.py` — teardown xfail removed; grep shows one implementation per skeleton.

- [X] T038 [US4] Create src/remo_cli/core/lifecycle.py run_destroy() (guard → snapshot pre-cleanup → confirm → teardown → best-effort registry removal, normative ordering per contracts/lifecycle-templates.md); extract teardown(entry, **opts) in all four src/remo_cli/providers/*.py from their destroy() bodies; switch factory destroy to the template; delete the four legacy destroy() sequences; un-xfail the teardown conformance check (FR-013, FR-001 completion)
- [X] T039 [US4] Add destroy-ordering regression tests in tests/unit/core/test_lifecycle.py: call-order assertion (guard/cleanup/confirm/teardown/removal), decline→UserAbortedError exit 3, registry-removal failure warns but succeeds, ssh-guard raises PreconditionError
- [X] T040 [P] [US4] Add build_configure_extra_vars(tools_only, tools_skip) to src/remo_cli/core/ansible_runner.py (timezone + build_tool_args + remo_version) and replace all 8 inline copies across the four providers' *_site.yml and *_configure.yml paths (FR-015)
- [X] T041 [P] [US4] Add run_resize_playbook(playbook, extra_vars, verbose) to src/remo_cli/core/ansible_runner.py (nonzero rc → OperationFailedError); delete the private copies in src/remo_cli/providers/{incus,proxmox}.py (FR-016)
- [X] T042 [P] [US4] Add render_host_table(entries, columns) to src/remo_cli/core/output.py with descriptor-declared columns; switch the factory list command to it; delete the four list_hosts() renderers (FR-016, formatting change allowed by FR-025)
- [X] T043 [US4] Flip the private-access architecture gate: zero `noqa: SLF001` in src/remo_cli/cli/ (empty allowlist in tests/unit/test_architecture.py) and remove the corresponding ruff suppressions (SC-003 second half)

**Checkpoint**: Every shared skeleton is single-sourced; conformance suite fully green.

---

## Phase 7: User Story 5 — Formalized sync contract closes issue #87 (Priority: P3)

**Goal**: Observed-vs-default merge semantics; AWS access_mode phantom updates gone.

**Independent Test**: `uv run pytest tests/unit/core/test_reconcile.py -k observed tests/unit/providers/test_aws_sync.py` — the four acceptance cases in contracts/sync-merge.md pass.

- [X] T044 [US5] Add DiscoveredHost.observed (frozenset[str] | None, default None = legacy semantics) and the observed-aware merge rule to merge_entry() in src/remo_cli/core/reconcile.py; additions still use discovered wholesale; tests for the merge rule + legacy-None behavior + plan idempotence in tests/unit/core/test_reconcile.py (FR-019, FR-020)
- [X] T045 [US5] Set DiscoveredHost.observed in the AWS _probe in src/remo_cli/providers/aws.py to ALL merge-relevant fields the probe actually read, excluding access_mode when the remo_access_mode tag is absent (NOT observed={"access_mode"} — other observed fields must keep merging); add the four contracts/sync-merge.md acceptance cases to tests/unit/providers/test_aws_sync.py (SC-007)

**Checkpoint**: `remo aws sync` twice in a row → empty second plan even with hand-edited access modes.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Remaining FR-018/FR-005 dispatch migrations, docs, final gates.

- [X] T046 Migrate the SSM branch out of src/remo_cli/core/ssh.py: implement ConnectionSpec.proxy_hook resolution in build_ssh_base_cmd (descriptor lookup; ssh-type short-circuits first), move the SSM ProxyCommand + get_aws_region logic into an AWS hook in src/remo_cli/providers/aws.py, update tests (FR-018)
- [X] T047 [P] Drive SyncScope validation/scoping in src/remo_cli/core/reconcile.py from descriptor name_format + is_provider_type instead of literal type tuples (_HOST_SCOPED_TYPES removed); unknown type still ScopeError (FR-005, FR-020 preserved)
- [X] T048 Drive the per-type serialization field map in src/remo_cli/core/registry.py from descriptor.registry_fields (ssh pseudo-type stays local; defensive serialize-all fallback + warning for unknown types) and replace the name-format literals in src/remo_cli/core/known_hosts.py with descriptor lookups; tests for the unknown-type fallback (FR-005, Edge Case "Unknown host type")
- [X] T049 [P] Documentation sync (Constitution V): update README.md provider sections, CLAUDE.md/AGENTS.md project-structure entries (deleted CLI modules, new core modules), and document the two normalizations (create --yes deprecation; playbook-rc→exit-1) in docs/ + the release-please commit message. CLAUDE.md updated (Active Technologies/Project Structure/Recent Changes); README.md needed no change (never documented create --yes or exit-code specifics); docs/providers.md already covered both normalizations, fixed stale "intended end state" framing; fixed stale post-delete docstring references in incus.py/hetzner.py. Note: AGENTS.md is severely stale (last updated 2026-01-06, predates even the 003 rewrite's own package rename to remo_cli) — pre-existing drift spanning many prior features, out of scope to backfill here; left untouched and flagged to the user.
- [~] T050 Close the loop on issue #87: verify the fix against the issue's repro, comment with the contract reference (contracts/sync-merge.md), close via `gh issue close 87` after merge — fix verified against the real, open issue #87 (confirmed via `gh issue view 87`: matches exactly); closing/commenting deliberately deferred since this work is not yet merged (still local, uncommitted on the feature branch) — do this after merge, per the task's own instruction
- [X] T051 Remove the transitional legacy-SystemExit passthrough from provider_command in src/remo_cli/cli/providers/factory.py; final sweep greps (`sys.exit` in providers/, `noqa: SLF001` in cli/, literal type-string tuples in core/) — all must be empty except documented exclusions (only remaining literal tuple: registry.py's `KNOWN_TYPES`, the registry wire-format's own fixed vocabulary — documented exclusion per R7 "ssh pseudo-type keeps an explicit local definition")
- [X] T052 Run the full quickstart.md validation (all 8 scenarios) + `uv run pytest` + `uv run mypy src/remo_cli` + `uv run ruff check src/remo_cli`; confirm SC-001…SC-008 checklist in quickstart.md — all 7 automatable scenarios pass; scenario 8 (behavior smoke against real infra) not applicable in this sandbox, substituted a safe `remo incus create` invocation confirming clean end-to-end CLI wiring up to the (expected, since incus isn't installed here) ansible failure boundary; full suite 1729 passed/17 skipped, mypy clean, ruff clean

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** → nothing
- **Phase 2 (Foundational)** → Phase 1; **blocks all stories**
- **Phase 3 (US1)** → Phase 2 — the MVP; delivers generated CLI on legacy verb internals
- **Phase 4 (US2)** → Phase 3 (tests the generated surface)
- **Phase 5 (US3)** → Phase 3 (wrapper + registry dispatch exist); independent of Phase 4
- **Phase 6 (US4)** → Phase 3 (factory swaps to templates) and benefits from Phase 5's typed errors (templates raise taxonomy errors); T038 depends on T029–T032
- **Phase 7 (US5)** → Phase 2 only (touches reconcile + AWS probe); can run in parallel with Phases 4–6
- **Phase 8 (Polish)** → all story phases (T046–T048 need descriptors + typed errors; T051 needs Phase 5 complete)

### Key task-level dependencies

- T019 needs T003–T006, T014–T018 · T020 needs T013, T019 · T021 needs T020, T024 · T022 needs T019–T021
- T037 needs T029–T033 · T038 needs T029–T032 · T043 needs T014–T017, T038
- T051 needs T037 (no more legacy SystemExit to pass through)

### Parallel Opportunities

- Phase 2: T003, T005, T006 in parallel after T004's types sketch (or T003 fully independent)
- Phase 3: T009–T012 (four descriptor files) in parallel; T014–T017 (four provider adapter sets) in parallel; T023/T025 parallel to T024
- Phase 5: T030–T032 (three provider error migrations) in parallel after T029 establishes the pattern
- Phase 6: T040–T042 in parallel after T038
- Phase 7 entirely parallel to Phases 4–6 (different files)

## Parallel Example: User Story 1

```bash
# Four descriptor modules simultaneously (different files, pure metadata):
Task: "Create src/remo_cli/providers/incus_descriptor.py"
Task: "Create src/remo_cli/providers/proxmox_descriptor.py"
Task: "Create src/remo_cli/providers/aws_descriptor.py"
Task: "Create src/remo_cli/providers/hetzner_descriptor.py"
# Then four adapter sets simultaneously:
Task: "Entry-based adapters in providers/incus.py" ... (×4)
```

## Implementation Strategy

**MVP first (Phases 1–3)**: generated CLI replacing 1,375 hand-written lines, behavior-preserving, fifth-provider path proven — stop, validate against the T002 baseline, ship.

**Incremental**: Phase 4 pins uniformity (cheap, high user trust). Phase 5 is the correctness payload (error contract). Phase 6 completes dedup + conformance. Phase 7 (#87) is independent and can land any time after Phase 2. Phase 8 closes the abstraction (core dispatch), documents, and flips the last gates.

Each phase leaves the tree green (R10); commit per task or logical group; the release containing Phase 4 starts the one-release `--yes` deprecation clock.
