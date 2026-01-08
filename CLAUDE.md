# remo Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-01-06

## Constitution

See `.specify/memory/constitution.md` for project principles and non-negotiable standards.

## Active Technologies
- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local) (002-incus-container-support)
- N/A (Incus storage pools already configured by 001-bootstrap-incus-host) (002-incus-container-support)

- Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module) (001-bootstrap-incus-host)

## Project Structure

```text
ansible/
├── roles/
│   └── incus_bootstrap/
│       ├── defaults/main.yml
│       ├── handlers/main.yml
│       └── tasks/main.yml
├── incus_bootstrap.yml
└── requirements.yml

specs/
└── 001-bootstrap-incus-host/

.specify/
├── memory/
│   └── constitution.md
└── templates/
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
# Run Incus bootstrap on remote host
./run.sh incus_bootstrap.yml -i "<host>," -e "target_hosts=all ansible_user=<user>"

# Run Incus bootstrap on localhost
./run.sh incus_bootstrap.yml

# Verbose output
./run.sh incus_bootstrap.yml -e incus_bootstrap_verbosity=detailed
```

## Code Style

Ansible 2.14+ / YAML: Follow standard conventions plus Constitution principles

## Recent Changes
- 002-incus-container-support: Added Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (existing), Incus CLI (local)

- 001-bootstrap-incus-host: Added Ansible 2.14+ / YAML + `ansible.builtin`, `community.general` (for zypper module)
- 001-bootstrap-incus-host: Added macvlan networking as default (containers get LAN IPs)

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
