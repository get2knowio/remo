# Tasks: Incus Container Support

**Input**: Design documents from `/specs/002-incus-container-support/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/

**Tests**: Manual verification via playbook execution as specified in plan.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- **Ansible project**: `ansible/` at repository root
- Roles in `ansible/roles/`
- Playbooks in `ansible/`
- Inventory in `ansible/inventory/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create directory structure and base files for Incus container roles

- [x] T001 Create incus_container role directory structure at ansible/roles/incus_container/
- [x] T002 [P] Create incus_container_teardown role directory structure at ansible/roles/incus_container_teardown/
- [x] T003 [P] Create group_vars file for incus containers at ansible/group_vars/incus_containers.yml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core role defaults and validation that ALL user stories depend on

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T004 Create defaults/main.yml for incus_container role with container identity, SSH, network, and storage variables at ansible/roles/incus_container/defaults/main.yml
- [x] T005 [P] Create defaults/main.yml for incus_container_teardown role with destruction options at ansible/roles/incus_container_teardown/defaults/main.yml
- [x] T006 Implement pre-flight validation tasks (Incus daemon, image access, storage pool, network, SSH key) in ansible/roles/incus_container/tasks/preflight.yml
- [x] T007 [P] Create static inventory file for incus_containers group at ansible/inventory/incus_containers.yml

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - Create Local Development Container (Priority: P1) MVP

**Goal**: Spin up an Incus container with SSH access using same workflow as Hetzner VMs

**Independent Test**: Run `./run.sh incus_container.yml -e container_name=test-us1` and verify SSH connectivity via `ssh remo@<container_ip>`

### Implementation for User Story 1

- [x] T008 [US1] Implement container existence check task in ansible/roles/incus_container/tasks/main.yml
- [x] T009 [US1] Implement container creation task using `incus init` with cloud-enabled image in ansible/roles/incus_container/tasks/main.yml
- [x] T010 [US1] Implement cloud-init configuration task for SSH key injection in ansible/roles/incus_container/tasks/main.yml
- [x] T011 [US1] Implement container start task in ansible/roles/incus_container/tasks/main.yml
- [x] T012 [US1] Implement IP address discovery task with retry logic in ansible/roles/incus_container/tasks/main.yml
- [x] T013 [US1] Implement SSH availability wait task using wait_for module in ansible/roles/incus_container/tasks/main.yml
- [x] T014 [US1] Implement dynamic inventory registration using add_host in ansible/roles/incus_container/tasks/main.yml
- [x] T015 [US1] Set output facts (incus_container_ip, incus_container_created, incus_container_exists) in ansible/roles/incus_container/tasks/main.yml
- [x] T016 [US1] Create incus_container.yml playbook that invokes the incus_container role at ansible/incus_container.yml
- [x] T017 [US1] Add success message output to incus_container.yml playbook with connection instructions

**Checkpoint**: User Story 1 complete - can create containers with SSH access within 3 minutes

---

## Phase 4: User Story 2 - Configure Container Using Existing Roles (Priority: P2)

**Goal**: Apply existing Ansible configuration roles (docker, nodejs, user_setup) to Incus containers

**Independent Test**: Run `./run.sh incus_container_configure.yml -e container_name=test-us2` after provisioning and verify Docker/Node.js are installed via SSH

### Implementation for User Story 2

- [x] T018 [US2] Create incus_container_configure.yml playbook at ansible/incus_container_configure.yml
- [x] T019 [US2] Add pre-tasks for container inventory verification and apt readiness in ansible/incus_container_configure.yml
- [x] T020 [US2] Configure playbook to apply docker role with become: true in ansible/incus_container_configure.yml
- [x] T021 [US2] Configure playbook to apply user_setup role in ansible/incus_container_configure.yml
- [x] T022 [US2] Configure playbook to apply nodejs role in ansible/incus_container_configure.yml
- [x] T023 [US2] Configure playbook to apply fzf and zellij roles in ansible/incus_container_configure.yml
- [x] T024 [US2] Add success message output with installed tools summary in ansible/incus_container_configure.yml

**Checkpoint**: User Story 2 complete - existing roles apply to containers without modification

---

## Phase 5: User Story 3 - Manage Container Inventory (Priority: P2)

**Goal**: Incus containers tracked in Ansible inventory alongside Hetzner hosts

**Independent Test**: After provisioning, run `ansible-inventory --list` and verify container appears in incus_containers group with correct connection parameters

### Implementation for User Story 3

- [x] T025 [US3] Configure group variables for incus_containers with connection defaults in ansible/group_vars/incus_containers.yml
- [x] T026 [US3] Update static inventory template with example container entry in ansible/inventory/incus_containers.yml
- [x] T027 [US3] Ensure add_host task in incus_container role adds to incus_containers group with ansible_python_interpreter in ansible/roles/incus_container/tasks/main.yml

**Checkpoint**: User Story 3 complete - containers manageable via inventory patterns

---

## Phase 6: User Story 4 - Destroy Container with Data Preservation (Priority: P3)

**Goal**: Destroy Incus containers when no longer needed, with option to preserve persistent data

**Independent Test**: Create container with mount, destroy with `preserve_data=true`, verify host directory remains; then destroy with `preserve_data=false`, verify directory removed

### Implementation for User Story 4

- [x] T028 [US4] Implement container existence check in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T029 [US4] Implement teardown warning display task in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T030 [US4] Implement user confirmation pause task (skippable via auto_confirm) in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T031 [US4] Implement container stop task in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T032 [US4] Implement container deletion task with force option in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T033 [US4] Implement mount directory cleanup task (conditional on preserve_data) in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T034 [US4] Set output facts (incus_container_destroyed, incus_container_data_preserved) in ansible/roles/incus_container_teardown/tasks/main.yml
- [x] T035 [US4] Create incus_container_teardown.yml playbook at ansible/incus_container_teardown.yml
- [x] T036 [US4] Add success message output showing preservation status in ansible/incus_container_teardown.yml

**Checkpoint**: User Story 4 complete - container lifecycle fully manageable

---

## Phase 7: User Story 5 - Container Networking Access (Priority: P3)

**Goal**: Access services running inside containers from workstation via LAN IP (macvlan)

**Independent Test**: Start web server in container, access via container's LAN IP from a separate workstation

### Implementation for User Story 5

- [x] T037 [US5] Verify incusbr0 macvlan network exists in preflight checks at ansible/roles/incus_container/tasks/preflight.yml
- [x] T038 [US5] Add network attachment verification after container start in ansible/roles/incus_container/tasks/main.yml
- [x] T039 [US5] Include container LAN IP in playbook success output with macvlan access note in ansible/incus_container.yml

**Checkpoint**: User Story 5 complete - containers accessible from workstation via LAN IP

---

## Phase 8: Persistent Storage (Cross-Cutting)

**Purpose**: Enable host directory mounts for data persistence across container destruction

- [x] T040 Implement mount source directory creation task (creates if not exists) in ansible/roles/incus_container/tasks/main.yml
- [x] T041 Implement disk device addition task using incus config device add with shift=true in ansible/roles/incus_container/tasks/main.yml
- [x] T042 Add mount device idempotency check (skip if device already exists) in ansible/roles/incus_container/tasks/main.yml

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [x] T043 Add handlers/main.yml for incus_container role (if needed) at ansible/roles/incus_container/handlers/main.yml
- [x] T044 Update ansible/README.md with container provisioning commands per Constitution Principle V
- [x] T045 Verify idempotency by running provision playbook twice on same container
- [x] T046 Run quickstart.md validation - execute all documented workflows
- [x] T047 Test edge case: SSH connection timeout handling and error message
- [x] T048 Test edge case: Container with conflicting name already exists
- [x] T049 Test edge case: Image not available in configured remotes
- [x] T050 Test multi-container workflow: provision 10+ containers and verify same commands work without complexity increase (SC-007)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
  - US1 (Phase 3): No dependencies on other stories - MVP
  - US2 (Phase 4): Requires US1 to create containers for configuration
  - US3 (Phase 5): Requires US1 for inventory population
  - US4 (Phase 6): Requires US1 for containers to destroy
  - US5 (Phase 7): Requires US1 for running containers
- **Persistent Storage (Phase 8)**: Depends on US1, enhances US4
- **Polish (Phase 9)**: Depends on all user stories being complete

### User Story Dependencies

```
US1 (Create Container) ──┬──> US2 (Configure Container)
                         │
                         ├──> US3 (Inventory Management)
                         │
                         ├──> US4 (Destroy Container)
                         │
                         └──> US5 (Networking Access)
```

### Within Each User Story

- Pre-flight checks before operations
- Container creation before configuration
- IP discovery before SSH wait
- SSH connectivity before inventory registration
- Story complete before moving to next priority

### Parallel Opportunities

- T001, T002, T003 (Setup) can run in parallel
- T004, T005, T006, T007 (Foundational) - T005 and T007 can run in parallel
- Different user stories can be worked on in parallel after Foundational completion

---

## Parallel Example: Setup Phase

```bash
# Launch all setup tasks together:
Task: "Create incus_container role directory structure at ansible/roles/incus_container/"
Task: "Create incus_container_teardown role directory structure at ansible/roles/incus_container_teardown/"
Task: "Create group_vars file for incus containers at ansible/group_vars/incus_containers.yml"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: User Story 1
4. **STOP and VALIDATE**: Run `./run.sh incus_container.yml -e container_name=test` and SSH in
5. Deploy/demo if ready - containers can now be created with SSH access

### Incremental Delivery

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → MVP! Containers can be created
3. Add User Story 2 → Test independently → Containers can be configured
4. Add User Story 3 → Test independently → Inventory integration complete
5. Add User Story 4 → Test independently → Full lifecycle management
6. Add User Story 5 → Test independently → Network access confirmed
7. Each story adds value without breaking previous stories

### Suggested MVP Scope

- **MVP**: Phase 1, 2, and 3 (User Story 1 only)
- This provides: Container creation with SSH access in under 3 minutes
- Meets success criteria SC-001: "Users can provision a new Incus container and SSH into it within 3 minutes"

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All registered variables must use `| default()` filters per Constitution
- Test playbooks on fresh system AND system with existing state
- Avoid: vague tasks, same file conflicts, cross-story dependencies that break independence
