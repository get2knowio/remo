# Tasks: Bootstrap Incus Host

**Input**: Design documents from `/specs/001-bootstrap-incus-host/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Not requested - manual verification via `incus launch` after bootstrap per spec.md

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

This is an Ansible infrastructure project with the following structure:
- Playbooks: `ansible/`
- Roles: `ansible/roles/`
- Inventory: `ansible/inventory/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and Ansible role structure

- [x] T001 Create Ansible role directory structure at ansible/roles/incus_bootstrap/
- [x] T002 [P] Create role defaults file at ansible/roles/incus_bootstrap/defaults/main.yml
- [x] T003 [P] Create empty handlers file at ansible/roles/incus_bootstrap/handlers/main.yml
- [x] T004 Verify community.general collection in ansible/requirements.yml

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**Critical**: The role structure and defaults must exist before implementing user story tasks.

- [ ] T005 Add localhost connection entry to ansible/inventory/hosts.yml (if not present)
- [ ] T005a Add pre-flight check for sudo privileges with clear fail message in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T005b Add pre-flight check for minimum disk space (10GB) with warning in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T005c Add pre-flight check for required kernel modules (overlay) with warning in ansible/roles/incus_bootstrap/tasks/main.yml

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 1 - First-Time Incus Setup (Priority: P1) MVP

**Goal**: Transform a fresh OpenSUSE Tumbleweed system into a working Incus host with minimal effort

**Independent Test**: Run bootstrap on fresh OpenSUSE Tumbleweed, then execute `incus launch images:alpine/edge test-container` within 60 seconds

### Implementation for User Story 1

- [ ] T006 [US1] Implement package installation task (zypper) in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T007 [US1] Implement incus.socket service enablement in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T008 [US1] Implement incus-user.socket service enablement in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T009 [US1] Implement user group addition (incus-admin) in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T010 [US1] Implement Incus initialization (incus admin init --minimal) in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T011 [US1] Create main playbook at ansible/incus_bootstrap.yml targeting localhost

**Checkpoint**: At this point, User Story 1 should be fully functional - a fresh OpenSUSE system can be bootstrapped and launch containers

---

## Phase 4: User Story 2 - Idempotent Re-Runs (Priority: P2)

**Goal**: Bootstrap completes without errors on systems with existing Incus configuration, preserving containers and settings

**Independent Test**: Run bootstrap twice consecutively - both runs succeed, existing containers continue running

### Implementation for User Story 2

- [ ] T012 [US2] Add idempotency check for storage pool existence before initialization in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T013 [US2] Add conditional for user group membership check in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T014 [US2] Ensure all tasks use state: present pattern (not state: latest) in ansible/roles/incus_bootstrap/tasks/main.yml

> **Note**: Network idempotency is handled by T012's storage pool check - `incus admin init --minimal` creates both storage and network atomically, so skipping init preserves both.

**Checkpoint**: At this point, User Stories 1 AND 2 should both work - fresh install and re-runs are safe

---

## Phase 5: User Story 3 - Integration with Remo Bootstrap (Priority: P2)

**Goal**: Bootstrap follows remo run.sh patterns for consistency with other playbooks

**Independent Test**: Execute `./run.sh incus_bootstrap.yml` from repo root and verify it targets localhost correctly

### Implementation for User Story 3

- [ ] T015 [US3] Verify playbook follows existing remo playbook patterns (compare with other playbooks in ansible/)
- [ ] T016 [US3] Add playbook header documentation matching remo style in ansible/incus_bootstrap.yml
- [ ] T017 [US3] Test playbook execution via ./run.sh incus_bootstrap.yml

**Checkpoint**: At this point, User Stories 1, 2, AND 3 work - bootstrap integrates with remo workflow

---

## Phase 6: User Story 4 - Multi-Distribution Support Preparation (Priority: P3)

**Goal**: Distribution-specific logic is isolated for easy extension to Ubuntu and other distros

**Independent Test**: Code review confirms package installation and OS detection are isolated; unsupported OS fails gracefully

### Implementation for User Story 4

- [ ] T018 [US4] Add OS family detection variable (ansible_os_family) in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T019 [US4] Refactor package installation to use when: condition for Suse family in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T020 [US4] Add placeholder task block for Debian family (Ubuntu) with comment in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T021 [US4] Add pre-flight check for supported OS with clear fail message in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T022 [US4] Document extension points in ansible/roles/incus_bootstrap/defaults/main.yml comments

**Checkpoint**: All user stories should now be independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T023 [P] Add incusd restart handler in ansible/roles/incus_bootstrap/handlers/main.yml
- [ ] T024 [P] Add post-bootstrap verification tasks (incus version, storage list, network list) in ansible/roles/incus_bootstrap/tasks/main.yml
- [ ] T025 Run quickstart.md validation steps manually

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-6)**: All depend on Foundational phase completion
  - US1 (P1) → US2 (P2) → US3 (P2) → US4 (P3) recommended order
  - US2 depends on US1 tasks existing (adds idempotency to them)
  - US3 depends on playbook from US1
  - US4 refactors US1 package installation
- **Polish (Phase 7)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 (P1)**: Can start after Foundational (Phase 2) - Core implementation
- **User Story 2 (P2)**: Modifies US1 tasks to add idempotency - depends on US1
- **User Story 3 (P2)**: Validates and documents US1 playbook - depends on US1
- **User Story 4 (P3)**: Refactors US1 for multi-distro - depends on US1

### Within Each User Story

- Tasks within a phase are generally sequential (same file)
- Tasks marked [P] in Setup can run in parallel (different files)

### Parallel Opportunities

- **Phase 1**: T002 and T003 can run in parallel (different files in role)
- **Phase 2**: T005a, T005b, T005c can run in parallel (same file but independent checks)
- **Phase 3-6**: User story phases are sequential due to same-file modifications
- **Phase 7**: T023 (handlers) and T024 (tasks) modify different files

---

## Parallel Example: Setup Phase

```bash
# These tasks can run in parallel (different files):
Task T002: "Create role defaults file at ansible/roles/incus_bootstrap/defaults/main.yml"
Task T003: "Create empty handlers file at ansible/roles/incus_bootstrap/handlers/main.yml"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup (role structure)
2. Complete Phase 2: Foundational (inventory + pre-flight checks)
3. Complete Phase 3: User Story 1 (core installation)
4. **STOP and VALIDATE**: Run `./run.sh incus_bootstrap.yml` on OpenSUSE Tumbleweed
5. Verify: `incus launch images:alpine/edge test-container`

### Incremental Delivery

1. Complete Setup + Foundational → Role structure ready
2. Add User Story 1 → Test on fresh system → MVP!
3. Add User Story 2 → Test re-runs → Safe idempotency
4. Add User Story 3 → Test via run.sh → Remo integration
5. Add User Story 4 → Code review → Extensibility ready

### Single Developer Strategy

Work sequentially through phases:
1. Setup (10 min)
2. Foundational + pre-flight checks (10 min)
3. US1 core install (20 min)
4. US2 idempotency (10 min)
5. US3 integration (10 min)
6. US4 extensibility (15 min)
7. Polish (10 min)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Most tasks modify the same file (main.yml) so limited parallel opportunities
- Ansible roles are inherently sequential in execution
- Verify each checkpoint before proceeding
- Commit after each phase or logical group
