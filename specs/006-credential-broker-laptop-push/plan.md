# Implementation Plan: Credential Broker (Sidecar Devcontainer Model)

**Branch**: `006-credential-broker-laptop-push` | **Date**: 2026-05-31 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/006-credential-broker-laptop-push/spec.md`

## Summary

Add sidecar-managed credentials to remo without changing the laptop CLI surface. Provider create/update flows will provision the `remo-broker` daemon plus a reserved `_remo-vault` devcontainer on each instance; the sidecar stores encrypted-at-rest credentials, pushes plaintext snapshots over the local admin socket, and project devcontainers fetch only manifest-declared secrets at startup through a remo-managed secrets feature.

This repository owns the orchestration around that model: Ansible provisioning, remote shell/picker behavior, manifest and helper-script conventions, devcontainer startup flow, and user documentation. The sibling `remo-broker` repository owns the daemon-side v2 wire protocol, in-memory store, and audit events; this plan treats those as an explicit cross-repo contract dependency.

## Technical Context

**Language/Version**: Python 3.11+, Ansible Core 2.18+, Bash/Jinja2 remote host templates  
**Primary Dependencies**: Click 8.1+, InquirerPy, ansible-core, Docker CE + Compose plugin, `@devcontainers/cli`, systemd credentials on the LXC host, sibling `remo-broker` v2 binary/schema, and remote sidecar CLIs (`gh`, `aws`, `claude`, `fnox`)  
**Storage**: Version-controlled `~/projects/<project>/.remo/manifest.toml`; sidecar Docker volume at `/var/lib/remo-vault/fnox.enc`; host-side systemd credential material for the sidecar decryption key; broker secrets in memory only; broker audit log on host disk  
**Testing**: Existing `pytest` unit tests, `tests/unit/test_ansible_templates.py`, targeted provider/unit coverage for create-update-shell flows, plus manual end-to-end quickstart verification across one provider and the local `/workspaces/remo-broker` contract repo  
**Target Platform**: Linux local CLI provisioning Debian/Ubuntu-like remote instances and containers across AWS, Hetzner, Incus, and Proxmox, with Docker-based devcontainers on the remote host  
**Project Type**: Single project CLI/automation repository (`src/remo_cli/` + `ansible/` + `tests/`)  
**Performance Goals**: Fresh `push-creds` of a typical ~10-secret map completes in under 50 ms on the broker side; project startup retries required secrets for up to 15 seconds before failing closed; freshly provisioned project filesystems expose no useful credentials at rest before the user performs an in-container login  
**Constraints**: No new laptop CLI commands; `_remo-vault` is a reserved picker/project name; project manifests are bind-mounted read-only into devcontainers; sidecar-to-broker push is plaintext over a local Unix socket; broker protocol must align with `remo-broker` v2; no external secret service dependency; no plaintext secret persistence in broker disk or project container storage  
**Scale/Scope**: One sidecar per remo instance, one developer per instance, multiple project devcontainers per instance, roughly 10 managed secrets per instance, manifest cache defaults of 900 seconds / 50 entries carried from broker v1

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applicability | How this plan addresses it |
|---|---|---|
| I. Defensive Variable Access (Ansible) | High | New provisioning work is Ansible-heavy. All new registered variable attribute access in roles/playbooks must use `\| default(...)`, especially around Docker, systemd credential setup, and devcontainer bootstrap checks. |
| II. Test All Conditional Paths | High | The feature introduces meaningful branches: sidecar present/missing, secrets available/unavailable, env vs. file rendering, provider create vs. update, and destructive cleanup paths. Plan includes targeted unit/template coverage plus manual failure-path quickstart steps. |
| III. Idempotent by Default | High | Provider `update` must safely re-run sidecar/broker setup, helper script installation, and secrets feature staging without duplicate containers or drift. The sidecar and host assets are modeled as declarative Ansible state, not ad-hoc shell mutations. |
| IV. Fail Fast with Clear Messages | High | Required secrets fail closed after a 15-second bounded retry; manifest reload and broker/sidecar status helpers surface missing config explicitly; provisioning should stop on missing prerequisite binaries or invalid credential-key posture. |
| V. Documentation Reflects Reality | High | README/provider docs and feature quickstart are part of the design output because the user-facing workflow shifts materially even though the laptop CLI remains unchanged. |

**Pre-design gate result**: Pass. No constitutional violations are required for the planned design.

**Post-design re-check**: Pass. The Phase 1 artifacts keep the implementation within the existing Python + Ansible architecture, preserve idempotent provider workflows, and route new behavior through documented, testable surfaces.

## Project Structure

### Documentation (this feature)

```text
specs/006-credential-broker-laptop-push/
├── plan.md                 # This file
├── research.md             # Phase 0 decisions and integration rationale
├── data-model.md           # Phase 1 entities, relationships, validation rules
├── quickstart.md           # Phase 1 manual end-to-end verification flow
├── contracts/              # Phase 1 user-visible and cross-repo contracts
│   ├── cli-surface.md
│   ├── manifest-schema.md
│   └── broker-admin.md
└── tasks.md                # Phase 2 output (NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── shell.py                    # `remo shell`, direct project jump, help text
│   └── providers/
│       ├── aws.py                  # create/update wiring and user messaging
│       ├── hetzner.py
│       ├── incus.py
│       └── proxmox.py
├── core/
│   ├── ssh.py                      # project-launch invocation path
│   ├── output.py                   # user-facing errors/warnings for new flows
│   └── validation.py               # reserved-name and config validation helpers if needed
└── models/
    └── host.py                     # existing instance identity model

ansible/
├── *_site.yml / *_configure.yml    # provider configure flows gain sidecar/broker setup
├── roles/
│   ├── devcontainers/              # existing devcontainer CLI install
│   ├── docker/                     # Docker prerequisites for sidecar containers
│   ├── user_setup/                 # project picker, project-launch, shell startup hooks
│   ├── remo_broker/                # NEW: broker install/config/systemd assets
│   ├── vault_devcontainer/         # NEW: `_remo-vault` sidecar definition + helper scripts
│   └── remo_secrets_feature/       # NEW: project-side secrets feature files/templates
└── tasks/configure_dev_tools.yml   # shared role orchestration

tests/
├── unit/
│   ├── cli/
│   │   └── test_shell.py
│   ├── providers/
│   │   ├── test_aws_*.py
│   │   ├── test_hetzner_*.py
│   │   ├── test_incus_*.py
│   │   └── test_proxmox_*.py
│   └── test_ansible_templates.py   # remote script/template contract coverage
└── integration/                    # optional follow-on smoke coverage if existing harness expands

README.md
docs/aws.md
docs/hetzner.md
docs/incus.md
docs/proxmox.md
CLAUDE.md / AGENTS.md               # updated by agent-context script
```

**Structure Decision**: Stay within the existing single-project layout. The implementation is mostly Ansible/template work plus small Python CLI/help wiring, so new behavior should live in additive roles and existing provider/shell entrypoints rather than introducing a new top-level package or a second local CLI.

## Complexity Tracking

> No constitutional violations or exceptional complexity justifications are required for this design.
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
