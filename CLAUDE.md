# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-06

## Constitution

See `.specify/memory/constitution.md` for project principles and non-negotiable standards.

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)
- Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (AWS, optional), hcloud (Hetzner, optional) (003-python-cli-rewrite)
- Flat file (`~/.config/remo/known_hosts`, colon-delimited) (003-python-cli-rewrite)

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
- 003-python-cli-rewrite: Added Python 3.11+ + Click (CLI framework), InquirerPy (interactive picker), boto3 (AWS, optional), hcloud (Hetzner, optional)
- 002-incus-container-support: Added Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local)

- 001-bootstrap-incus-host: Added Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
