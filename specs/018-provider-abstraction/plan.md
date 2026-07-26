# Implementation Plan: Formal Provider Abstraction

**Branch**: `018-provider-abstraction` | **Date**: 2026-07-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/018-provider-abstraction/spec.md`

## Summary

Replace the convention-by-copy provider layer with a formal abstraction: a `Provider` protocol (entry-based verbs strictly typed; heterogeneous create/destroy/update verbs contract-checked against descriptor-declared options), a `ProviderDescriptor` + generic provider registry in core (`core/provider_registry.py`) that becomes the sole dispatch mechanism, and a CLI factory that generates all four provider command groups from descriptors — deleting the four hand-written `cli/providers/*` modules. A typed error taxonomy (`core/errors.py`) with a single CLI translation boundary eliminates the 15 business-layer `sys.exit` sites; five copy-pasted skeletons collapse into shared templates; provider knowledge migrates out of `core/` (SSM branch, completers, name formats, per-type serialization) into descriptor data; and the formalized sync-query contract gains observed-vs-default merge semantics, closing issue #87. Success criterion: a fifth provider = one implementation module + one descriptor registration, zero existing CLI files touched (proven by an in-test FakeProvider in the conformance suite).

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations`, typing.Protocol with modules-as-implementations)

**Primary Dependencies**: Click (CLI generation via programmatic `click.Command`/`click.Group` construction), stdlib `dataclasses`/`inspect`/`ast`; boto3 + hcloud remain optional lazy-imported extras. **No new runtime dependencies.**

**Storage**: N/A — registry format v2 (`~/.config/remo/registry.json`) frozen; no schema or wire changes (FR-025)

**Testing**: pytest (unit + conformance + architecture gates), mypy (Protocol satisfaction), ruff (no providers-package ignores)

**Target Platform**: Linux/macOS workstation CLI; providers layer also imported by the `remo-web` FastAPI service (must never see `SystemExit`)

**Project Type**: Single Python package, existing three-layer architecture (cli/ → providers/ → core/)

**Performance Goals**: CLI startup unchanged — building all command groups is metadata-only; `remo --help`/completion import zero optional SDKs (SC-008)

**Constraints**: Behavior preservation by default; only normalizations are the `create --yes` deprecation and playbook-rc→exit-1 normalization (both documented); Spec-016 sync semantics preserved verbatim (FR-020); full in-repo migration, no released compatibility delegates (clarify Q2)

**Scale/Scope**: 4 providers (~4,800 lines business + 1,375 lines CLI), ~64 `sys.exit` sites total (15 business-layer), 10 SLF001 suppressions, 5 duplicated skeletons, 6 core dispatch sites; largest roadmap item to date — staged in 6 green-tree stages (research.md R10)

## Constitution Check

*GATE: evaluated against `.specify/memory/constitution.md` v1.0.0 — pre-Phase-0 and re-checked post-Phase-1: **PASS** (no violations; Complexity Tracking empty).*

- **I. Defensive Variable Access (Ansible)**: No Ansible playbook changes in scope — the refactor is pure Python around existing playbook invocations. N/A, trivially satisfied.
- **II. Test All Conditional Paths**: Directly served — the conformance suite parametrizes every provider through every contract path, and the previously untested silent branches (shell.py unknown-type, AWS `sys.exit` paths) get explicit tests (SC-004; the audit flagged both as coverage gaps).
- **III. Idempotent by Default**: Destructive operations keep explicit safeguards — the shared destroy template makes the guard→cleanup→confirm ordering normative (contracts/lifecycle-templates.md); sync idempotence pinned by contracts/sync-merge.md case 4.
- **IV. Fail Fast with Clear Messages**: The core of FR-002/FR-006 — typed errors with actionable messages (MissingDependencyError names the extra; unknown type names the type), replacing silent-ignore and scattered exits.
- **V. Documentation Reflects Reality**: FR-023 contributor guide + CHANGELOG entries for both normalizations ship in the same feature; CLAUDE.md/AGENTS.md structure sections updated when the four CLI modules are deleted.

## Project Structure

### Documentation (this feature)

```text
specs/018-provider-abstraction/
├── plan.md              # This file
├── research.md          # Phase 0 — 10 resolved decisions (R1–R10)
├── data-model.md        # Phase 1 — descriptor/registry/protocol/error entities
├── quickstart.md        # Phase 1 — validation guide mapped to SC-001…SC-008
├── contracts/
│   ├── provider-protocol.md      # Protocol + verb-signature conformance rules
│   ├── errors.md                 # Taxonomy, exit-code mapping, prohibitions
│   ├── cli-surface.md            # Generated command/flag matrix + deprecations
│   ├── lifecycle-templates.md    # Destroy/aggregation/extra-vars/resize/table
│   └── sync-merge.md             # Observed-vs-default merge (#87)
└── tasks.md             # Phase 2 (/speckit-tasks — not created here)
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/
│   ├── errors.py                # NEW — ProviderError taxonomy (contracts/errors.md)
│   ├── provider_registry.py     # NEW — ProviderDescriptor, OptionSpec/CommandSpec,
│   │                            #        ConnectionSpec, register/get/all (generic mechanism)
│   ├── provider_protocol.py     # NEW — Provider Protocol (entry-based surface)
│   ├── lifecycle.py             # NEW — shared destroy template
│   ├── ansible_runner.py        # MOD — + build_configure_extra_vars, run_resize_playbook
│   ├── snapshot.py              # MOD — + list_all_snapshots aggregation
│   ├── output.py                # MOD — + render_host_table
│   ├── ssh.py                   # MOD — SSM branch → descriptor ConnectionSpec.proxy_hook
│   ├── reconcile.py             # MOD — DiscoveredHost.observed, merge_entry rule,
│   │                            #        SyncScope via name_format (no literal type tuples)
│   ├── registry.py              # MOD — per-type field map from descriptor.registry_fields
│   │                            #        (ssh pseudo-type stays local + defensive fallback)
│   ├── known_hosts.py           # MOD — name-format checks via descriptors
│   └── completion.py            # DELETED — generic completer generated from name_format
├── providers/
│   ├── incus.py / hetzner.py / aws.py / proxmox.py
│   │                            # MOD — typed errors (0 sys.exit), update_entry/teardown,
│   │                            #        public entry-based snapshot verbs, shared templates,
│   │                            #        AWS proxy_hook + observed-fields probe
│   ├── incus_descriptor.py      # NEW — metadata only (×4: also hetzner_/aws_/proxmox_)
│   └── builtin.py               # NEW — registers the four built-in descriptors
├── cli/
│   ├── main.py                  # MOD — mounts generated groups from all_descriptors()
│   ├── shell.py                 # MOD — if/elif chain → registry lookup + update_entry;
│   │                            #        unknown type errors, ssh explicitly skipped
│   └── providers/
│       ├── factory.py           # NEW — build_provider_group + provider_command wrapper
│       └── {incus,hetzner,aws,proxmox}.py   # DELETED — replaced by factory output
└── web/                         # MOD (small) — catch ProviderError instead of rc/RuntimeError

tests/
├── unit/test_architecture.py                  # NEW — AST gates (no sys.exit / no SLF001)
├── unit/providers/test_provider_conformance.py # NEW — Protocol + signature + FakeProvider
├── unit/cli/test_cli_uniformity.py            # NEW — cross-provider flag/help matrix
├── unit/cli/test_startup_imports.py           # NEW — SC-008 lazy-import gate
└── (existing provider/cli/core suites)        # MOD — migrated to typed-error expectations
```

**Structure Decision**: Existing single-package three-layer layout retained. The descriptor/registry *mechanism* lives in `core/` (generic, no provider knowledge — preserves the layering rule) while descriptor *data* and implementations live in `providers/`; `cli/` shrinks to the factory plus non-provider commands. Naming deliberately avoids a second bare "registry": `provider_registry` vs the host registry `core/registry.py`.

## Delivery staging (feeds /speckit-tasks)

Six green-tree stages (rationale in research.md R10): (1) foundations — errors/registry/protocol/test harnesses; (2) per-provider contract migration, AWS first; (3) shared-template dedup; (4) generated CLI swap + deletions + deprecation notices; (5) core dispatch-site migration (shell/ssh/completion/reconcile/host-registry); (6) #87 merge semantics, gate flip to zero-tolerance, contributor docs + CHANGELOG.

## Complexity Tracking

No constitution violations to justify — table intentionally empty.
