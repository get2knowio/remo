# Implementation Plan: Incus Container Support

**Branch**: `002-incus-container-support` | **Date**: 2025-12-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/002-incus-container-support/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Extend remo's infrastructure provisioning to support Incus/LXC containers on a local host. This feature creates Ansible playbooks and roles to provision, configure, and destroy containers using the same workflow patterns as Hetzner VMs. Containers will be managed via SSH, tracked in Ansible inventory, and configured using existing roles (docker, nodejs, user_setup).

## Technical Context

**Language/Version**: Ansible 2.14+ / YAML
**Primary Dependencies**: `ansible.builtin`, `community.general` (existing from 001-bootstrap-incus-host)
**Storage**: N/A (Incus storage pools configured by 001-bootstrap-incus-host; optional host directory mounts for persistence)
**Testing**: Manual verification via playbook execution; idempotency test via re-run; SSH connectivity checks
**Target Platform**: Linux (OpenSUSE Tumbleweed primary; Ubuntu secondary) - localhost Incus host
**Project Type**: Infrastructure automation (Ansible playbooks and roles)
**Performance Goals**: Container provisioning and SSH availability within 3 minutes (per SC-001)
**Constraints**: Must be idempotent; must not disrupt existing containers; must reuse existing roles without modification
**Scale/Scope**: Single Incus host; 10+ containers (per SC-007); no clustering

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status**: PASS (no violations)

The constitution file (`.specify/memory/constitution.md`) contains template placeholders and no project-specific principles have been defined yet. This feature follows infrastructure automation best practices and aligns with patterns established in 001-bootstrap-incus-host:

- **Simplicity**: Ansible role pattern matching existing roles (docker, hetzner_server)
- **Idempotency**: All tasks designed for safe re-runs (core Ansible principle)
- **Consistency**: Workflow mirrors Hetzner provisioning (provision.yml → configure.yml → teardown.yml)
- **Reuse**: Existing configuration roles (docker, nodejs, user_setup) applied without modification
- **Test-First Spirit**: Acceptance scenarios defined in spec with measurable success criteria

No complexity justifications required.

### Post-Design Re-evaluation

**Status**: PASS (no new violations)

After Phase 1 design completion:
- Design maintains simplicity with only two new roles and three playbooks
- Data model uses standard Ansible variable patterns (consistent with 001-bootstrap-incus-host)
- Contracts document focused, minimal interfaces matching Hetzner workflow
- No over-engineering: shell commands for Incus operations (no custom modules), cloud-init for SSH (standard approach)
- Research confirmed role reuse without modification is achievable

No complexity justifications required.

## Project Structure

### Documentation (this feature)

```text
specs/002-incus-container-support/
├── spec.md              # Feature specification (input)
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
├── incus_container.yml           # New playbook: Container provisioning
├── incus_container_configure.yml # New playbook: Container configuration
├── incus_container_teardown.yml  # New playbook: Container destruction
├── inventory/
│   ├── hosts.yml                 # Existing: Add container entries dynamically
│   └── incus_containers.yml      # New: Static inventory for containers (optional)
├── roles/
│   ├── incus_container/          # New role: Container lifecycle + SSH access
│   │   ├── tasks/
│   │   │   ├── main.yml          # Core container operations
│   │   │   └── preflight.yml     # Pre-flight validation checks
│   │   ├── defaults/
│   │   │   └── main.yml          # Variables: image, profile, network, SSH
│   │   └── handlers/
│   │       └── main.yml          # (if needed)
│   ├── incus_container_teardown/ # New role: Container destruction
│   │   ├── tasks/
│   │   │   └── main.yml          # Stop, delete, cleanup operations
│   │   └── defaults/
│   │       └── main.yml          # Preservation options
│   ├── docker/                   # Existing: Reused for container configuration
│   ├── nodejs/                   # Existing: Reused for container configuration
│   ├── user_setup/               # Existing: Reused for container configuration
│   └── ...                       # Other existing roles
└── group_vars/
    └── incus_containers.yml      # New: Variables for container group
```

**Structure Decision**: Infrastructure automation project using existing Ansible structure in `ansible/` directory. Two new roles (`incus_container`, `incus_container_teardown`) handle container lifecycle and destruction. Three new playbooks parallel the Hetzner workflow: provision → configure → teardown. Existing configuration roles are reused without modification.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations detected. Design is minimal:
- Two new roles for container-specific operations
- Three new playbooks matching established patterns
- Reuse of existing configuration roles without modification
- Standard Ansible inventory patterns
