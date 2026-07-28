# Implementation Plan: CLI Plane Separation — Intent-Named Verbs and a Host Subgroup

**Branch**: `021-cli-plane-separation` | **Date**: 2026-07-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/021-cli-plane-separation/spec.md`

## Summary

Split the three-intent `remo <provider> update` verb into three single-intent verbs —
`upgrade` (in-instance configure play only), `resize` (resource change only), and `tag`
(managed-marker write only, marker-supporting providers only) — and move `bootstrap` under a
new descriptor-driven `remo <provider> host` subgroup, all as a clean break (no aliases,
`update` and flat `bootstrap` removed). Rename `--user` → `--host-user` (incus) /
`--node-user` (proxmox) everywhere it appears. Make every printed remedy truthful: the
registry-migration notice and `sync`'s "Mark permanently:" line recommend `tag`; `remo shell`'s
version-mismatch prompt names `upgrade`.

Technical approach: extend `ProviderDescriptor` with `upgrade_options`, `resize_dimensions` +
`resize_options`, `tag_options`, and `host_commands` (replacing `update_options`); extend
`CommandSpec` with a positional-target spec so host commands take the host positionally;
`cli/providers/factory.py` grows `_build_upgrade`/`_build_resize`/`_build_tag`/`_build_host_group`
builders (dropping `_build_update` and flat-mounted `bootstrap`); each provider module splits its
`update()` into `upgrade()`/`resize()`/`tag()` reusing the existing private helpers
(`_apply_managed_marker`, `_run_resize_playbook`, configure-play blocks) unchanged. No registry
schema change, no new runtime dependencies, no Ansible playbook changes.

## Technical Context

**Language/Version**: Python 3.11+ (supported matrix 3.11/3.12/3.13)

**Primary Dependencies**: Click (CLI), existing `core/` infrastructure (`provider_registry`,
`ansible_runner`, `known_hosts`, `reconcile`, `lifecycle`, `errors`). No new dependencies.

**Storage**: Registry v2 (`~/.config/remo/registry.json`) — format and semantics unchanged
(FR-011). The `host_user`/`node_user` JSON keys already match the new flag spellings.

**Testing**: pytest (`tests/unit/`, `tests/integration/`); the frozen CLI-surface baseline
(`tests/unit/cli/surface_baseline.py`), the FakeProvider conformance suite
(`tests/unit/providers/test_provider_conformance.py`), and the docs-structure gate all update in
this change.

**Target Platform**: Developer workstations (Linux/macOS) driving Incus/Proxmox hosts and
AWS/Hetzner APIs; Ansible invoked via subprocess (playbooks untouched).

**Project Type**: CLI (single Python package, src layout) — only `src/remo_cli/{cli,providers,core}/`,
tests, docs, and two CI scripts change. `web/`, `frontend/`, `ansible/` are untouched.

**Performance Goals**: `remo --help` and shell completion must continue to import zero provider
SDKs (Spec 018 SC-008 preserved — descriptors stay metadata-only).

**Constraints**: Breaking CLI release (user waived compatibility, FR-013 conventional-commit
`!` marker); zero remaining references to `remo <type> update` or flat `bootstrap` in
current-surface docs/help/code (SC-004); no registry schema change; no new runtime deps.

**Scale/Scope**: 4 built-in providers + the descriptor-driven fifth-provider guarantee
(FakeProvider conformance, SC-005). ~15 source modules, ~15 test modules, 8 doc surfaces,
2 CI/integration scripts.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Source: `.specify/memory/constitution.md` (v2.0.1).

| # | Principle | Check for this feature | Status |
|---|-----------|------------------------|--------|
| I | Layered Architecture | New verbs wired in `cli/providers/factory.py` (parsing only); business logic stays in `providers/*.py`; no new `core/` provider knowledge — `tag` availability and `host` subgroup presence are driven by descriptor fields (`supports_managed_marker`, `host_commands`). The factory keeps calling only public provider functions (`upgrade`/`resize`/`tag`/`bootstrap`), never `_`-private helpers. | PASS |
| II | Providers Are Declared | Every new surface element is a descriptor declaration: `upgrade_options`, `resize_dimensions`/`resize_options`, `tag_options`, `host_commands`. No `host.type` literals added anywhere; the fifth-provider guarantee is extended by the conformance test (FR-006/SC-005). Shared flags remain single `OptionSpec` objects; the `--host-user`/`--node-user` specs are per-provider by design (different names), each declared once in its descriptor module. | PASS |
| III | Typed Errors, One Exit Boundary | `tag` failure raises `OperationFailedError` (exit 1, not warn-and-continue); dimensionless `resize` raises `PreconditionError` listing that provider's dimensions; unresolvable Proxmox VMID raises `PreconditionError`. `provider_command` remains the only exit boundary; new verbs return `None` and route through it. | PASS |
| IV | Generated Contracts | No service response model or WS frame changes — the four generated artifacts are untouched; drift gates stay green by construction. | N/A |
| V | Defensive Variable Access | No Ansible playbook or role changes; the same playbooks are invoked from the new verbs with identical extra-vars. | N/A |
| VI | Test Skip/Fail Paths | FR-012 enumerates the error/skip paths to cover: unregistered name, added-SSH-host guard on all three verbs, no-dimension `resize`, failed marker write, already-tagged no-op, absent-`tag`/absent-`host` providers. Existing marker/guard/shell characterization tests are re-homed, not deleted. | PASS |
| VII | Idempotent & Re-runnable | `tag` twice → reported no-op exit 0 (read-before-write per provider); `upgrade` converges (playbook already idempotent); `resize` to current size stays a provider-level no-op. No registry writes outside `core/registry.py` (AWS `upgrade` keeps its existing `save_known_host` IP refresh, which already routes through the accessor). | PASS |
| VIII | Docs Reflect Reality | README, docs/{incus,proxmox,aws,hetzner,providers}.md, CLAUDE.md/AGENTS.md command tables and structure notes, ansible/README.md ship in the same change; `tests/unit/test_docs_structure.py` and the help-text/surface tests gate stragglers. Historical archives (docs/feature-history.md, CHANGELOG) legitimately describe the removed surface in past tense and are exempt from SC-004's zero-reference rule. | PASS |

**Post-Phase-1 re-check**: PASS — the design introduces no new layers, no hand-authored
contract mirrors, and no descriptor bypasses. See research.md D1–D10 for the decisions
that keep it that way.

## Project Structure

### Documentation (this feature)

```text
specs/021-cli-plane-separation/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/
│   ├── cli-surface.md          # The new per-provider command surface (the external contract)
│   └── descriptor-schema.md    # ProviderDescriptor/CommandSpec extensions (the internal contract)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── shell.py                    # version-mismatch prompt names `remo <type> upgrade <name>`;
│   │                               #   _run_provider_update wording → upgrade (still calls update_entry)
│   └── providers/factory.py        # -_build_update; +_build_upgrade/_build_resize/_build_tag/
│                                   #   _build_host_group; _resolve_entry_for_destroy keys the user
│                                   #   hint off the descriptor's `*_user` registry field (not "user")
├── providers/
│   ├── incus.py                    # update() → upgrade()/resize()/tag(); update_entry → upgrade path;
│   │                               #   bootstrap signature: positional-host + host_user param
│   ├── proxmox.py                  # same split; node_user param; tag() resolves VMID (PreconditionError)
│   ├── aws.py                      # update() → upgrade()/resize(); no tag; keeps IP-refresh registry write
│   ├── hetzner.py                  # update() → upgrade()/resize()/tag() (label); no host options
│   ├── incus_descriptor.py         # HOST_USER → --host-user/host_user; upgrade/resize/tag/host_commands
│   ├── proxmox_descriptor.py       # _NODE_USER → --node-user/node_user; same field migration
│   ├── aws_descriptor.py           # upgrade_options/resize_dimensions; no tag_options/host_commands
│   └── hetzner_descriptor.py       # upgrade_options/resize_dimensions/tag_options; no host_commands
├── core/
│   ├── provider_registry.py        # ProviderDescriptor: -update_options, +upgrade_options,
│   │                               #   +resize_dimensions, +resize_options, +tag_options, +host_commands;
│   │                               #   CommandSpec: +target (ArgumentSpec); __post_init__ dup-check loop
│   │                               #   extended; USER catalog spec retired if no consumers remain
│   ├── known_hosts.py              # _print_tagging_notice → `remo <type> tag <name>`; docstring corrected
│   └── reconcile.py                # render_plan mark_cmd → `remo <type> tag <n>`

tests/
├── unit/cli/surface_baseline.py            # frozen surface rewritten to the new verbs/flags
├── unit/cli/test_surface_preservation.py   # consumes updated baseline
├── unit/cli/test_cli_uniformity.py         # update → upgrade/resize in shared-option pairs
├── unit/cli/test_main.py                   # per-provider expected subcommand lists
├── unit/cli/test_shell.py                  # prompt-names-upgrade assertions
├── unit/cli/providers/test_*_sync_all.py   # --user → --host-user/--node-user
├── unit/providers/fake_provider.py         # +upgrade/resize/tag impls, +host_commands descriptor entry
├── unit/providers/test_provider_conformance.py  # verb loops, group-membership, positional-arg awareness
├── unit/providers/test_{incus,proxmox}_marker.py, test_hetzner_label.py  # re-homed onto tag/upgrade
├── unit/providers/test_added_provider_guard.py  # guard on upgrade/resize/tag
├── unit/core/test_migration_tagging_notice.py   # notice recommends tag
└── unit/core/test_reconcile.py                  # Mark permanently: tag

README.md, docs/{incus,proxmox,aws,hetzner,providers}.md, CLAUDE.md, AGENTS.md, ansible/README.md
.github/workflows/smoke-test.yml, tests/integration/orbstack.sh
```

**Structure Decision**: Existing single-project src layout; no new modules or directories. The
change is confined to the CLI/provider/core Python layers plus tests, docs, and two CI scripts —
`web/`, `frontend/`, `models/`, and `ansible/` are untouched.

## Complexity Tracking

No constitution violations — table not required.
