# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-06

## Constitution

See `.specify/memory/constitution.md` for project principles and non-negotiable standards.

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)
- Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (AWS, optional), hcloud (Hetzner, optional) (003-python-cli-rewrite)
- Flat file (`~/.config/remo/known_hosts`, colon-delimited) (003-python-cli-rewrite)
- Cross-provider snapshot model (`models/snapshot.py`) + shared helpers in `core/snapshot.py` (name generator, validator, table formatter, destroy-time cleanup hook). No new runtime deps. (005-provider-snapshots)
- Python 3.11+ (package `requires-python = ">=3.11"`); service container runs Python 3.13-slim + Service (new `[notifier]` extra): FastAPI ≥0.115, uvicorn[standard] ≥0.32, pydantic ≥2.9, python-telegram-bot ≥21.6, structlog ≥24.4, tomli (py<3.11 only). CLI side: Click ≥8.1 (existing), no new laptop runtime deps. Build: hatchling (existing), uv (in-container). Ansible: new `community.docker` collection. (007-notifier-sidecar)
- None. All approval state is in-memory in a `PendingApprovals` registry; never persisted (FR-009). (007-notifier-sidecar)
- Python 3.11+ (`requires-python = ">=3.11"`); service container runs Python 3.13-slim (unchanged from 007). + Reorganized extras — `notifier-core` (FastAPI ≥0.115, uvicorn[standard] ≥0.32, pydantic ≥2.9, structlog ≥24.4, tomli on py<3.11); `notifier-telegram` = core + python-telegram-bot ≥21.6; `notifier` retained as an alias of `notifier-telegram` for back-compat. CLI/laptop side: Click ≥8.1, InquirerPy (existing) — **no new laptop runtime deps** (catalog is pure-Python metadata). Build: hatchling + uv (in-container). Ansible: `community.docker` (already added in 007). (008-notifier-channels)
- None. All approval and grant state remains in-memory (FR-009 / 007 carried forward). (008-notifier-channels)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)

## Project Structure

```text
src/remo/                  # Python CLI package (src layout, hatchling build)
├── __init__.py            # Version from importlib.metadata
├── __main__.py            # python -m remo entry point
├── cli/                   # Click command layer (parsing only, no business logic)
│   ├── main.py            # Root CLI group, command registration, passive update check
│   ├── shell.py           # remo shell
│   ├── cp.py              # remo cp
│   ├── init_cmd.py        # remo init
│   └── providers/         # Provider CLI groups
│       ├── incus.py       # remo incus {create,destroy,update,list,sync,bootstrap}
│       ├── hetzner.py     # remo hetzner {create,destroy,update,list,sync}
│       └── aws.py         # remo aws {create,destroy,update,list,sync,stop,start,reboot,info}
├── providers/             # Business logic (no Click imports)
│   ├── incus.py
│   ├── hetzner.py
│   └── aws.py
├── core/                  # Shared utilities (no provider knowledge)
│   ├── config.py          # REMO_HOME, paths
│   ├── output.py          # Colored output, confirm()
│   ├── validation.py      # Name, port, region, tool validation
│   ├── known_hosts.py     # Flat-file host registry
│   ├── ssh.py             # SSH options, terminal reset, timezone
│   ├── ansible_runner.py  # Ansible playbook subprocess
│   ├── picker.py          # InquirerPy fuzzy picker
│   ├── rsync.py           # File transfer
│   ├── version.py         # Version check, passive update notification
│   └── init.py            # remo init logic
└── models/
    └── host.py            # KnownHost dataclass

ansible/                   # Ansible playbooks (invoked by Python via subprocess)
├── roles/
│   └── incus_bootstrap/
├── incus_bootstrap.yml
└── requirements.yml

pyproject.toml             # Build config, dependencies, console_scripts entry point
```

## Ansible Standards (from Constitution)

### Variable Access - CRITICAL

**NEVER** access registered variable attributes directly. **ALWAYS** use `| default()` filters:

```yaml
# WRONG - will fail if task was skipped
when: my_result.rc == 0
msg: "{{ my_result.stdout }}"

# CORRECT - safe for skipped tasks
when: my_result.rc | default(1) == 0
msg: "{{ my_result.stdout | default('N/A') }}"
```

### Pre-Commit Checklist

Before committing Ansible code:

1. Grep for unsafe patterns: `grep -r '\.rc ==' ansible/` and `grep -r '\.stdout' ansible/`
2. Verify all matches use `| default()`
3. Test playbook on fresh system AND system with existing state
4. Update README if behavior changed

### Safe Task Registration Pattern

```yaml
- name: Check something
  ansible.builtin.command: some_command
  register: check_result
  changed_when: false
  failed_when: false
  when: some_condition

- name: Use the result safely
  ansible.builtin.debug:
    msg: "Result: {{ check_result.stdout | default('skipped') }}"
  when: check_result.stdout is defined
```

## Commands

```bash
# Development setup
uv sync --all-extras              # Install with all optional deps + dev tools
uv sync --extra aws               # Install with AWS (boto3) only
uv sync --extra hetzner           # Install with Hetzner (hcloud) only

# Verify installation
uv run remo --version
uv run remo --help

# Run tests
uv run pytest

# Type checking and linting
uv run mypy src/remo
uv run ruff check src/remo
```

## Architecture (Three-Layer)

- **cli/** → Click commands, argument parsing only. No business logic.
- **providers/** → Business logic. No Click imports. Called by cli layer.
- **core/** → Shared utilities. No provider knowledge. Used by both layers.

Provider SDKs (boto3, hcloud) are lazy-imported with clear error messages if missing.

## Code Style

- Python: Type hints, `from __future__ import annotations`, no docstrings on obvious methods
- Ansible 2.14+ / YAML: Follow standard conventions plus Constitution principles

## Recent Changes
- 008-notifier-channels: Split the notifier into a channel-agnostic core + per-channel packages with a catalog (`remo notifier deploy --channel`, `remo notifier channels`); re-pointed approvals to agentsh's real REST API (poll/resolve approver client); reorganized extras into `notifier-core` / `notifier-telegram` (+ `notifier` alias).
- 007-notifier-sidecar: Added Python 3.11+ (package `requires-python = ">=3.11"`); service container runs Python 3.13-slim + Service (new `[notifier]` extra): FastAPI ≥0.115, uvicorn[standard] ≥0.32, pydantic ≥2.9, python-telegram-bot ≥21.6, structlog ≥24.4, tomli (py<3.11 only). CLI side: Click ≥8.1 (existing), no new laptop runtime deps. Build: hatchling (existing), uv (in-container). Ansible: new `community.docker` collection.
- 005-provider-snapshots: Added cross-provider snapshot CLI (`remo <P> snapshot {create,list,restore,delete}`) + destroy-time cleanup hook across Incus / Proxmox / AWS / Hetzner.


<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
