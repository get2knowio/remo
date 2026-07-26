# Implementation Plan: Unified Sync Reconcile

**Branch**: `016-sync-reconcile` | **Date**: 2026-07-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-sync-reconcile/spec.md`

## Summary

Replace four hand-rolled clear-then-repopulate `sync` implementations with one shared reconcile primitive. Each provider contributes a single *probe* — "for this scope, what hosts exist, which carry the managed marker, and was my enumeration complete?" — and a new provider-agnostic `core/reconcile.py` does everything else: diff against the in-scope registry slice, render the plan, gate removals behind consent, and commit via one atomic write.

The technical approach rests on three findings from Phase 0:

1. **No new registry write path is needed.** `mutate_registry()` (`core/registry.py:794-807`) already runs an arbitrary mutator inside the `fcntl` lock with validation and `os.replace`. The reconcile layer sits above it.
2. **The prompt cannot live inside the lock**, so consent is optimistic: the plan records the in-scope baseline it was built from, and the mutator aborts with `ReconcileConflictError` if that slice moved. This satisfies the concurrency edge case instead of papering over it.
3. **Removals require a provably complete enumeration.** Hetzner truncates at 25 servers today and AWS paginates nowhere — under the old semantics that silently lost entries; under reconcile it would silently propose deleting them. Completeness becomes an explicit part of the probe contract.
4. **The query must not filter on the managed marker** (FR-044, added post-planning). AWS's `tag:remo=true` and Hetzner's `label_selector=remo` are server-side filters, so an existing-but-unmarked host is invisible to them and reads as absent — which would have made FR-022 unenforceable on exactly the two providers it matters most for. Both must enumerate broadly and evaluate the marker locally, as Incus and Proxmox already do.

Beyond the three named bugs, the spec's clarifications extend scope to the Hetzner provisioning path (the `remo` label sync queries for is never applied by anything) and change marker semantics so the marker gates *addition* while provider presence gates *removal*.

## Technical Context

**Language/Version**: Python 3.11+ (`requires-python = ">=3.11"`), `from __future__ import annotations`, full type hints

**Primary Dependencies**: Click 8.1+ (CLI), boto3 (AWS), stdlib `urllib`/`json`/`fcntl` (Hetzner API, registry). Ansible 2.14+ / `hetzner.hcloud >= 6.7.0` collection for the Hetzner label work. **No new runtime dependencies.**

**Storage**: Existing JSON registry at `~/.config/remo/registry.json`, format v2. **No schema change, no migration** (SC-011). Instance state is reported, never stored.

**Testing**: pytest + pytest-mock. Existing `tmp_config_dir` fixture (`tests/conftest.py:10-21`) provides a real temporary registry via `REMO_HOME`. `build_plan` is pure, so the classification matrix needs no fixtures at all.

**Target Platform**: Linux/macOS developer workstations (CLI)

**Project Type**: Single-project CLI, three-layer (`cli/` → `providers/` → `core/`)

**Performance Goals**: Exactly one registry write per sync run, down from N+1 today (SC-006). Registry lock held for a list comparison and splice only — all network/SSH work and all prompting happen outside it.

**Constraints**:
- The registry lock is **not reentrant** — the mutator must be pure list-in/list-out and must not call any other registry function.
- `core/registry.py` must never raise `SystemExit` (its module docstring, lines 8-9). The new core module inherits that: it raises and returns codes; only the thin CLI wrapper exits.
- Exit code `2` is reserved for Click/usage errors and must not be emitted by sync.
- `sync` must not mutate provider-side state (FR-008), including during adoption.

**Scale/Scope**: 4 providers · 43 functional requirements · 1 new core module · ~6 existing modules touched · 1 Ansible role task · README + 4 provider docs

## Constitution Check

*GATE: evaluated before Phase 0 and re-evaluated after Phase 1 design.*

| Principle | Assessment | Verdict |
|---|---|---|
| **I. Defensive Variable Access (Ansible)** | The only Ansible change is adding a `labels:` key to an existing `hetzner.hcloud.server` task (`ansible/roles/hetzner_server/tasks/main.yml:55-69`). It registers no new variable and adds no `when:`/`register:`. The existing `hetzner_server_result` registration is untouched. Pre-commit grep for `.rc ==` / `.stdout` without `\| default()` still applies. | **PASS** |
| **II. Test All Conditional Paths** | This is the binding gate. The consent gate has five outcomes (no removals · `--yes` · confirmed · declined · non-interactive); `complete` and `marked` each branch two ways; `--dry-run` and `--all` compose with all of them. Two providers currently have **zero** sync tests, and where sync is tested the destructive step is mocked out — so the existing suite would not have caught either AWS bug. SC-008 makes closing this explicit, and Phase 2 tasks must enumerate the branches rather than sampling them. | **PASS with obligation** |
| **III. Idempotent by Default** | FR-036 requires a second run to be a no-op that does not prompt (verified in quickstart Scenario 4). FR-033 requires the Hetzner label write to report no change when already present. Destructive operations gain an explicit safeguard where none existed: today's sync deletes registry entries with no confirmation at all. | **PASS — strengthens** |
| **IV. Fail Fast with Clear Messages** | FR-009 requires a failed probe to abort with the registry untouched. Phase 0 found three silent failures that this converts to loud ones — most importantly `_read_tags_by_vmid` (`proxmox.py:145-180`) ignoring its return code, which today turns an SSH failure into "zero containers are marked" and wipes the node. Error messages must name what failed, the scope, and the remedy. | **PASS — strengthens** |
| **V. Documentation Reflects Reality** | FR-038 is a blocking requirement. `README.md:405-424` currently documents the old marker semantics and states that a later default sync drops `--all`-adopted entries — false after this change. `docs/proxmox.md:64-65` describes sync as "rebuild known_hosts entries", which is the behaviour being removed. `docs/incus.md` and `docs/hetzner.md` have no sync section at all. | **PASS with obligation** |

**Violations requiring justification**: none. The Complexity Tracking section is therefore omitted.

**Post-Phase-1 re-evaluation**: The design adds exactly one new module and no new dependency, layer, or abstraction beyond the probe seam the spec explicitly asks for. Provider code shrinks — four bespoke sync bodies become four probes plus a two-line delegation. No gate moved from PASS.

## Project Structure

### Documentation (this feature)

```text
specs/016-sync-reconcile/
├── plan.md                      # This file
├── research.md                  # Phase 0: 12 findings with file:line evidence
├── data-model.md                # Phase 1: in-memory entities, classification matrix
├── quickstart.md                # Phase 1: validation scenarios
├── contracts/
│   ├── cli-sync.md              # User-facing CLI surface, output, exit codes
│   └── provider-probe.md        # Internal provider seam
├── checklists/requirements.md   # Spec quality checklist (16/16)
└── tasks.md                     # Phase 2 output — NOT created by /speckit-plan
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/
│   ├── reconcile.py             # NEW — SyncScope, DiscoveredHost, ProbeResult,
│   │                            #   ReconcilePlan, merge_entry, build_plan (pure),
│   │                            #   render_plan, consent gate, apply, run_sync driver,
│   │                            #   exit-code constants, ProbeError/ReconcileConflictError
│   ├── registry.py              # UNCHANGED — mutate_registry() used as-is
│   ├── known_hosts.py           # UNCHANGED — clear_* helpers stay for other callers
│   └── output.py                # UNCHANGED — confirm()/print_* reused
├── providers/
│   ├── incus.py                 # sync -> probe + run_sync; _resolve_container_ip
│   │                            #   soft-fails instead of sys.exit(1)
│   ├── proxmox.py               # sync -> probe + run_sync; _read_tags_by_vmid gains
│   │                            #   a return-code check (silent-failure fix)
│   ├── aws.py                   # sync -> probe + run_sync; paginate; retain non-terminal
│   │                            #   states; region-scoped; prefer remo_resource_name tag
│   └── hetzner.py               # sync -> probe + run_sync; _hetzner_api_paged;
│                                #   _apply_managed_label (read-merge) called from update
└── cli/providers/
    ├── incus.py, proxmox.py     # + --yes, --dry-run; sys.exit(rc)
    ├── aws.py                   # + --yes, --dry-run, --all; sys.exit(rc)
    └── hetzner.py               # + --yes, --dry-run, --all; sys.exit(rc)

ansible/roles/hetzner_server/tasks/main.yml    # + labels: {remo: "true"} on the server task

tests/
├── unit/core/test_reconcile.py                # NEW — pure plan logic, full matrix
├── unit/providers/test_{incus,proxmox,aws,hetzner}_sync.py   # NEW — per-provider probes
├── unit/cli/providers/                        # flags, exit codes; 2 existing files updated
├── integration/test_sync_reconcile.py         # NEW — real temp registry, one-write assertion
└── unit/providers/test_provider_registry_entries.py   # UPDATED — pinned shapes move

README.md · docs/{aws,hetzner,incus,proxmox}.md            # FR-038
```

**Structure Decision**: Single-project CLI following the existing three-layer split. `reconcile.py` belongs in `core/` because it must have no provider knowledge — that is precisely what SC-005 measures ("adding a fifth provider requires supplying only a desired-hosts query"). Provider modules keep their business logic and gain a probe; the `cli/` layer stays parsing-only, gaining flags and the `sys.exit(rc)` that all four sync wrappers are missing today.

## Implementation Phases

Sequenced so each phase is independently valuable and testable, mirroring the spec's user-story priorities.

### Phase A — Reconcile core (blocks everything)

`core/reconcile.py` plus `tests/unit/core/test_reconcile.py`. `build_plan` is pure, so the entire classification matrix, `merge_entry` semantics, and scope predicates are testable before any provider is touched. Delivers no user-visible change on its own.

### Phase B — One provider end-to-end (US1, P1)

Wire Incus first: smallest probe, existing test scaffolding, and `--all`/`--use-ip` already present to prove they survive. Proves the whole pipeline — scope, plan, consent, single atomic write, exit codes — against a real temp registry. This is the MVP slice: the empty-result wipe is fixed for one provider.

### Phase C — AWS correctness (US2 + US3, P1)

Region scoping, pagination, non-terminal states, address preservation, and the `remo_resource_name` name fix. Highest bug density; depends on Phase A/B having settled the contract.

### Phase D — Proxmox and Hetzner probes (US4, P2)

Proxmox brings the `_read_tags_by_vmid` silent-failure fix. Hetzner brings `_hetzner_api_paged` and its first-ever sync tests. Completes uniform behaviour across all four.

### Phase E — Adoption parity and the Hetzner label (US5, P3)

`--all` for AWS and Hetzner with stated criteria; the Ansible label at create; the read-merge backfill in `hetzner update`. Independently deferrable behind the P1 fixes.

### Phase F — Documentation (FR-038, blocking)

README command reference and troubleshooting prose, the four provider docs, and removal of the now-false "a later default sync will drop those unmarked one(s) again" warning from source.

## Risks

| Risk | Mitigation |
|---|---|
| Hetzner's API replaces the label map wholesale, so a naive backfill drops a user's own labels (violates FR-034) | Read-merge in Python via `_hetzner_api`, mirroring `_apply_managed_marker`'s `(ok, err)` contract. Explicit test in quickstart Scenario 7. |
| Changing `sync`'s return type to `int` breaks existing CLI tests | Known and enumerated (research R11): two sync-flag tests plus the AWS `ec2` stubs. Update, don't delete. |
| `test_provider_registry_entries.py` pins exact registry shapes | The probe must build entries identically to each provider's `create`, so create and sync agree. Where a shape legitimately changes (AWS `host` no longer falls back to the instance id), update the pin deliberately. |
| The optimistic-concurrency check could false-positive under normal use | The baseline compares only the *in-scope* slice, so concurrent syncs of different scopes never collide. A genuine collision is rare and fails loudly with a re-run instruction, which is the required behaviour. |
| Adoption on Hetzner means "every server in the project" — blunter than AWS's `remo-*` | FR-030 requires printing the criteria. Making the bluntness visible is the mitigation; there is no naming convention to infer from (research R7). |

## Out of scope

Found during research, deliberately excluded — see research.md R12 for detail: the stale `boto3`/`hcloud` extras documented in `CLAUDE.md`, the unused `hcloud` dependency, the `cx23`/`cx22` group_vars-vs-role-defaults mismatch, the ignored `--yes` on incus/proxmox `create`, the hardcoded `access_mode="ssm"` in `aws update`, and the other unpaginated AWS calls (`describe_snapshots`, `describe_volumes`, IAM listings).
