# Implementation Plan: Provider Snapshots

**Branch**: `005-provider-snapshots` | **Date**: 2026-05-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-provider-snapshots/spec.md`

## Summary

Add a per-provider `snapshot` subcommand group (`create`, `list`, `restore`, `delete`) across all four providers (Incus, Proxmox, AWS, Hetzner). Integrate a destroy-time cleanup prompt into the existing `destroy` commands. Snapshots are scoped by provider-side identity (root volume ID on AWS, source server ID on Hetzner, container identity intrinsic on Incus/Proxmox); async create on cloud providers returns immediately and reports status via `list`; operations against pending snapshots fail fast.

Architecturally, follow the existing three-layer split: thin Click commands in `cli/providers/<name>.py` delegate to pure-Python business logic in `providers/<name>.py`, which uses `core/` helpers for shared concerns. No new Ansible playbooks are required — Incus and Proxmox snapshot operations are short enough to run directly over SSH (matching how `_resolve_container_ip` and similar helpers already work). AWS uses `boto3` and Hetzner uses `hcloud`, both lazy-imported per the existing convention.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click 8.1+ (CLI), InquirerPy 0.3.4+ (existing — not directly used by snapshot but available), boto3 (lazy-imported, AWS only), hcloud (lazy-imported, Hetzner only). Subprocess + ssh for Incus/Proxmox (matches existing pattern in `providers/incus.py` and `providers/proxmox.py`).
**Storage**: `~/.config/remo/known_hosts` (existing flat file). No schema changes — snapshot data lives on the provider; the registry already has the per-instance identifiers we need (`KnownHost.host` for AWS volume lookup via the instance, `KnownHost.instance_id` for proxmox VMID and AWS instance ID).
**Testing**: pytest 9.x + pytest-mock (existing). New unit tests under `tests/unit/cli/providers/` and `tests/unit/providers/`. Integration with live providers is out of scope (matches existing pattern — providers are mocked).
**Target Platform**: Linux client (the `remo` CLI runs locally); remote targets are Debian/Ubuntu LXC containers, EC2 instances, and Hetzner Cloud servers.
**Project Type**: Single project (CLI tool). Source under `src/remo_cli/`, tests under `tests/`.
**Performance Goals**: `create` returns within 5 seconds on AWS/Hetzner (async kickoff only) and within 30 seconds on Incus/Proxmox (per SC-006). `list` returns in under 2 seconds for an instance with 50 snapshots.
**Constraints**: boto3/hcloud must be lazy-imported with a clear error if missing (matches the existing `_boto3_session()` and `_hcloud_client()` patterns). No new SSH multiplexing required. Destructive commands MUST prompt by default.
**Scale/Scope**: Steady-state expectation of 0–50 snapshots per instance; no pagination needed. ~26 functional requirements, 4 providers, 4 subcommands each = 16 new commands + 4 destroy-integration touchpoints.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution is Ansible-focused. This feature is primarily Python:

| Principle | Applicability | How addressed |
|---|---|---|
| I. Defensive Variable Access (Ansible) | Low — no new Ansible playbooks expected. If snapshot operations on Incus/Proxmox grow to need playbooks (none currently planned), the `\| default()` rule applies. | N/A for Phase 1; revisit if Phase 2 adds playbook work. |
| II. Test All Conditional Paths | High — every snapshot subcommand has confirm/decline branches, success/failure provider paths, snapshot-present/absent paths, async pending vs. available paths. | Unit tests for each branch; pytest-mock for provider boundaries. |
| III. Idempotent by Default | High — `create` with duplicate name errors (FR-006); destroy-time cleanup of an instance with no snapshots is a no-op (FR-023); `delete` of a missing snapshot is treated as a (loud) error rather than silent success to avoid hiding mistakes. | Encoded in FRs; covered by tests. |
| IV. Fail Fast with Clear Messages | High — Proxmox storage backend detection (FR-005), pending-snapshot rejection (FR-028), AWS mid-flight restore failure (FR-016), name validation (FR-025). | All have explicit FRs; error messages quote the offending input. |
| V. Documentation Reflects Reality | Required at PR time — README must document the new subcommands. | Tasks phase will include README updates. |

**No violations. Gates pass.**

## Project Structure

### Documentation (this feature)

```text
specs/005-provider-snapshots/
├── plan.md              # This file
├── research.md          # Phase 0 output — provider primitives & cross-cutting decisions
├── data-model.md        # Phase 1 output — Snapshot dataclass, status enum
├── quickstart.md        # Phase 1 output — manual end-to-end test recipe per provider
├── contracts/           # Phase 1 output — CLI surface contracts (per provider)
│   ├── incus-snapshot.md
│   ├── proxmox-snapshot.md
│   ├── aws-snapshot.md
│   ├── hetzner-snapshot.md
│   └── destroy-integration.md
└── tasks.md             # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   └── providers/
│       ├── incus.py        # +snapshot subcommand group (~80 lines added)
│       ├── proxmox.py      # +snapshot subcommand group
│       ├── aws.py          # +snapshot subcommand group
│       └── hetzner.py      # +snapshot subcommand group
├── providers/
│   ├── incus.py            # +snapshot_create/list/restore/delete, +destroy hook
│   ├── proxmox.py          # +snapshot_create/list/restore/delete, +destroy hook, +storage detection
│   ├── aws.py              # +snapshot_create/list/restore/delete (incl. volume-swap restore), +destroy hook
│   └── hetzner.py          # +snapshot_create/list/restore/delete (incl. server rebuild), +destroy hook
├── core/
│   ├── snapshot.py         # NEW — name generator (remo-YYYYMMDD-HHMMSS), client-side name validation
│   └── output.py           # +`confirm()` already supports default-False; no change unless --yes flag standardization needed
└── models/
    └── snapshot.py         # NEW — Snapshot dataclass

tests/
└── unit/
    ├── cli/
    │   └── providers/
    │       ├── test_incus_snapshot.py
    │       ├── test_proxmox_snapshot.py
    │       ├── test_aws_snapshot.py
    │       └── test_hetzner_snapshot.py
    ├── providers/
    │   ├── test_incus_snapshot.py
    │   ├── test_proxmox_snapshot.py
    │   ├── test_aws_snapshot.py
    │   └── test_hetzner_snapshot.py
    └── core/
        └── test_snapshot.py
```

**Structure Decision**: Single project, additive-only. No directory restructure; new files live alongside existing per-provider code. The `cli/providers/<name>.py` files already use Click subcommand groups (`@click.group()` patterns) so adding `snapshot` as a nested group is a natural extension. Business logic per the existing three-layer rule lives in `providers/<name>.py`.

## Complexity Tracking

> No constitution violations; no complexity-justification entries required.
