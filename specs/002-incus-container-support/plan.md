# Implementation Plan: Incus Container Support

**Branch**: `002-incus-container-support` | **Date**: 2026-01-07 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-incus-container-support/spec.md`

## Summary

Extend remo's infrastructure provisioning with Incus/LXC container support, enabling local container creation and management using the same Ansible workflow style as remote Hetzner hosts. Technical approach: Create an `incus_container` Ansible role for container lifecycle management (create, configure, destroy), a dynamic inventory plugin for automatic container discovery, and integrate SSH bootstrapping via cloud-init for key injection.

## Technical Context

**Language/Version**: Ansible 2.14+ / YAML
**Primary Dependencies**: `ansible.builtin`, `community.general` (existing), Incus CLI (local)
**Storage**: N/A (Incus storage pools already configured by 001-bootstrap-incus-host)
**Testing**: Manual playbook execution + idempotency verification
**Target Platform**: Linux workstation (OpenSUSE Tumbleweed, future: Ubuntu)
**Project Type**: Single project (Ansible roles and playbooks)
**Performance Goals**: Container provisioning + SSH accessible within 3 minutes
**Constraints**: Must work with macvlan networking (host cannot reach containers directly)
**Scale/Scope**: Single Incus host, 10+ containers manageable simultaneously

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Defensive Variable Access | ✅ REQUIRED | All registered variable attributes MUST use `\| default()` filters |
| II. Test All Conditional Paths | ✅ REQUIRED | Test playbooks on fresh + existing systems |
| III. Idempotent by Default | ✅ REQUIRED | `changed_when` must be accurate; check state before changes |
| IV. Fail Fast with Clear Messages | ✅ REQUIRED | Pre-flight checks with actionable error messages |
| V. Documentation Reflects Reality | ✅ REQUIRED | Update README alongside feature delivery |

**Gate Status**: PASS - All principles applicable and will be enforced during implementation.

## Project Structure

### Documentation (this feature)

```text
specs/002-incus-container-support/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
ansible/
├── roles/
│   ├── incus_bootstrap/          # Existing (from 001)
│   │   ├── defaults/main.yml
│   │   ├── handlers/main.yml
│   │   └── tasks/main.yml
│   └── incus_container/          # NEW - Container provisioning role
│       ├── defaults/main.yml     # Default container settings
│       ├── tasks/
│       │   ├── main.yml          # Entry point with state routing
│       │   └── preflight.yml     # Pre-flight validation checks
│       └── handlers/main.yml     # Success message handlers
│   └── incus_container_teardown/ # NEW - Container destruction role
│       ├── defaults/main.yml     # Destruction options
│       └── tasks/main.yml        # Teardown logic
├── inventory/
│   ├── hosts.yml                 # Existing static inventory
│   └── incus_containers.yml      # NEW - Static container inventory
├── incus_bootstrap.yml           # Existing (from 001)
├── incus_container.yml           # NEW - Container provisioning playbook
├── incus_container_configure.yml # NEW - Apply roles to containers
├── incus_container_teardown.yml  # NEW - Container destruction playbook
└── group_vars/
    ├── all.yml                   # Existing
    └── incus_containers.yml      # NEW - Container-specific vars
```

**Structure Decision**: Ansible infrastructure project following established patterns from 001-bootstrap-incus-host. New `incus_container` role for container lifecycle, separate playbooks for each operation (provision, configure, teardown) mirroring Hetzner workflow.

## Post-Design Constitution Check

*Re-evaluated after Phase 1 design artifacts generated.*

| Principle | Status | Design Compliance |
|-----------|--------|-------------------|
| I. Defensive Variable Access | ✅ COMPLIANT | Research doc includes patterns with `\| default()` for all registered variables |
| II. Test All Conditional Paths | ✅ COMPLIANT | Data model defines idempotency behavior for all states (absent, stopped, running) |
| III. Idempotent by Default | ✅ COMPLIANT | Role contracts specify `changed_when` requirements and state checks |
| IV. Fail Fast with Clear Messages | ✅ COMPLIANT | Pre-flight checks documented in contracts (Incus daemon, image, storage, network, SSH key) |
| V. Documentation Reflects Reality | ✅ COMPLIANT | Quickstart doc created alongside design; README update required at implementation |

**Post-Design Gate Status**: PASS - Design artifacts enforce all Constitution principles.

## Complexity Tracking

> No constitution violations requiring justification.
