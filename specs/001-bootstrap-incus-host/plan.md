# Implementation Plan: Bootstrap Incus Host

**Branch**: `001-bootstrap-incus-host` | **Date**: 2025-12-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/001-bootstrap-incus-host/spec.md`

## Summary

Automate the setup of a local Linux host to run Incus/LXC containers via an Ansible role. The implementation installs Incus packages, configures system services, manages user permissions (incus-admin group), and initializes a default storage pool (directory-based) and NAT bridge network. Follows the existing remo Ansible role patterns, supports idempotent re-runs, and targets OpenSUSE Tumbleweed with extensibility for Ubuntu.

## Technical Context

**Language/Version**: Ansible 2.14+ / YAML
**Primary Dependencies**: `ansible.builtin`, `community.general` (for zypper module)
**Storage**: N/A (Incus storage pool uses directory-based backend on local filesystem)
**Testing**: Manual verification via `incus launch` after bootstrap; idempotency test via re-run
**Target Platform**: Linux (OpenSUSE Tumbleweed primary; Ubuntu secondary)
**Project Type**: Infrastructure automation (Ansible role)
**Performance Goals**: Bootstrap completion within 5 minutes on fresh system
**Constraints**: Must be idempotent; must not disrupt existing containers/pools; localhost execution
**Scale/Scope**: Single-host local workstation; no clustering

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Status**: PASS (no violations)

The constitution file (`.specify/memory/constitution.md`) contains template placeholders and no project-specific principles have been defined yet. This feature follows infrastructure automation best practices:

- **Simplicity**: Single Ansible role with minimal dependencies
- **Idempotency**: All tasks designed for safe re-runs (core Ansible principle)
- **Consistency**: Follows existing remo role patterns (docker, user_setup, zellij)
- **Test-First Spirit**: Acceptance scenarios defined in spec for manual verification

No complexity justifications required.

### Post-Design Re-evaluation

**Status**: PASS (no new violations)

After Phase 1 design completion:
- Design maintains single-role simplicity
- Data model uses standard Ansible variable patterns
- Contracts document minimal, focused interface
- No over-engineering detected (8 role variables, 3 core tasks)

## Project Structure

### Documentation (this feature)

```text
specs/001-bootstrap-incus-host/
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
├── incus_bootstrap.yml           # New playbook: localhost Incus bootstrap
├── inventory/
│   └── hosts.yml                 # Existing: add localhost entry if needed
├── roles/
│   └── incus_bootstrap/          # New role
│       ├── tasks/
│       │   └── main.yml          # Core installation and configuration
│       ├── defaults/
│       │   └── main.yml          # Variables: storage pool type, network name
│       └── handlers/
│           └── main.yml          # Service handlers (incusd restart)
└── requirements.yml              # Existing: verify community.general included
```

**Structure Decision**: Infrastructure automation project using Ansible role pattern. No traditional src/tests directories - follows existing remo Ansible structure in `ansible/` directory. Single new role (`incus_bootstrap`) with accompanying playbook for localhost execution.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations detected. Design is minimal:
- Single role with 3 task groups (install, service, init)
- 8 configuration variables with sensible defaults
- Standard Ansible patterns matching existing roles
