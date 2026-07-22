# Implementation Plan: Managed-Instance Tagging & Filtered Sync (Incus / Proxmox)

**Branch**: `013-managed-instance-tags` | **Date**: 2026-07-22 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/013-managed-instance-tags/spec.md`

## Summary

Mark `remo`-created Incus and Proxmox containers with a provider-native managed
marker at provision time, and make `sync` filter on that marker by default so it
stops importing every unrelated container on a hypervisor host. This brings the
two hypervisor providers in line with AWS (`tag:remo=true`) and Hetzner
(`label_selector=remo`), which already filter on a native marker.

Technical approach: apply and read the marker **host-side in the Python provider
layer** (`providers/incus.py`, `providers/proxmox.py`), reusing the existing
per-host SSH helpers (`_ssh_run_on_incus_host`, `_ssh_run`). This is required
because `update`'s dev-tools playbook connects to the *container's* IP, not the
hypervisor host — only the Python layer holds a host/node connection at both
`create` and `update` time. The marker literal is a fixed built-in constant
(clarified), so a single shared definition in `core/config.py` keeps Incus and
Proxmox consistent. `sync` gains an `--all` flag that restores today's unfiltered
behavior; the default path filters on the marker and prints an actionable hint
(naming skipped containers) when it skips anything.

## Technical Context

**Language/Version**: Python 3.11+ (existing `remo_cli` src-layout package)

**Primary Dependencies**: Click (CLI), stdlib `subprocess`/`shlex` for host
commands over SSH. No new runtime dependencies. Provider marker mechanics use
the native `incus` and `pct`/`pvesh` CLIs already invoked over SSH.

**Storage**: Marker is authoritative on the **provider side** (Incus `user.*`
config key; Proxmox guest tag). The local flat-file registry
(`~/.config/remo/known_hosts`) is unchanged — it does not record marker state
(explicitly out of scope).

**Testing**: pytest with `mocker.patch` of the provider SSH helpers
(`tests/unit/providers/`, `tests/unit/cli/providers/`), mirroring the existing
snapshot test suites. No live hypervisor required.

**Target Platform**: Linux workstation running the `remo` CLI against Incus
(localhost or remote) and Proxmox nodes over SSH.

**Project Type**: Single-project Python CLI (three-layer: cli/ → providers/ →
core/).

**Performance Goals**: Marker detection during `sync` MUST stay bounded to a
small, constant number of host queries (FR-013) — no per-container round-trip
that scales with unrelated containers.

**Constraints**: Marker application MUST be idempotent (FR-002) and MUST
preserve pre-existing Proxmox tags (FR-003). `sync` MUST remain read-only on
container state (FR-010). AWS/Hetzner behavior MUST NOT change (FR-011).

**Scale/Scope**: Two providers, two commands touched each (`create`, `update`,
`sync`), plus one shared constant and a hint helper. Tens of containers per host
is the realistic ceiling.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution is Ansible-centric; this feature is implemented in the Python
provider layer and touches **no Ansible**. The principles still map cleanly:

| Principle | Applies? | How this plan satisfies it |
|-----------|----------|----------------------------|
| I. Defensive Variable Access (Ansible) | N/A | No Ansible tasks added or changed; marker logic lives in Python. |
| II. Test All Conditional Paths | ✅ | Tests cover marked/unmarked/mixed hosts, default vs `--all`, idempotent re-apply, and marker-apply failure (FR-005). Both branches of every new `if all:`/`if marked:` path exercised. |
| III. Idempotent by Default | ✅ | `create`/`update` re-apply is a no-op (Incus: set-same-value; Proxmox: skip write when `remo` already in tag set). Verified by SC-005 (config identical apart from marker). |
| IV. Fail Fast with Clear Messages | ✅ | Marker-apply failure warns with actionable text (FR-005); filtered `sync` prints a named hint with both remedies (FR-008). |
| V. Documentation Reflects Reality | ✅ | README `sync` sections (lines ~236–268, ~354–357) updated to state Incus/Proxmox now filter by default and document `--all`. |

**Gate result: PASS** — no violations, Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/013-managed-instance-tags/
├── plan.md              # This file
├── research.md          # Phase 0 output — marker mechanics decisions
├── data-model.md        # Phase 1 output — marker + entities
├── quickstart.md        # Phase 1 output — validation scenarios
├── contracts/           # Phase 1 output
│   ├── cli-sync.md      # `--all` flag + sync output contract
│   └── marker-commands.md  # host-command contract per provider
└── tasks.md             # /speckit-tasks output (NOT created here)
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/
│   └── config.py                 # + fixed marker constants (single source)
├── providers/
│   ├── incus.py                  # + _apply_managed_marker(), marker-aware
│   │                             #   listing; create()/update() apply marker;
│   │                             #   sync(all=False) filters
│   └── proxmox.py                # + _apply_managed_marker() (tag union),
│                                 #   bulk tag read; create()/update() apply;
│                                 #   sync(all=False) filters
├── cli/providers/
│   ├── incus.py                  # + `--all` flag on `sync`
│   └── proxmox.py                # + `--all` flag on `sync`
└── core/
    └── output.py                 # (reuse) print_info/print_warning for hint

tests/unit/
├── providers/
│   ├── test_incus_marker.py      # new: apply/idempotency/filtered sync
│   └── test_proxmox_marker.py    # new: tag union/preserve/filtered sync
└── cli/providers/
    ├── test_incus_sync_all.py    # new: `--all` flag wiring + hint output
    └── test_proxmox_sync_all.py  # new: `--all` flag wiring + hint output

README.md                         # sync docs updated (Principle V)
```

**Structure Decision**: Single-project Python CLI, existing three-layer
architecture. Marker business logic goes in `providers/`, the fixed constant in
`core/config.py`, and only the thin `--all` flag lands in `cli/providers/`. No
new modules or Ansible roles are introduced.

## Complexity Tracking

> No constitution violations — section intentionally empty.
