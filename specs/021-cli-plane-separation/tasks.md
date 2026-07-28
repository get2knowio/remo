# Tasks: CLI Plane Separation — Intent-Named Verbs and a Host Subgroup

**Input**: Design documents from `/specs/021-cli-plane-separation/`

**Prerequisites**: plan.md, spec.md, research.md (decisions D1–D10), data-model.md,
contracts/{cli-surface.md,descriptor-schema.md}, quickstart.md

**Tests**: Included — the spec mandates them (FR-012; Constitution Principle VI: pin behavior
before refactoring, cover every skip/fail path).

**Organization**: Grouped by user story. Sequencing note: `remo <type> update` stays mounted
through Phases 3–6 (each story is additive, so the suite is green at every checkpoint) and is
removed in Phase 7 once the three new verbs jointly cover all of its capabilities (FR-004).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: US1 = upgrade, US2 = tag, US3 = resize, US4 = host subgroup

## Phase 1: Setup

**Purpose**: Confirm a green starting point so every later checkpoint is meaningful.

- [X] T001 Verify baseline: `uv sync --all-extras && uv run pytest && uv run ruff check src/remo_cli && uv run mypy src/remo_cli` all pass on branch `021-cli-plane-separation`; commit any outstanding spec artifacts

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Descriptor/factory mechanism extensions and the `--user` rename (FR-008) that every
story builds on. `update_options` and `_build_update` are intentionally NOT touched yet.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T002 In `src/remo_cli/core/provider_registry.py`: add frozen `ArgumentSpec` dataclass (`name`, `default=None`, `required=True`, `completion=CompletionKind.NONE`); add `target: ArgumentSpec | None = None` to `CommandSpec`; add `ProviderDescriptor` fields `upgrade_options`, `resize_dimensions`, `resize_options`, `tag_options` (all `tuple[OptionSpec, ...] = ()`) and `host_commands: tuple[CommandSpec, ...] = ()` (keep `update_options` for now); extend the `__post_init__` duplicate-option loop to cover the new fields, checking `resize_dimensions + resize_options` combined per rule G-7 of contracts/descriptor-schema.md
- [X] T003 In `src/remo_cli/cli/providers/factory.py`: give `_instance_argument` a `param: str = "instance"` parameter (snapshot callers unchanged); rewrite `_resolve_entry_for_destroy` (lines ~180–214) to drop `kwargs.get("user")` and instead read `kwargs.get(<json_key>)` for the `descriptor.registry_fields` entry whose JSON key ends in `_user` (rule G-6)
- [X] T004 [P] In `src/remo_cli/providers/incus_descriptor.py`: redeclare `HOST_USER` as `OptionSpec(name="--host-user", param="host_user", ...)` with help text stating it is the hypervisor login and that the container login stays `remo` (FR-008)
- [X] T005 [P] In `src/remo_cli/providers/proxmox_descriptor.py`: redeclare `_NODE_USER` as `OptionSpec(name="--node-user", param="node_user", ...)` with equivalent node-login help text
- [X] T006 [P] In `src/remo_cli/providers/incus.py`: rename the `user=` kwarg to `host_user=` on every public function whose flag renamed (`create`, `update` (interim), `info`, `sync`/`probe` path, `bootstrap`, destroy/teardown path); keep internal locals and registry fields unchanged; rewrite the stale hint at incus.py:115–118 to name the new surface (`--host-user`, and `upgrade` once it exists — use `remo incus upgrade <name> --host <h> --host-user <u>`)
- [X] T007 [P] In `src/remo_cli/providers/proxmox.py`: same rename `user=` → `node_user=` on public functions; internal `user` locals may remain
- [X] T008 In `tests/`: update the rename fallout — `tests/unit/cli/surface_baseline.py` (every `--user` entry → `--host-user`/`--node-user`), `tests/unit/cli/providers/test_incus_sync_all.py:22` and `test_proxmox_sync_all.py:22` (`--user` invocations), any `kwargs["user"]` assertions in destroy/factory tests; then remove the shared `USER` spec from `core/provider_registry.py`'s catalog — after T004/T005 both former consumers declare fresh specs, so zero consumers remain (research D7; `remo add --user` in `cli/added.py` is unrelated and MUST stay untouched)
- [X] T009 In `tests/unit/providers/test_provider_conformance.py`: teach the param-collection helper (`_option_param_names`, lines ~86–87) to also include `click.Argument` param names so positional-taking verbs are visible to the set-equality check (needed by all four stories)

**Checkpoint**: `uv run pytest` green; `--host-user`/`--node-user` live on the existing surface; new descriptor fields exist but are unused.

---

## Phase 3: User Story 1 — Refresh an instance's software with one predictable verb (Priority: P1) 🎯 MVP

**Goal**: `remo <type> upgrade NAME` on all four providers runs exactly the in-instance configure
play — zero provider-side writes (SC-001); `remo shell`'s prompt names it.

**Independent Test**: Run `upgrade` per provider with the provider seam mocked; assert the
configure playbook runs and no marker/label/resize call occurs; `remo shell` prompt names
`remo <type> upgrade <name>` (quickstart.md §2).

### Implementation for User Story 1

- [X] T010 [P] [US1] In `src/remo_cli/providers/incus.py`: add `upgrade(name, host="", host_user="", tools_only=(), tools_skip=(), verbose=False)` — validate → `guard_not_added_ssh_host` → `_lookup_incus_host` → `_resolve_container_ip` → `incus_configure.yml` (extract the existing blocks from `update()` lines ~400–467, marker/resize excluded); point `update_entry` at `upgrade(...)` (drop `apply_marker` plumbing from its call)
- [X] T011 [P] [US1] In `src/remo_cli/providers/proxmox.py`: add `upgrade(name, host="", node_user="", devcontainer_runtime=None, tools_only=(), tools_skip=(), verbose=False)` — same extraction from `update()` (no VMID resolution beyond what `_resolve_container_ip` needs); `update_entry` → `upgrade`
- [X] T012 [P] [US1] In `src/remo_cli/providers/aws.py`: add `upgrade(name="", tools_only=(), tools_skip=(), verbose=False)` — keeps `_get_running_instance` + `save_known_host` IP refresh (FR-001 permits) and `aws_configure.yml`; no resize block; `update_entry` → `upgrade`
- [X] T013 [P] [US1] In `src/remo_cli/providers/hetzner.py`: add `upgrade(name="", tools_only=(), tools_skip=(), verbose=False)` — lookup + `hetzner_configure.yml` only, no label backfill, no resize; `update_entry` → `upgrade`
- [X] T014 [US1] In the four `src/remo_cli/providers/*_descriptor.py`: set `upgrade_options` per data-model.md §5 (incus: `HOST`, `HOST_USER`; proxmox: `HOST`, `_NODE_USER`, `DEVCONTAINER_RUNTIME`; aws/hetzner: `()`)
- [X] T015 [US1] In `src/remo_cli/cli/providers/factory.py`: add `_build_upgrade(descriptor)` — positional `NAME` via `_instance_argument(descriptor, param="name")`, options `[*descriptor.upgrade_options, ONLY, SKIP, VERBOSE]`, callback `module.upgrade(**kwargs)` (rules G-2/G-4); mount it in `build_provider_group` (leave `update` mounted)
- [X] T016 [US1] In `src/remo_cli/cli/shell.py`: make both version-mismatch prompts name the exact command (e.g. `Instance 'dev1' tools are v0.8, local is v0.9. Run \`remo proxmox upgrade dev1\`?` and the no-version-info variant); reword `_run_provider_update` messages from "update" to "upgrade" (it still calls `module.update_entry(host)`, which now performs exactly US1's operation — acceptance scenario 3)

### Tests for User Story 1

- [X] T017 [P] [US1] In `tests/unit/providers/fake_provider.py` + `test_provider_conformance.py`: add `upgrade` impl to the fake; add `upgrade` to the verb signature-conformance loop (line ~96) and the group-membership tuple (line ~217)
- [X] T018 [P] [US1] In `tests/unit/providers/test_incus_marker.py`, `test_proxmox_marker.py`, `test_hetzner_label.py`: re-home the `update_entry`-does-not-touch-host characterization tests as `upgrade` invariants asserting zero marker/label AND zero resize calls for direct `upgrade()` invocation and via `update_entry`; add the aws case in a new `tests/unit/providers/test_upgrade_invariants.py` (SC-001, all four providers)
- [X] T019 [P] [US1] In `tests/unit/providers/test_added_provider_guard.py`: parametrize the guard cases over `<provider>.upgrade` (alongside the existing `update` entries for now)
- [X] T020 [P] [US1] In `tests/unit/cli/test_shell.py`: assert the prompts name `remo <type> upgrade <name>` and the accepted path invokes `update_entry` (scenario 3); update existing prompt-text assertions
- [X] T021 [US1] In `tests/unit/cli/surface_baseline.py` + `tests/unit/cli/test_main.py`: add `upgrade` (with its per-provider flags) to the frozen surface and expected subcommand lists; run `uv run pytest`

**Checkpoint**: `upgrade` fully functional on all providers, independently testable; `update` still present and untouched.

---

## Phase 4: User Story 2 — Tag a legacy instance without touching anything else (Priority: P2)

**Goal**: `remo <type> tag NAME` writes exactly the managed marker on marker-supporting providers
(incus/proxmox/hetzner); already-tagged is a reported exit-0 no-op; failure is a hard error;
migration notice and sync remedy print `tag` (SC-002, SC-003).

**Independent Test**: `tag` on an untagged instance → exactly one marker write, zero Ansible;
second run → no-op; `remo aws tag` → unknown command; notice/remedy strings name `tag`
(quickstart.md §2).

### Implementation for User Story 2

- [X] T022 [P] [US2] In `src/remo_cli/providers/incus.py`: add `tag(name, host="", host_user="")` — validate → guard → lookup → read `incus config get <name> user.remo` (new pre-read via `_ssh_run_on_incus_host`); if already `true`, print already-tagged notice and return; else `_apply_managed_marker` and raise `OperationFailedError` with the underlying stderr on failure (NOT `create`'s warn-and-continue)
- [X] T023 [P] [US2] In `src/remo_cli/providers/proxmox.py`: add `tag(name, host="", node_user="")` — resolve VMID (registry-cached else `_resolve_vmid` host lookup; unresolvable → `PreconditionError`); detect tag already present from the existing `pct config` read inside `_apply_managed_marker` (refactor it to report already-present distinctly); strict `OperationFailedError` on write failure
- [X] T024 [P] [US2] In `src/remo_cli/providers/hetzner.py`: add `tag(name)` — reuse `_apply_managed_label`'s GET-merge-PUT (already no-ops when label present — surface that as the already-tagged notice); strict `OperationFailedError` on failure
- [X] T025 [US2] In `incus_descriptor.py`/`proxmox_descriptor.py`/`hetzner_descriptor.py`: set `tag_options` per data-model.md §5 (aws_descriptor untouched — no `tag_options`, `supports_managed_marker` stays `False`)
- [X] T026 [US2] In `src/remo_cli/cli/providers/factory.py`: add `_build_tag(descriptor)` — generated only when `descriptor.supports_managed_marker` (rule G-1); positional `NAME` (param `name`), options `[*descriptor.tag_options]`; mount in `build_provider_group`
- [X] T027 [US2] In `src/remo_cli/core/known_hosts.py`: `_print_tagging_notice` prints `remo <type> tag <name>` (+ ` --host <host>` for HOST_SCOPED types, driven by `descriptor.name_format`); correct the lines ~72–73 docstring claim that `update`/`sync` tag (only `tag` and `create` write markers) (FR-009/FR-010)
- [X] T028 [US2] In `src/remo_cli/core/reconcile.py`: `render_plan`'s `mark_cmd` (lines ~328–331) becomes `remo {plan.scope.type} tag <n>` (+ ` --host <h>` for HOST_SCOPED) — both "Mark permanently:" branches

### Tests for User Story 2

- [X] T029 [P] [US2] In `tests/unit/providers/test_incus_marker.py`, `test_proxmox_marker.py`, `test_hetzner_label.py`: re-home `test_update_applies_marker`/`test_explicit_update_still_backfills`-style cases onto `tag`; add: exactly-one-write + zero-Ansible (SC-002), already-tagged no-op exit 0 second run, write-failure → `OperationFailedError` (not warning), proxmox unresolvable-VMID → `PreconditionError`
- [X] T030 [P] [US2] In `tests/unit/core/test_migration_tagging_notice.py` and `tests/unit/core/test_reconcile.py`: assert the notice prints `remo incus tag <name> --host <host>` / `remo hetzner tag <name>` shapes and `render_plan` prints `Mark permanently: remo <type> tag <n>`; assert aws never appears in the notice (SC-003)
- [X] T031 [US2] Surface wiring tests: `fake_provider.py` gains `tag` (its descriptor sets `supports_managed_marker=True`); conformance asserts `tag` present for marker-supporting descriptors and ABSENT otherwise (`remo aws tag` → Click unknown command, scenario 5); add `tag` to guard parametrization, `surface_baseline.py` (incus/proxmox/hetzner only), and `test_main.py`; run `uv run pytest`

**Checkpoint**: US1 + US2 both work; tagging a legacy container requires no reconfigure.

---

## Phase 5: User Story 3 — Resize an instance without reconfiguring it (Priority: P2)

**Goal**: `remo <type> resize NAME` applies only the resource change (incl. provider-required
in-guest fs grow); no configure play; dimensionless invocation fails listing that provider's
dimensions.

**Independent Test**: `resize` with each dimension flag per provider → resize path runs, no
configure playbook; no flags → exit 1 with the dimension list; `--cores`/`--memory` absent from
aws/hetzner `--help` (quickstart.md §2).

### Implementation for User Story 3

- [X] T032 [US3] In `src/remo_cli/cli/providers/factory.py`: add `_build_resize(descriptor)` — positional `NAME` (param `name`), options `[*descriptor.resize_dimensions, *descriptor.resize_options, VERBOSE]`; callback raises `PreconditionError` listing the dimension flag names (from `resize_dimensions[*].name`) when every dimension param is falsy, BEFORE provider import/dispatch (rule G-3); mount in `build_provider_group`
- [X] T033 [P] [US3] In `src/remo_cli/providers/incus.py`: add `resize(name, host="", host_user="", volume_size="", cores=0, memory=0, verbose=False)` — validate/guard/lookup + `parse_volume_size` + the existing resize block (`_run_resize_playbook`, `incus_resize.yml`); no configure play
- [X] T034 [P] [US3] In `src/remo_cli/providers/proxmox.py`: add `resize(...node_user...)` — includes VMID resolution for the resize playbook (`PreconditionError` if unresolvable), reusing `_run_resize_playbook`/`proxmox_resize.yml`
- [X] T035 [P] [US3] In `src/remo_cli/providers/aws.py`: add `resize(name="", volume_size="", verbose=False)` — the existing EBS-grow block (`aws_resize.yml`, which includes the in-guest fs grow play, so it needs the instance IP); keep the running-instance lookup and the registry IP refresh preceding it (matches today's `update` behavior; idempotent registry write via `save_known_host`)
- [X] T036 [P] [US3] In `src/remo_cli/providers/hetzner.py`: add `resize(name="", volume_size="", verbose=False)` — existing volume-grow block (`hetzner_resize.yml` incl. in-guest grow); no label logic
- [X] T037 [US3] In the four `*_descriptor.py`: set `resize_dimensions` (`VOLUME_SIZE`+`CORES`+`MEMORY` for incus/proxmox; `VOLUME_SIZE` for aws/hetzner) and `resize_options` (incus: `HOST`,`HOST_USER`; proxmox: `HOST`,`_NODE_USER`; aws/hetzner: `()`) per data-model.md §5

### Tests for User Story 3

- [X] T038 [P] [US3] Provider/CLI resize tests (extend the marker/label test modules or a new `tests/unit/providers/test_resize.py` + `tests/unit/cli/` case): each dimension flag triggers the resize path with zero configure-playbook invocations; no-dimension invocation exits 1 with a message listing exactly that provider's dimension flags (all four providers); `--cores`/`--memory` absent from `remo aws resize --help`/`remo hetzner resize --help` (scenario 4); guard parametrization gains `resize`
- [X] T039 [US3] Surface wiring: `fake_provider.py` gains `resize` (+ `resize_dimensions` on its descriptor); conformance loop + group tuple + `surface_baseline.py` + `test_main.py` gain `resize`; run `uv run pytest`

**Checkpoint**: All three intents individually invocable; `update` is now fully redundant.

---

## Phase 6: User Story 4 — Host operations have one explicit home (Priority: P3)

**Goal**: `remo {incus,proxmox} host bootstrap HOST` replaces flat `bootstrap`; `host` subgroup
absent on aws/hetzner; mechanism is descriptor-generated (FR-005/FR-006).

**Independent Test**: `bootstrap` exists only under `host` for incus/proxmox, takes the host
positionally; aws/hetzner `--help` shows no `host`; FakeProvider gets a `host` subgroup from its
descriptor alone (quickstart.md §1–2).

### Implementation for User Story 4

- [X] T040 [US4] In `src/remo_cli/cli/providers/factory.py`: add `_build_host_command(descriptor, spec)` (reuses `_build_extra_command` machinery + prepends `click.Argument` from `spec.target` per rule G-5) and `_build_host_group(descriptor)` (a `host` `click.Group`, mounted only when `descriptor.host_commands` is non-empty, help: "Operate on the hypervisor host, not an instance."); no `extra_commands` filtering is needed — T041/T042 *move* bootstrap from `extra_commands` to `host_commands`, so the factory simply mounts both fields' commands as declared
- [X] T041 [P] [US4] In `src/remo_cli/providers/incus_descriptor.py`: move bootstrap from `extra_commands` to `host_commands` with `target=ArgumentSpec("host", default="localhost", required=False)`, options `(HOST_USER, NETWORK_TYPE, VERBOSE)` (no `--host` option)
- [X] T042 [P] [US4] In `src/remo_cli/providers/proxmox_descriptor.py`: move bootstrap to `host_commands` with `target=ArgumentSpec("host", required=True)`, options `(_NODE_USER, _BRIDGE, _STORAGE, _TEMPLATE, VERBOSE)`
- [X] T043 [P] [US4] In `src/remo_cli/providers/incus.py`: adjust `bootstrap(host="localhost", host_user="", network_type="", verbose=False)` if needed for the positional param (behavior unchanged — same playbook/extra-vars)
- [X] T044 [P] [US4] In `src/remo_cli/providers/proxmox.py`: `bootstrap(host, node_user="", ...)` — keep the empty-host `PreconditionError` as defense-in-depth for non-CLI callers (Click's required positional shields the CLI path; the conformance check compares params, not bodies); behavior otherwise unchanged
- [X] T045 [US4] Tests: conformance — FakeProvider descriptor gains one `host_commands` entry with a `target`, prove `host` subgroup + command generated with zero existing-file edits and absent when `host_commands=()` (SC-005/scenario 4); `remo aws|hetzner --help` shows no `host`; flat `remo incus bootstrap` → Click unknown command (scenario 2); update `surface_baseline.py` (bootstrap moves under `host`), `test_main.py` lists; run `uv run pytest`
- [X] T046 [US4] CI/integration scripts: `.github/workflows/smoke-test.yml:355` → `remo incus host bootstrap --host-user $USER --network-type bridge` (positional host omitted → localhost); `tests/integration/orbstack.sh:167,171` → `remo incus host bootstrap "$host" --host-user "$SSH_USER"` and `create ... --host-user`

**Checkpoint**: All four stories functional; only `update` remains to be removed.

---

## Phase 7: Verb Removal & Surface Cleanup (cross-cutting, FR-004)

**Purpose**: The clean break — `update` ceases to exist now that upgrade/resize/tag jointly
cover it.

- [X] T047 Delete `update()` from `src/remo_cli/providers/{incus,proxmox,aws,hetzner}.py` (and any now-orphaned `apply_marker` plumbing); delete `_build_update` from `factory.py` and its mount in `build_provider_group`; remove `update_options` from `ProviderDescriptor` (and its `__post_init__` loop entry) in `core/provider_registry.py` and from all four `*_descriptor.py`
- [X] T048 Test cleanup: remove `update` from `fake_provider.py`, the conformance verb loop + its `apply_marker` carve-out (lines ~104–111) + group tuple, `surface_baseline.py`, `test_main.py`, `test_added_provider_guard.py`, and `tests/unit/cli/test_cli_uniformity.py` shared-option pairs (`update` → `upgrade`/`resize` pairs); run `uv run pytest`
- [X] T049 SC-004 sweep: run the straggler grep from quickstart.md §2 over `src/` help text and `tests/` — zero occurrences of `remo <type> update`, flat `bootstrap`, or `--user` (incus/proxmox sense) outside historical archives (docs handled in Phase 8); `uv run remo incus update` and `uv run remo incus bootstrap` exit 2 with Click's unknown-command error

---

## Phase 8: Polish & Documentation (Principle VIII)

- [X] T050 [P] Update `README.md`: all `update` examples → `upgrade`/`resize` (lines ~293–350, 439), bootstrap lines → `remo <type> host bootstrap`, marker prose → `tag`
- [X] T051 [P] Update `docs/incus.md`, `docs/proxmox.md`, `docs/aws.md`, `docs/hetzner.md`: verb examples, `--user` → `--host-user`/`--node-user` semantics paragraphs, "mark permanently" callouts → `tag`, bootstrap sections → `host bootstrap`, option tables
- [X] T052 [P] Update `docs/providers.md`: generated-verb list (`upgrade`/`resize`/`tag`/`host`), descriptor field docs (new fields, `update_options` gone), Part B verb examples; record the FR-007 naming rule (commands named by the resource whose state they change; instance verbs flat, host verbs under `host`; multi-plane steps only in service of one intent)
- [X] T053 Update `CLAUDE.md` + `AGENTS.md`: structure-diagram comment lines for `factory.py`/`shell.py`/provider modules, Commands section, and any `update`-verb references; keep both files in sync (docs-structure gate asserts the tree)
- [X] T054 Run full validation per `quickstart.md` §1–2: help-surface greps, focused suites, straggler grep, `uv run pytest`, `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli`, `uv run pytest tests/unit/test_docs_structure.py`
- [X] T055 Commit series finalization: conventional-commit `feat(cli)!: split update into upgrade/resize/tag, add host subgroup` with a `BREAKING CHANGE:` footer enumerating removed `update`, relocated `bootstrap`, and the `--user` → `--host-user`/`--node-user` rename (FR-013)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 2 (Foundational)** ← Phase 1. BLOCKS all stories (descriptor fields, rename, helpers).
- **Phases 3–6 (US1–US4)** ← Phase 2 only. Mutually independent — any order or in parallel
  (each adds its own verb, tests, and baseline entries; `update` stays mounted throughout).
  Shared-file coordination: `factory.py`, the four descriptors, `surface_baseline.py`,
  `fake_provider.py`/conformance are touched by multiple stories — sequential within each file.
- **Phase 7 (Removal)** ← US1 + US2 + US3 + US4 complete (FR-004: joint coverage before
  deletion; T049's sweep also asserts flat `bootstrap` is gone, which requires US4).
- **Phase 8 (Polish)** ← Phase 7.

### Within Each Story

Provider implementations `[P]` (different files) → descriptor values → factory builder →
notices/prompts → tests → baseline/conformance wiring + checkpoint `pytest`.

### Parallel Opportunities

- Phase 2: T004+T005 together, then T006+T007 together.
- US1: T010–T013 (four provider files) in parallel; T017–T020 (four test files) in parallel.
- US2: T022–T024 in parallel; T029+T030 in parallel.
- US3: T033–T036 in parallel.
- US4: T041–T044 in parallel.
- Phase 8: T050–T052 in parallel.

### Parallel Example: User Story 1

```bash
# Four provider decompositions concurrently (different files):
Task: "Add upgrade() to src/remo_cli/providers/incus.py, re-point update_entry"      # T010
Task: "Add upgrade() to src/remo_cli/providers/proxmox.py, re-point update_entry"    # T011
Task: "Add upgrade() to src/remo_cli/providers/aws.py, keep IP refresh"              # T012
Task: "Add upgrade() to src/remo_cli/providers/hetzner.py"                           # T013
# Then T014 (descriptors) → T015 (factory) → T016 (shell) → T017–T020 [P] → T021
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phases 1–2 (setup + foundational).
2. Phase 3 (US1 `upgrade`) → checkpoint: the primary maintenance workflow has its predictable
   verb; `update` still exists, so nothing is lost.
3. **STOP and VALIDATE**: quickstart §2 focused suites for `upgrade` + shell prompt.

### Incremental Delivery

US2 (`tag`, closes the shipped untruthful-remedy defect) → US3 (`resize`) → US4 (`host`) — each
independently checkpointed with a green suite. Phase 7 then executes the breaking removal in one
step, and Phase 8 lands docs + the `feat(cli)!:` marker. Everything ships as one PR (research
D10), but the checkpoints make review and bisection tractable.

---

## Notes

- Suite stays green at every checkpoint: `update` is deleted only in Phase 7, and
  `surface_baseline.py` is updated incrementally with each phase that changes the surface.
- Constitution guards throughout: no Click/`sys.exit` in `providers/` (Principle I/III), new
  behavior descriptor-driven only (Principle II, rules G-1…G-8), `tag` idempotence and strict
  failure (Principles VII/III), docs in the same change with gates green (Principle VIII).
- Historical archives (`docs/feature-history.md`, CHANGELOG) keep past-tense references to the
  removed surface — exempt from the T049 sweep.
