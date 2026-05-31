# Tasks: Credential Broker (Sidecar Devcontainer Model)

**Input**: Design documents from `/specs/006-credential-broker-laptop-push/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`

**Tests**: Include targeted unit and template tests because the plan and contracts explicitly call for coverage of conditional paths, shell behavior, provider reconciliation, manifest rendering, and fail-closed startup behavior.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing. The source spec does not define explicit “User Story” headings, so these phases derive from the three primary user journeys in the feature narrative:

- **US1**: Provision and reconcile broker + `_remo-vault` on remo instances
- **US2**: Manage credentials and manifests through the `_remo-vault` shell experience
- **US3**: Start project devcontainers with manifest-gated secret vending and fail-closed behavior

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (`US1`, `US2`, `US3`)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish shared constants, fixtures, and artifact scaffolding used by later phases.

- [X] T001 Create broker/sidecar implementation tracker comments and shared constants in `src/remo_cli/core/validation.py` and `src/remo_cli/core/output.py`
- [X] T002 [P] Add fixture inputs for sidecar manifest, broker status, and helper-script rendering in `tests/unit/test_ansible_templates.py`
- [X] T003 [P] Add new Ansible role directories and placeholder README/task files under `ansible/roles/remo_broker/`, `ansible/roles/vault_devcontainer/`, and `ansible/roles/remo_secrets_feature/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before any user story can be implemented.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 Define shared broker + sidecar Ansible variables, defaults, and orchestration entrypoints in `ansible/tasks/configure_dev_tools.yml`
- [X] T005 [P] Implement the host-side `remo_broker` role for binary install, config, admin socket permissions, and systemd service wiring in `ansible/roles/remo_broker/tasks/main.yml`, `ansible/roles/remo_broker/templates/remo-broker.service.j2`, and `ansible/roles/remo_broker/templates/remo-broker.env.j2`
- [X] T006 [P] Implement the `_remo-vault` sidecar provisioning role with persistent volume, Docker secret/key mounting, devcontainer definition, and watcher service in `ansible/roles/vault_devcontainer/tasks/main.yml`, `ansible/roles/vault_devcontainer/templates/devcontainer.json.j2`, `ansible/roles/vault_devcontainer/templates/docker-compose.yml.j2`, and `ansible/roles/vault_devcontainer/templates/remo-vault-watcher.sh.j2`
- [X] T007 [P] Implement the project-side secrets feature assets in `ansible/roles/remo_secrets_feature/tasks/main.yml`, `ansible/roles/remo_secrets_feature/templates/feature-devcontainer.json.j2`, `ansible/roles/remo_secrets_feature/templates/remo-fetch-secrets.sh.j2`, and `ansible/roles/remo_secrets_feature/templates/manifest.schema.toml`
- [X] T008 Add safe registered-variable handling and failure messaging for the new roles in `ansible/roles/remo_broker/tasks/main.yml`, `ansible/roles/vault_devcontainer/tasks/main.yml`, and `ansible/roles/remo_secrets_feature/tasks/main.yml`
- [X] T009 Add template/unit coverage for foundational broker, sidecar, and secrets-feature assets in `tests/unit/test_ansible_templates.py`

**Checkpoint**: Foundational Ansible roles and shared assets exist, are idempotent on paper, and have baseline test coverage.

---

## Phase 3: User Story 1 - Provision and reconcile broker + `_remo-vault` (Priority: P1) 🎯 MVP

**Goal**: A developer can run existing provider `create`/`update` flows and end up with a working `remo-broker` host service plus a managed `_remo-vault` sidecar on the instance, without any new laptop CLI commands.

**Independent Test**: Run `remo <provider> create` or `update` against a test instance and verify the host has a running broker service, `_remo-vault` exists in the remote project list, and no new local flags or commands are required.

### Tests for User Story 1

- [X] T010 [P] [US1] Add provider orchestration tests for broker/sidecar provisioning hooks in `tests/unit/cli/providers/test_aws_snapshot.py`, `tests/unit/cli/providers/test_hetzner_snapshot.py`, `tests/unit/cli/providers/test_incus_snapshot.py`, and `tests/unit/cli/providers/test_proxmox_snapshot.py`
- [X] T011 [P] [US1] Add provider business-logic tests for create/update reconciliation messaging in `tests/unit/providers/test_aws_snapshot.py`, `tests/unit/providers/test_hetzner_snapshot.py`, `tests/unit/providers/test_incus_snapshot.py`, and `tests/unit/providers/test_proxmox_snapshot.py`

### Implementation for User Story 1

- [X] T012 [US1] Wire the new Ansible roles into shared provider configure flows in `ansible/aws_configure.yml`, `ansible/hetzner_configure.yml`, `ansible/incus_configure.yml`, `ansible/proxmox_configure.yml`, and `ansible/tasks/configure_dev_tools.yml`
- [X] T013 [US1] Update provider create/update business logic to surface broker/sidecar reconciliation steps in `src/remo_cli/providers/aws.py`, `src/remo_cli/providers/hetzner.py`, `src/remo_cli/providers/incus.py`, and `src/remo_cli/providers/proxmox.py`
- [X] T014 [P] [US1] Update provider CLI help and pass-through messaging for unchanged laptop commands in `src/remo_cli/cli/providers/aws.py`, `src/remo_cli/cli/providers/hetzner.py`, `src/remo_cli/cli/providers/incus.py`, and `src/remo_cli/cli/providers/proxmox.py`
- [X] T015 [US1] Extend destroy-time cleanup expectations for the managed sidecar in `src/remo_cli/providers/aws.py`, `src/remo_cli/providers/hetzner.py`, `src/remo_cli/providers/incus.py`, and `src/remo_cli/providers/proxmox.py`
- [X] T016 [US1] Add or update provisioning documentation for broker + sidecar lifecycle in `README.md`, `docs/aws.md`, `docs/hetzner.md`, `docs/incus.md`, and `docs/proxmox.md`

**Checkpoint**: Existing provider flows provision, update, and tear down the broker + sidecar model end-to-end.

---

## Phase 4: User Story 2 - Use `_remo-vault` to manage credentials and manifests (Priority: P2)

**Goal**: A developer can reach `_remo-vault` from `remo shell`, understand that it is a managed sidecar, inspect stored credentials safely, and trigger project manifest reload/testing through helper scripts.

**Independent Test**: From `remo shell`, verify `_remo-vault` appears as a reserved picker entry, `remo shell -p _remo-vault` lands in the sidecar, and helper commands report metadata/status without exposing secret values.

### Tests for User Story 2

- [X] T017 [P] [US2] Add shell picker and direct-jump tests for `_remo-vault` in `tests/unit/cli/test_shell.py`
- [X] T018 [P] [US2] Add template tests for `_remo-vault` menu/launch behavior and helper script rendering in `tests/unit/test_ansible_templates.py`

### Implementation for User Story 2

- [X] T019 [US2] Implement reserved `_remo-vault` picker and direct-project-shell behavior in `src/remo_cli/cli/shell.py`, `src/remo_cli/core/ssh.py`, and `src/remo_cli/core/validation.py`
- [X] T020 [P] [US2] Update remote picker and launcher templates for `_remo-vault` handling in `ansible/roles/user_setup/templates/project-menu.sh.j2`, `ansible/roles/user_setup/templates/project-launch.sh.j2`, and `ansible/roles/user_setup/templates/devshell.sh.j2`
- [X] T021 [US2] Install `_remo-vault` helper commands, MOTD text, and reload/test/status wrappers in `ansible/roles/vault_devcontainer/templates/remo-list-creds.sh.j2`, `ansible/roles/vault_devcontainer/templates/remo-test-project.sh.j2`, `ansible/roles/vault_devcontainer/templates/remo-vend-status.sh.j2`, `ansible/roles/vault_devcontainer/templates/remo-reload.sh.j2`, and `ansible/roles/vault_devcontainer/templates/motd.j2`
- [X] T022 [US2] Mount the canonical `.remo/manifest.toml` read-only and support host-side reload semantics in `ansible/roles/user_setup/tasks/main.yml` and `ansible/roles/vault_devcontainer/tasks/main.yml`
- [X] T023 [US2] Document sidecar login, manifest editing, and helper-command workflows in `README.md`, `docs/remo-fnox-spec.md`, and `specs/006-credential-broker-laptop-push/quickstart.md`

**Checkpoint**: `_remo-vault` is a first-class managed shell target and the user can safely manage credential state and manifest reloads through it.

---

## Phase 5: User Story 3 - Start project devcontainers with manifest-gated secret vending (Priority: P3)

**Goal**: A project devcontainer can start with manifest-declared secrets rendered as env vars or tmpfs files, using broker-backed vending, structured bundle templates, cache invalidation on push, and fail-closed startup when required secrets are missing.

**Independent Test**: Start a project devcontainer with a valid manifest and confirm env/file rendering works; then reference a missing required secret and confirm startup retries for 15 seconds and exits non-zero without handing off to the user command.

### Tests for User Story 3

- [X] T024 [P] [US3] Add manifest parsing and render-mode tests for the secrets feature in `tests/unit/test_ansible_templates.py`
- [X] T025 [P] [US3] Add shell/startup behavior tests for project launch with secret vending in `tests/unit/cli/test_shell.py`

### Implementation for User Story 3

- [X] T026 [US3] Implement `remo-fetch-secrets` manifest parsing, env export, tmpfs file rendering, structured bundle placeholders, and 15-second fail-closed retry behavior in `ansible/roles/remo_secrets_feature/templates/remo-fetch-secrets.sh.j2`
- [X] T027 [P] [US3] Wire the secrets feature into remote project startup and devcontainer bootstrap in `ansible/roles/user_setup/tasks/main.yml`, `ansible/roles/devcontainers/tasks/main.yml`, and `ansible/roles/user_setup/templates/project-launch.sh.j2`
- [X] T028 [P] [US3] Add manifest schema/install assets and read-only bind-mount behavior in `ansible/roles/remo_secrets_feature/tasks/main.yml`, `ansible/roles/remo_secrets_feature/templates/feature-devcontainer.json.j2`, and `ansible/roles/vault_devcontainer/templates/remo-reload.sh.j2`
- [X] T029 [US3] Align broker-side contract expectations and status/error handling in this repo with v2 admin semantics in `ansible/roles/vault_devcontainer/templates/remo-vault-watcher.sh.j2`, `ansible/roles/vault_devcontainer/templates/remo-vend-status.sh.j2`, and `specs/006-credential-broker-laptop-push/contracts/broker-admin.md`
- [X] T030 [US3] Document manifest schema, env/file usage, fail-closed startup, and validation steps in `README.md`, `docs/remo-fnox-spec.md`, and `specs/006-credential-broker-laptop-push/contracts/manifest-schema.md`

**Checkpoint**: Project devcontainers receive only manifest-allowed secrets at startup and fail closed when required secrets cannot be vended.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Close gaps that affect multiple user stories and validate the end-to-end experience.

- [X] T031 [P] Update agent-facing repository guidance for the new sidecar/broker architecture in `CLAUDE.md` and `AGENTS.md`
- [X] T032 Harden cross-role failure handling, audit-log expectations, and protocol-version checks in `ansible/roles/remo_broker/tasks/main.yml`, `ansible/roles/vault_devcontainer/templates/remo-vend-status.sh.j2`, and `ansible/roles/vault_devcontainer/templates/remo-test-project.sh.j2`
- [X] T033 [P] Run and document the quickstart validation flow for one provider in `specs/006-credential-broker-laptop-push/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks all user story work.
- **User Stories (Phases 3–5)**: Depend on Foundational completion.
- **Polish (Phase 6)**: Depends on the desired user stories being complete.

### User Story Dependencies

- **US1 (P1)**: Starts after Phase 2 and delivers the MVP infrastructure.
- **US2 (P2)**: Starts after Phase 2 and depends on the `_remo-vault` sidecar existing from US1 in integrated testing, but its shell/picker work can be developed in parallel.
- **US3 (P3)**: Starts after Phase 2 and depends on the secrets feature plus broker/sidecar contracts from US1; it can proceed in parallel with most of US2.

### Within Each User Story

- Tests should be written before or alongside implementation for the touched surfaces.
- Ansible role/templates should be in place before provider/shell integration depends on them.
- Shell/CLI integration should land before docs that instruct users to rely on it.
- Each story should be validated independently using the story checkpoint before moving on.

### Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel.
- **Phase 2**: T005, T006, and T007 can run in parallel once T004 defines shared orchestration.
- **US1**: T010 and T011 can run in parallel; T014 can proceed alongside T013 once provider wiring is understood.
- **US2**: T017 and T018 can run in parallel; T020 and T021 can also run in parallel after T019 defines shell behavior.
- **US3**: T024 and T025 can run in parallel; T027 and T028 can run in parallel after T026 fixes feature behavior.
- **Polish**: T031 and T033 can run in parallel.

---

## Parallel Example: User Story 1

```bash
# Tests
Task: "Add provider orchestration tests in tests/unit/cli/providers/test_aws_snapshot.py, tests/unit/cli/providers/test_hetzner_snapshot.py, tests/unit/cli/providers/test_incus_snapshot.py, and tests/unit/cli/providers/test_proxmox_snapshot.py"
Task: "Add provider business-logic tests in tests/unit/providers/test_aws_snapshot.py, tests/unit/providers/test_hetzner_snapshot.py, tests/unit/providers/test_incus_snapshot.py, and tests/unit/providers/test_proxmox_snapshot.py"

# Implementation
Task: "Update provider CLI help in src/remo_cli/cli/providers/aws.py, src/remo_cli/cli/providers/hetzner.py, src/remo_cli/cli/providers/incus.py, and src/remo_cli/cli/providers/proxmox.py"
Task: "Wire provider business logic in src/remo_cli/providers/aws.py, src/remo_cli/providers/hetzner.py, src/remo_cli/providers/incus.py, and src/remo_cli/providers/proxmox.py"
```

## Parallel Example: User Story 2

```bash
# Tests
Task: "Add _remo-vault shell tests in tests/unit/cli/test_shell.py"
Task: "Add _remo-vault template tests in tests/unit/test_ansible_templates.py"

# Implementation
Task: "Update project picker templates in ansible/roles/user_setup/templates/project-menu.sh.j2, ansible/roles/user_setup/templates/project-launch.sh.j2, and ansible/roles/user_setup/templates/devshell.sh.j2"
Task: "Add helper command templates in ansible/roles/vault_devcontainer/templates/remo-list-creds.sh.j2, ansible/roles/vault_devcontainer/templates/remo-test-project.sh.j2, ansible/roles/vault_devcontainer/templates/remo-vend-status.sh.j2, and ansible/roles/vault_devcontainer/templates/remo-reload.sh.j2"
```

## Parallel Example: User Story 3

```bash
# Tests
Task: "Add manifest/render tests in tests/unit/test_ansible_templates.py"
Task: "Add project startup tests in tests/unit/cli/test_shell.py"

# Implementation
Task: "Wire remote project startup in ansible/roles/user_setup/tasks/main.yml, ansible/roles/devcontainers/tasks/main.yml, and ansible/roles/user_setup/templates/project-launch.sh.j2"
Task: "Add manifest schema/install assets in ansible/roles/remo_secrets_feature/tasks/main.yml and ansible/roles/remo_secrets_feature/templates/feature-devcontainer.json.j2"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational.
3. Complete Phase 3: US1.
4. Validate provider create/update/destroy behavior on one provider.
5. Stop for review before layering shell UX and project secret vending.

### Incremental Delivery

1. Finish Setup + Foundational to establish the shared broker/sidecar/secrets-feature primitives.
2. Deliver **US1** to make infrastructure provisioning real.
3. Deliver **US2** so the sidecar is usable by humans.
4. Deliver **US3** to complete the project-devcontainer vending flow.
5. Finish with polish, docs, and quickstart validation.

### Parallel Team Strategy

1. One engineer owns foundational Ansible roles (Phase 2).
2. After Phase 2:
   - Engineer A: US1 provider integration
   - Engineer B: US2 shell/picker/helper flows
   - Engineer C: US3 secrets feature and startup behavior
3. Rejoin for Phase 6 cross-cutting hardening and documentation.

---

## Notes

- All tasks follow the required checklist format: checkbox, task ID, optional `[P]`, required `[US#]` for story tasks, and exact file paths.
- Story phases are inferred from the spec’s primary user journeys because `spec.md` does not contain explicit “User Story” headings.
- The MVP scope is **US1 only**: provisioning and reconciling the broker + sidecar with existing provider commands.
- Keep `remo-broker` protocol details aligned with the sibling repo at `/workspaces/remo-broker` while implementing this repository’s orchestration changes.
