# Feature Specification: Incus/LXC Container Support

**Feature Branch**: `002-incus-container-support`
**Created**: 2025-12-28
**Status**: Draft
**Input**: User description: "Define a specification for adding Incus/LXC container support to the remo project. The goal is to extend remo's infrastructure provisioning capabilities so that containers on a local Incus host can be created and managed in the same workflow style as remote Hetzner hosts, including inventory integration, SSH bootstrapping, Ansible roles reuse, and lifecycle management."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create Local Development Container (Priority: P1)

As a developer, I want to spin up an Incus container on my local workstation using the same workflow I use for provisioning Hetzner VMs, so that I can develop and test infrastructure configurations locally without cloud costs or network latency.

**Why this priority**: This is the core value proposition - enabling local container provisioning that mirrors the remote VM workflow. Without this, the feature provides no value.

**Independent Test**: Can be fully tested by running a single playbook command that creates a named container with SSH access, and verifying SSH connectivity to that container.

**Acceptance Scenarios**:

1. **Given** an Incus host is bootstrapped with storage and networking configured, **When** I run the container provisioning playbook with a container name, **Then** a new Incus container is created with the specified name and image.
2. **Given** a container does not exist, **When** I run the provisioning playbook, **Then** the container is created, started, and becomes reachable via SSH within 3 minutes.
3. **Given** a container already exists with the same name, **When** I run the provisioning playbook, **Then** the existing container is preserved (idempotent behavior) and no data is lost.

---

### User Story 2 - Configure Container Using Existing Roles (Priority: P2)

As a developer, I want to apply existing Ansible configuration roles (docker, nodejs, user_setup, etc.) to my Incus containers, so that I can reuse proven configurations without duplicating playbooks.

**Why this priority**: Role reuse is essential for maintaining a single source of truth for configurations. This directly supports the goal of workflow parity with Hetzner VMs.

**Independent Test**: Can be fully tested by running the configure playbook against a provisioned container and verifying that Docker, Node.js, and other tools are installed and functional.

**Acceptance Scenarios**:

1. **Given** a running Incus container with SSH access, **When** I run the configuration playbook targeting that container, **Then** all specified roles execute successfully.
2. **Given** an existing role designed for remote Hetzner hosts, **When** I run it against an Incus container, **Then** the role applies without modification (or with documented inventory-only changes).
3. **Given** a container with partially applied configuration, **When** I re-run the configuration playbook, **Then** only necessary changes are applied (idempotent behavior).

---

### User Story 3 - Manage Container Inventory (Priority: P2)

As a developer, I want Incus containers to be tracked in the Ansible inventory alongside Hetzner hosts, so that I can target containers by name and manage mixed infrastructure with a unified workflow.

**Why this priority**: Inventory integration is foundational for Ansible workflows and enables all subsequent configuration and management operations.

**Independent Test**: Can be fully tested by creating a container, verifying it appears in inventory output, and running an ad-hoc Ansible command against it.

**Acceptance Scenarios**:

1. **Given** a newly provisioned Incus container, **When** I list the Ansible inventory, **Then** the container appears as a manageable host with correct connection parameters.
2. **Given** multiple containers and Hetzner hosts, **When** I run a playbook with host patterns, **Then** I can target containers specifically, remote hosts specifically, or both.
3. **Given** a container that has been destroyed, **When** I refresh the inventory, **Then** the destroyed container no longer appears.

---

### User Story 4 - Destroy Container with Data Preservation Options (Priority: P3)

As a developer, I want to destroy Incus containers when no longer needed, with the option to preserve persistent data, so that I can manage resources efficiently while protecting important work.

**Why this priority**: Lifecycle management completes the provisioning story but is not blocking for initial container creation and configuration workflows.

**Independent Test**: Can be fully tested by destroying a container and verifying it no longer exists, then creating a new container and confirming data can be preserved across destruction.

**Acceptance Scenarios**:

1. **Given** a running container, **When** I run the teardown playbook, **Then** the container is stopped and deleted.
2. **Given** a container with persistent volume mounts, **When** I run teardown with data preservation enabled, **Then** the container is deleted but the host-side data directory remains intact.
3. **Given** a non-existent container name, **When** I run teardown, **Then** the operation completes successfully without error (idempotent behavior).

---

### User Story 5 - Container Networking Access (Priority: P3)

As a developer, I want to access services running inside my Incus containers from my workstation (or any machine on the LAN), so that I can test web applications, APIs, and SSH into containers just like I would with Hetzner VMs.

**Why this priority**: Service accessibility is important for development workflows. With macvlan networking, containers get LAN IPs directly from DHCP, making them accessible from any machine on the network (except the Incus host itself, which is a known macvlan limitation).

**Independent Test**: Can be fully tested by running a web server in a container and accessing it from a separate workstation on the same LAN via the container's IP address.

**Acceptance Scenarios**:

1. **Given** a running container with a web server, **When** I access the container's LAN IP address from a separate machine on the network, **Then** the web server responds.
2. **Given** a container on the default macvlan network, **When** I query the container's IP, **Then** I receive an IP address assigned by the LAN's DHCP server.
3. **Given** a running container with SSH enabled, **When** I SSH to the container's LAN IP from my workstation, **Then** I connect successfully (just like connecting to a Hetzner VM).

---

### Edge Cases

- What happens when the Incus host has insufficient storage for a new container?
- How does the system handle containers with conflicting names across different Incus remotes (if multi-remote support is considered)?
- What happens when SSH fails to connect to a container within the timeout period?
- How does the system behave when the Incus daemon is not running or accessible?
- What happens when a container image is not available in configured remotes?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST create Incus containers using a single playbook command with a container name parameter.
- **FR-002**: System MUST configure containers with SSH access (key-based authentication) as part of the provisioning process.
- **FR-003**: System MUST wait for SSH availability and verify connectivity before marking provisioning complete.
- **FR-004**: System MUST support applying existing Ansible roles to Incus containers without role modification.
- **FR-005**: System MUST register provisioned containers in Ansible inventory with appropriate connection parameters.
- **FR-006**: System MUST support container destruction with configurable data preservation options.
- **FR-007**: System MUST operate idempotently - repeated runs with the same parameters produce the same result without side effects.
- **FR-008**: System MUST use the container image `images:ubuntu/24.04/cloud` as the default (cloud variant required for cloud-init SSH key injection), with the image being configurable via playbook variables.
- **FR-009**: System MUST support both static inventory files and dynamic inventory registration via `add_host` for container tracking. (Note: A standalone dynamic inventory plugin is out of scope; containers are registered dynamically at provisioning time.)
- **FR-010**: System MUST attach containers to the default Incus macvlan network so containers receive LAN IP addresses via DHCP and are directly accessible from other machines on the network.

### Key Entities

- **Container**: A running Incus instance with a unique name, associated image, network configuration, and optional persistent storage mounts. Belongs to a single Incus remote/host.
- **Inventory Entry**: A host definition in Ansible inventory representing a container, including connection method (SSH), hostname/IP, user credentials, and group memberships.
- **Container Profile**: An Incus profile defining default resource limits, device mappings (disks, networks), and configuration. Containers may use the default profile or custom profiles.
- **Persistent Mount**: A host directory mounted into a container for data that should survive container destruction. Defined as source path (host) and target path (container).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can provision a new Incus container and SSH into it within 3 minutes from running the playbook.
- **SC-002**: Existing configuration roles (docker, nodejs, user_setup) execute successfully on Incus containers without modification.
- **SC-003**: Provisioning, configuration, and teardown operations complete without manual intervention on a bootstrapped Incus host.
- **SC-004**: The container workflow mirrors the Hetzner workflow: users issue the same style of commands with analogous parameters.
- **SC-005**: Containers are accessible from the user's workstation (or any LAN machine) via their macvlan-assigned LAN IP immediately after provisioning completes.
- **SC-006**: Data in designated persistent mounts survives container destruction when preservation is requested.
- **SC-007**: Users can manage 10+ containers simultaneously without workflow complexity increases (same commands work regardless of container count).

## Assumptions

- The Incus host has already been bootstrapped using the 001-bootstrap-incus-host feature (Incus installed, storage pool configured, macvlan network active).
- The target container image (`images:ubuntu/24.04/cloud`) is accessible via the default Incus image remote or a configured remote.
- The user running Ansible has membership in the `incus-admin` group or equivalent permissions to manage Incus containers.
- SSH key pairs exist on the control machine and can be injected into containers via cloud-init or profile configuration.
- The Incus macvlan network provides containers with LAN IP addresses via DHCP, allowing containers to be directly accessible from other machines on the network.
- **Important macvlan limitation**: The Incus host machine CANNOT directly communicate with containers on the macvlan network (this is a known kernel limitation). Users access containers from a separate workstation, just like accessing Hetzner VMs.
- The user runs Ansible playbooks from their workstation (separate from the Incus host), targeting containers by their LAN IP addresses - mirroring the Hetzner workflow where you SSH to VMs from your local machine, not from the Hetzner infrastructure.
- Container SSH access will use the same key-based authentication pattern as Hetzner hosts (public key from `~/.ssh/id_rsa.pub`).
- Initial user inside containers will be `remo` (configurable), with sudo privileges.

## Scope Boundaries

### In Scope

- Single Incus host (local workstation) container management
- Container lifecycle: create, configure, start, stop, destroy
- SSH-based Ansible connectivity to containers (from user's workstation to container LAN IPs)
- Inventory integration (static and dynamic)
- Reuse of existing configuration roles
- Persistent storage mounts from host to container
- Default macvlan networking (containers get LAN IPs via DHCP)

### Out of Scope

- Multi-host Incus clustering or remote Incus server management
- Container image building or customization
- Advanced networking (SR-IOV, custom bridges, overlay networks)
- GPU passthrough or hardware device mapping
- Container migration between hosts
- Windows or non-Linux containers
- Integration with container orchestration platforms (Kubernetes, etc.)
- Automated container scaling or load balancing
- Host-to-container direct communication (macvlan limitation; use separate workstation)

## Dependencies

- **001-bootstrap-incus-host**: Incus must be installed and initialized before container provisioning can occur.
- **Existing Ansible roles**: The docker, nodejs, user_setup, and other roles must remain compatible with both remote VMs and local containers.
- **Incus image remotes**: Default images remote (images.linuxcontainers.org) must be accessible for pulling container images.

## Differences from Hetzner Workflow

| Aspect              | Hetzner Hosts                      | Incus Containers                    |
| ------------------- | ---------------------------------- | ----------------------------------- |
| Provisioning API    | Hetzner Cloud API (hcloud)         | Incus CLI/API (local)               |
| Network Model       | Public IP + firewall rules         | LAN IP via DHCP (macvlan)           |
| Access Pattern      | SSH from workstation to VM         | SSH from workstation to container   |
| Host Communication  | N/A (hypervisor is cloud infra)    | Not possible (macvlan limitation)   |
| Persistent Storage  | Detachable cloud volumes           | Host directory mounts               |
| Cost Model          | Pay-per-use cloud billing          | No additional cost (local)          |
| Boot Time           | 1-2 minutes (VM + cloud-init)      | 5-30 seconds (container)            |
| Isolation Level     | Full VM (hardware virtualization)  | Container (shared kernel)           |
| DNS/Discovery       | DuckDNS integration                | Local /etc/hosts or mDNS            |
| Default User        | root (then remo)                   | remo (configurable)                 |

**Key similarity**: With macvlan networking, the access pattern is identical to Hetzner - you SSH to containers from your workstation using their IP addresses. The Incus host is just the hypervisor (like Hetzner's infrastructure), not an access point.
