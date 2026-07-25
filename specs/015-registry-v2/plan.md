# Implementation Plan: Versioned Structured Host Registry (Registry v2)

**Branch**: `015-registry-v2` | **Date**: 2026-07-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-registry-v2/spec.md`

## Summary

Replace the colon-delimited `~/.config/remo/known_hosts` flat file with a versioned JSON registry (`registry.json`, format version 2) whose entries carry explicitly named per-type fields (no overloaded slots) and an explicit `access` attribute. A single new accessor module (`core/registry.py`) owns parsing, serialization, validation, advisory locking, and migration for every consumer — CLI, providers, and web service — collapsing today's three independent parsers. The CLI migrates lazily on first read (legacy file renamed in place as backup); the web service reads both formats in place and never migrates. The adopt/push mirror payload moves to version 2 with an advertised-capability handshake so version skew fails fast before mutation, while an upgraded service still accepts v1 payloads. The `KnownHost` dataclass remains the in-memory model; only serialization, parsing, and storage semantics change.

## Technical Context

**Language/Version**: Python 3.11+ (`from __future__ import annotations`, type hints; existing codebase conventions)

**Primary Dependencies**: Stdlib only for this feature — `json` (format), `fcntl` (advisory locking), `os.replace` (atomic writes). No new runtime dependencies. Web-side touches use the existing FastAPI/Pydantic surface (already in the `web` extra).

**Storage**: Flat file `${REMO_HOME}/registry.json` (human-readable, diffable JSON, 2-space indent, deterministic entry ordering). Legacy `${REMO_HOME}/known_hosts` retained only as migration source / renamed backup. Sidecar lock file `${REMO_HOME}/registry.lock`.

**Testing**: pytest (existing suite layout under `tests/`). New: migration matrix tests, round-trip property tests, multiprocess concurrency stress test, setup-API version-skew contract tests.

**Target Platform**: Linux + macOS workstations (CLI); Linux containers (web service). `fcntl` is available on both; Windows is not a supported platform today.

**Project Type**: Single Python package (`src/remo_cli`, src layout) + existing FastAPI web sub-package. No frontend changes.

**Performance Goals**: Registry read/parse/write overhead < 100 ms per command invocation at 200 entries (SC-008). Lock acquisition bounded at 5 s (FR-017).

**Constraints**: No database, no new runtime deps; file must remain human-diffable (SC-007); readonly consumers must never write or mkdir (FR-011/FR-013); every write atomic via same-directory temp file + `os.replace` (FR-018); migration crash-safe and idempotent (FR-010).

**Scale/Scope**: ≤ 200 registry entries (personal-fleet tool). ~10 write call sites in `providers/*`, 3 read-path implementations to collapse, 2 web endpoints (setup status/PUT) and the push engine (`core/web_adopt.py`) to move to payload v2.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution (v1.0.0) is Ansible-centric; Principle I (Defensive Variable Access) is not applicable — this feature touches no Ansible code. The remaining principles apply as follows and are satisfied by the design:

| Principle | Application to this feature | Status |
|-----------|-----------------------------|--------|
| II. Test All Conditional Paths | Migration has many branches (legacy-only / v2-only / both-present / garbage lines / newer-version / readonly). The migration test matrix (quickstart §3, data-model §6) exercises every branch on fresh state AND pre-existing state, matching the "fresh systems AND systems with existing state" rule. | PASS |
| III. Idempotent by Default | Migration is idempotent and crash-safe (FR-010): re-runs are no-ops, interrupted runs converge on next read; backups are never clobbered (FR-009); all writes check-then-act under a lock. | PASS |
| IV. Fail Fast with Clear Messages | Validation happens before anything touches disk (FR-016); version-skew push aborts before mutation with which-side-to-upgrade remediation (FR-021); "registry busy", "written by a newer version", and skipped-line warnings all name the problem and the fix (FR-025). | PASS |
| V. Documentation Reflects Reality | README registry documentation, `docs/web-session-interface.md` (adoption payload), and CLAUDE.md's "Flat file (colon-delimited)" technology line must be updated in the same change set; called out as explicit tasks. | PASS (tracked) |

No violations → Complexity Tracking table not required.

**Post-Phase-1 re-check (2026-07-25)**: Design artifacts introduce no new projects, no new dependencies, and no deviations from the above. GATE still PASS.

## Project Structure

### Documentation (this feature)

```text
specs/015-registry-v2/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── registry-file-v2.md        # On-disk format contract (JSON Schema + examples)
│   ├── registry-accessor-api.md   # Python API contract for core/registry.py
│   └── mirror-payload-v2.md       # Setup API payload v2 + version-negotiation contract
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/
│   ├── registry.py          # NEW — single accessor: parse/serialize/validate/lock/migrate
│   ├── known_hosts.py       # SLIMMED — public functions become thin delegates to registry.py
│   ├── config.py            # get_registry_path()/readonly variants added beside known_hosts paths
│   └── web_adopt.py         # Payload v2, capability handshake, push-cache v2 reset
├── models/
│   └── host.py              # KnownHost unchanged as model; from_line/to_line retained for
│                            #   legacy parse + payload-v1 compatibility only (marked legacy)
├── providers/
│   ├── incus.py             # Write call sites move to registry mutate API (mechanical)
│   ├── hetzner.py           #   "
│   ├── aws.py               #   "
│   ├── proxmox.py           #   "
│   └── added.py             #   "
└── web/
    ├── discovery.py         # Drop private parser → registry.read_registry(readonly=True)
    ├── state.py             # Registry probe accepts registry.json OR legacy known_hosts
    ├── check.py             # Same probe update; verify output mentions format found
    └── api/
        └── setup.py         # Drop private parser; PUT accepts payload v1+v2, stores v2;
                             #   status advertises payload_versions

tests/
├── unit/
│   ├── core/
│   │   ├── test_registry_format.py    # NEW — round-trip, validation, tolerant parse, versions
│   │   ├── test_registry_migration.py # NEW — migration matrix (all types × field combos)
│   │   └── test_registry_locking.py   # NEW — lock timeout, degradation, crash atomicity
│   ├── providers/
│   │   ├── test_provider_registry_entries.py # NEW — per-provider save-path fixtures (R5 risk pin)
│   │   └── test_added_add.py          # EXTENDED — IPv6 added-host end-to-end (bracket + bare forms)
│   └── web/
│       └── test_registry_readonly.py  # NEW — both formats, ro volume, parity, broken mapping
├── integration/
│   ├── test_registry_concurrency.py   # NEW — multiprocess lost-update stress
│   └── test_setup_payload_versions.py # NEW — v1/v2/unknown payloads against setup API
├── perf/
│   └── test_registry_perf.py          # NEW — 200-entry round-trip < 100 ms (SC-008)
└── (existing tests updated where they fabricate legacy known_hosts files)
```

**Structure Decision**: Single-project src layout (existing). The feature adds one new core module and three contract documents; everything else is modification-in-place. No new packages, services, or build steps.

## Complexity Tracking

> Not required — Constitution Check passed with no violations.
