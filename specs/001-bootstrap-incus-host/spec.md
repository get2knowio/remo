# Feature Specification: Bootstrap Incus Host

**Feature Branch**: `001-bootstrap-incus-host`
**Created**: 2025-12-28
**Status**: Draft
**Input**: User description: "Automate the setup of a local Linux host so it is ready to run Incus/LXC containers, including installation of necessary packages, configuring system services, adjusting permissions, and initializing Incus with a default storage pool and networking for container support. First iteration targets OpenSUSE Tumbleweed with extensibility to other distributions such as Ubuntu."

## Clarifications

### Session 2025-12-28

- Q: What should be the default storage pool backend when initializing Incus? → A: `dir` (directory-based) - works on any filesystem, simplest and most compatible
- Q: What level of output should the bootstrap provide during execution? → A: Summary - show task names as they execute, details only on errors

## Motivation

The remo project currently bootstraps remote cloud servers (Hetzner) for development environments using devcontainers. However, developers also need the ability to run containers on their local Linux workstations for:

1. **Offline development**: Work on containerized projects without requiring cloud connectivity
2. **Cost savings**: Avoid cloud compute charges for local development and testing
3. **Faster iteration**: Eliminate network latency when working with containers locally
4. **Local testing**: Validate container configurations before deploying to cloud environments

Incus (the community fork of LXD) provides a lightweight container and VM management solution that complements the existing Docker-based devcontainer workflow. By automating the Incus host setup, developers can have a consistent, reproducible local container environment.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - First-Time Incus Setup (Priority: P1)

A developer wants to set up their OpenSUSE Tumbleweed workstation to run Incus containers for the first time. They run a single bootstrap command and the system is fully configured to create and manage containers.

**Why this priority**: This is the core value proposition - transforming a bare Linux system into a working Incus host with minimal effort.

**Independent Test**: Can be fully tested by running the bootstrap on a fresh OpenSUSE Tumbleweed installation and then successfully launching a test container.

**Acceptance Scenarios**:

1. **Given** a fresh OpenSUSE Tumbleweed system without Incus, **When** the user runs the bootstrap, **Then** Incus is installed, configured, and the user can launch containers without additional manual steps.

2. **Given** a system where the user is not in the required groups, **When** the bootstrap runs, **Then** the user is added to the necessary groups to manage Incus without sudo (after re-login).

3. **Given** a newly bootstrapped system, **When** the user runs `incus launch images:alpine/edge test-container`, **Then** the container starts successfully within 60 seconds.

---

### User Story 2 - Idempotent Re-Runs (Priority: P2)

A developer runs the bootstrap on a system that already has Incus installed (either from a previous bootstrap or manual installation). The bootstrap completes without errors and without disrupting any existing containers or configurations.

**Why this priority**: Idempotency enables safe re-runs after system updates and supports the "run it again to verify" pattern common in infrastructure automation.

**Independent Test**: Can be tested by running the bootstrap twice consecutively on the same system and verifying both runs succeed and existing containers remain operational.

**Acceptance Scenarios**:

1. **Given** a system with Incus already installed and running containers, **When** the bootstrap runs again, **Then** the existing containers continue running without interruption.

2. **Given** a system with a custom Incus storage pool named "custom-pool", **When** the bootstrap runs, **Then** the custom pool is preserved and the default pool is only created if no pool exists.

3. **Given** a system with existing Incus network configuration, **When** the bootstrap runs, **Then** existing network settings are preserved.

---

### User Story 3 - Integration with Remo Bootstrap (Priority: P2)

A developer uses the existing remo `run.sh` pattern to execute the Incus bootstrap, maintaining consistency with how other remo provisioning is performed.

**Why this priority**: Consistency with existing patterns reduces learning curve and enables future integration into larger bootstrap workflows.

**Independent Test**: Can be tested by invoking the bootstrap through the remo run script interface and verifying it behaves consistently with other remo playbooks.

**Acceptance Scenarios**:

1. **Given** a user familiar with remo's `run.sh` workflow, **When** they want to bootstrap Incus, **Then** they can use the same invocation pattern as other remo playbooks.

2. **Given** a local inventory file for localhost, **When** the bootstrap runs, **Then** it correctly targets the local machine without requiring SSH.

---

### User Story 4 - Multi-Distribution Support Preparation (Priority: P3)

While the first implementation targets OpenSUSE Tumbleweed, the bootstrap structure supports adding other distributions (Ubuntu, Fedora, etc.) in future iterations.

**Why this priority**: Future extensibility reduces technical debt, but initial value is delivered on a single supported platform.

**Independent Test**: Can be verified by code review to confirm distribution-specific logic is isolated and documented for extension.

**Acceptance Scenarios**:

1. **Given** the bootstrap implementation, **When** reviewed for extensibility, **Then** distribution-specific package names and commands are isolated from core logic.

2. **Given** a user running on an unsupported distribution, **When** the bootstrap runs, **Then** it fails gracefully with a clear message indicating supported distributions.

---

### Edge Cases

- What happens when the system has insufficient disk space for the default storage pool?
- How does the system handle if required kernel modules (overlay, br_netfilter) are not available?
- What happens when package installation fails due to repository issues?
- How does the bootstrap behave when run as a non-sudo user?
- What happens if Incus services fail to start after installation?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST install Incus and all required dependencies on OpenSUSE Tumbleweed
- **FR-002**: System MUST configure and start the Incus daemon service
- **FR-003**: System MUST add the current user to the incus-admin group (or equivalent) for non-root container management
- **FR-004**: System MUST initialize Incus with a default storage pool if no storage pools exist
- **FR-005**: System MUST configure default networking (NAT bridge) for container internet access if no networks exist
- **FR-006**: System MUST preserve existing Incus configurations (storage pools, networks, containers) when re-run
- **FR-007**: System MUST validate prerequisites (sudo access, supported OS) before making changes
- **FR-008**: System MUST provide clear error messages when operations fail
- **FR-009**: System MUST follow the remo Ansible role pattern for consistency with existing infrastructure
- **FR-010**: System MUST support running against localhost without requiring SSH
- **FR-011**: System MUST display task names during execution with detailed output only on errors (summary verbosity)

### Key Entities

- **Host System**: The local Linux machine being configured to run Incus
- **Incus Installation**: The Incus daemon and CLI tools installed on the system
- **Storage Pool**: A configured storage backend for container filesystems (default: directory-based)
- **Network Bridge**: A NAT bridge network providing container connectivity to the internet
- **User Permissions**: Group memberships allowing non-root container management

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A fresh OpenSUSE Tumbleweed system is ready to run containers within 5 minutes of bootstrap completion
- **SC-002**: Users can launch and manage Incus containers without using sudo after a single re-login
- **SC-003**: Running the bootstrap twice on the same system produces no errors and no disruption to existing containers
- **SC-004**: 100% of bootstrap runs on supported systems complete successfully (no partial states)
- **SC-005**: Adding support for a new Linux distribution requires changes only to distribution-specific modules, not core logic

## Assumptions

- The target system has internet connectivity to download packages
- The user running the bootstrap has sudo privileges
- The system has at least 10GB of free disk space for the default storage pool
- The host system is running a 64-bit x86 or ARM architecture
- OpenSUSE Tumbleweed repositories are accessible and up-to-date
- The user will re-login (or start a new shell session) after bootstrap to activate group membership changes

## Out of Scope

- Provisioning cloud-based Incus hosts (this feature is specifically for local workstations)
- Configuring remote Incus servers or clustering
- Creating actual development containers (this feature only prepares the host)
- Integrating Incus with the existing devcontainer workflow (potential future feature)
- GUI-based container management tools
- Container image building or custom image creation
