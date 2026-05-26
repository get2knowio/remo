# Contract: Ansible Changes

Date: 2026-05-25
Branch: 005-credential-broker

Required Ansible diff against current `main`. Each item is a discrete, reviewable change.

## `ansible/group_vars/all.yml` (modified)

Replace every `lookup('env', '<TOKEN>')` with `lookup('pipe', 'fnox get <name>')` (FR-006). Concrete targets (full list compiled by greping during the tasks phase, but the known ones today):

```yaml
# BEFORE
hetzner_api_token: "{{ lookup('env', 'HETZNER_API_TOKEN') }}"
aws_access_key:    "{{ lookup('env', 'AWS_ACCESS_KEY_ID') }}"
aws_secret_key:    "{{ lookup('env', 'AWS_SECRET_ACCESS_KEY') }}"

# AFTER
hetzner_api_token: "{{ lookup('pipe', 'fnox get hetzner_api_token') }}"
aws_access_key:    "{{ lookup('pipe', 'fnox get aws_access_key_id') }}"
aws_secret_key:    "{{ lookup('pipe', 'fnox get aws_secret_access_key') }}"
```

Constitution Principle I applies to any lookup-result handling downstream; greps in pre-commit catch unsafe access.

## `ansible/roles/broker_install/` (new)

Installs the broker binary and systemd unit on the instance.

```text
ansible/roles/broker_install/
├── defaults/main.yml         # broker_version, broker_url_template, broker_sha256_template
├── tasks/main.yml            # download → verify sha256 → install to /usr/local/bin → render unit → enable
├── handlers/main.yml         # restart remo-broker
└── templates/
    └── remo-broker.service.j2
```

Key behaviors:
- Pinned `broker_version` per Remo release; overridable via `-e broker_version=...`.
- Download via `ansible.builtin.get_url`; SHA-256 verified (failure mode: "broker download integrity check failed for v<X> on <arch> — see https://github.com/get2knowio/remo-broker/releases").
- Idempotent: skips download if `/usr/local/bin/remo-broker --version` already reports the pinned version.
- Service unit reads `/etc/remo-broker/bootstrap-token` via `LoadCredential=` so the broker process never sees the path directly — supports future TPM2 sealing without unit-file changes.
- Creates `/run/remo-broker/` via `tmpfiles.d` (cleared on reboot) and `/var/log/remo-broker/` (persistent, mode 0700 root).

## `ansible/roles/bootstrap_token_{imds,file,mount}/` (new)

Three sibling roles asserting the transport-level state per provider (see bootstrap-delivery.md). Pure assertion + diagnostic — actual delivery is performed by the Python `providers/<name>.py` flow.

## `ansible/{incus,proxmox,hetzner,aws}_configure.yml` (modified)

Each existing configure playbook gains the new roles in `roles:`:

```yaml
# hetzner_configure.yml (example)
roles:
  - { role: broker_install }
  - { role: bootstrap_token_file }   # asserts /etc/remo-broker/bootstrap-token is present + mode 0400
  # ... existing roles unchanged ...
```

Order matters: `broker_install` runs first so the systemd unit and directory layout exist when `bootstrap_token_*` asserts on them.

## `ansible/roles/incus_bootstrap/` (modified)

The existing role (currently the node-side Incus install) gets a new sub-task block under a `when: registered_developers | length > 0` guard that installs `/usr/local/libexec/remo-broker-tokens` (the per-developer token-manager helper). Per-developer subdirectory creation happens at `remo incus add-node` time via SSH from the laptop, not during initial node bootstrap.

## Variable safety

Every `register:` introduced by these new roles MUST follow Constitution Principle I:

```yaml
- name: Check broker version
  ansible.builtin.command: /usr/local/bin/remo-broker --version
  register: broker_version_check
  changed_when: false
  failed_when: false

- name: Skip download if version matches
  ansible.builtin.set_fact:
    skip_broker_download: "{{ (broker_version_check.stdout | default('')) == pinned_broker_version }}"
```

Pre-commit grep gate (per CLAUDE.md) will catch any drift.
