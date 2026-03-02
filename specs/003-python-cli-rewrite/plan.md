# Implementation Plan: Python CLI Rewrite

**Branch**: `003-python-cli-rewrite` | **Date**: 2026-02-28 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/003-python-cli-rewrite/spec.md`

## Summary

Rewrite the 3,910-line bash `remo` CLI as a modular Python package using Click for command routing, InquirerPy for interactive selection (replacing fzf), and subprocess-based SSH/rsync/ansible-playbook invocation. The Ansible playbooks and roles remain unchanged. The Python CLI is pip-installable via `console_scripts` entry point and fully replaces the bash script.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Click (CLI framework), InquirerPy (interactive picker), boto3 (AWS, optional), hcloud (Hetzner, optional)
**Storage**: Flat file (`~/.config/remo/known_hosts`, colon-delimited)
**Testing**: pytest + pytest-mock, Click CliRunner for CLI integration tests
**Target Platform**: macOS, Linux (developer workstations)
**Project Type**: Single CLI package
**Build Backend**: Hatchling with `src/` layout
**Performance Goals**: N/A — CLI startup and runtime bounded by SSH/Ansible subprocess execution
**Constraints**: Must preserve exact CLI interface for backward compatibility
**Scale/Scope**: Single-user CLI tool, ~3,900 lines of bash → estimated ~2,500-3,000 lines of Python

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Defensive Variable Access (Ansible) | **N/A** | Ansible playbooks are unchanged. Python CLI does not modify Ansible code. |
| II. Test All Conditional Paths | **DEFERRED** | Test tasks omitted per user request. Constitution principle II is Ansible-specific ("For Ansible roles with `when:` conditions"); Ansible code is unchanged. Python testing should be added before production use. |
| III. Idempotent by Default | **PASS** | CLI operations are idempotent by nature (SSH connect, rsync, list, sync). Destructive operations (destroy) require confirmation per FR-020. |
| IV. Fail Fast with Clear Messages | **PASS** | FR-021 requires fail-fast on API errors. FR-015 requires colored error output. Edge cases specify clear error messages for all failure modes. |
| V. Documentation Reflects Reality | **PASS** | quickstart.md provides development setup. CLAUDE.md will be updated when the rewrite lands. |

**Gate result**: PASS — no violations.

## Project Structure

### Documentation (this feature)

```text
specs/003-python-cli-rewrite/
├── plan.md              # This file
├── research.md          # Technology decisions and rationale
├── data-model.md        # Entity definitions and relationships
├── quickstart.md        # Development setup guide
└── tasks.md             # Task breakdown (created by /speckit.tasks)
```

### Source Code (repository root)

```text
pyproject.toml                          # Package metadata, dependencies, entry point
src/
└── remo/
    ├── __init__.py                     # Package version (__version__)
    ├── __main__.py                     # Enables `python -m remo`
    │
    ├── cli/                            # Click command definitions (parsing + dispatch only)
    │   ├── __init__.py
    │   ├── main.py                     # Root Click group, --version, --help, passive update check
    │   ├── shell.py                    # `remo shell` command
    │   ├── cp.py                       # `remo cp` command
    │   ├── init_cmd.py                 # `remo init` command
    │   ├── self_update.py              # `remo self-update` command
    │   └── providers/                  # Provider subcommand groups
    │       ├── __init__.py
    │       ├── incus.py                # `remo incus {create,destroy,update,list,sync,bootstrap}`
    │       ├── hetzner.py              # `remo hetzner {create,destroy,update,list,sync}`
    │       └── aws.py                  # `remo aws {create,destroy,update,stop,start,reboot,info,list,sync}`
    │
    ├── providers/                      # Business logic per provider (no Click imports)
    │   ├── __init__.py
    │   ├── incus.py                    # Incus create/destroy/update/list/sync/bootstrap logic
    │   ├── hetzner.py                  # Hetzner logic (hcloud SDK)
    │   └── aws.py                      # AWS logic (boto3 SDK)
    │
    ├── core/                           # Shared utilities (no provider or CLI knowledge)
    │   ├── __init__.py
    │   ├── ssh.py                      # SSH option building, ProxyCommand, multiplexing, terminal reset
    │   ├── rsync.py                    # rsync wrapper for file transfer
    │   ├── ansible_runner.py           # run_playbook(), output filtering, venv detection
    │   ├── known_hosts.py              # save/remove/get/clear known hosts registry
    │   ├── output.py                   # print_error/success/info/warning, colored output
    │   ├── config.py                   # REMO_HOME, XDG paths, ansible dir resolution
    │   ├── version.py                  # Version comparison, GitHub API check, update cache
    │   ├── picker.py                   # Interactive selection (InquirerPy wrapper)
    │   └── validation.py               # Input validation (names, ports, regions, tools)
    │
    └── models/                         # Data classes
        ├── __init__.py
        └── host.py                     # KnownHost dataclass

tests/
├── conftest.py                         # Shared fixtures, tmp config dirs, subprocess mocking
├── unit/
│   ├── core/
│   │   ├── test_ssh.py
│   │   ├── test_rsync.py
│   │   ├── test_ansible_runner.py
│   │   ├── test_known_hosts.py
│   │   ├── test_config.py
│   │   ├── test_version.py
│   │   ├── test_picker.py
│   │   └── test_validation.py
│   ├── providers/
│   │   ├── test_incus.py
│   │   ├── test_hetzner.py
│   │   └── test_aws.py
│   └── cli/
│       ├── test_main.py
│       ├── test_shell.py
│       └── test_cp.py
└── integration/
    └── test_cli_smoke.py               # End-to-end CLI invocation tests

ansible/                                # Unchanged — not part of Python package
├── roles/
├── tasks/
├── *.yml
└── ...
```

**Structure Decision**: `src/` layout with three-layer separation (`cli/` → `providers/` → `core/`). The `cli/` layer handles Click command definitions and argument parsing only. The `providers/` layer contains business logic per provider with no CLI dependency. The `core/` layer provides shared utilities (SSH, rsync, config, output) with no provider or CLI knowledge. This enforces testability — provider logic can be unit-tested without Click, and core utilities are independently testable. The `ansible/` directory stays at the repo root, outside the Python package.

## Complexity Tracking

No constitution violations to justify.
