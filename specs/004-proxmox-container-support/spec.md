# Feature Specification: Proxmox VE LXC Container Support

**Feature Branch**: `004-proxmox-container-support`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: "Add Proxmox VE LXC container support to remo, mirroring the existing Incus provider. Users with a Proxmox VE node should be able to run `remo proxmox create dev1` and get the same dev environment workflow they'd get from `remo incus create dev1` or `remo hetzner create dev1`."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Create Local Development Container on Proxmox (Priority: P1)

As a developer with a homelab Proxmox VE node, I want to spin up an LXC container on that node using the same workflow I use for Incus and Hetzner, so I can have an isolated dev environment without learning a new tool or migrating hypervisors.

**Why this priority**: This is the core value proposition — feature parity with the Incus provider for users who already have Proxmox.

**Independent Test**: Run `remo proxmox create dev1 --host prox01 --user root` and verify SSH connectivity to the container as the `remo` user from the workstation.

**Acceptance Scenarios**:

1. **Given** Proxmox VE is installed on a node and reachable via SSH as a sudoer, **When** I run `remo proxmox create dev1 --host prox01 --user root`, **Then** a new LXC container named `dev1` is created on `prox01` with a DHCP-assigned LAN IP and the `remo` user configured for SSH key auth.
2. **Given** a container with the same hostname already exists, **When** I run create again, **Then** the existing container is preserved (idempotent) and dev tools are re-applied without recreating it.
3. **Given** create succeeds, **When** I run `ssh remo@<ip>`, **Then** I connect successfully within 3 minutes of starting create.

---

### User Story 2 - Configure Container Using Existing Roles (Priority: P2)

As a developer, I want to apply the same `docker`, `nodejs`, `user_setup`, `fzf`, `github_cli`, `zellij`, and `devcontainers` Ansible roles to Proxmox containers as I already use for Incus and Hetzner, so I don't maintain provider-specific configurations.

**Why this priority**: Role reuse keeps the codebase honest about what's a transport difference (Incus vs Proxmox) versus a behavior difference (which there shouldn't be).

**Independent Test**: After `remo proxmox create`, the container has Docker installed and runnable, Node.js available, and the project menu appears on SSH login.

**Acceptance Scenarios**:

1. **Given** a running Proxmox LXC container with SSH access, **When** the configure play runs, **Then** all dev tools install successfully without modifying the existing roles.
2. **Given** `--only zellij` or `--skip docker`, **When** I run `remo proxmox update dev1`, **Then** only those tools are touched.
3. **Given** a fully configured container, **When** I re-run `remo proxmox update dev1`, **Then** the run is idempotent.

---

### User Story 3 - Manage Container Inventory (Priority: P2)

As a developer, I want Proxmox containers to be tracked in `~/.config/remo/known_hosts` alongside Incus, Hetzner, and AWS hosts, so I can pick them from `remo shell` and target them by name.

**Why this priority**: Inventory parity is what makes "same workflow" actually feel the same.

**Independent Test**: After create, `remo proxmox list` shows the container with its node, VMID, IP, and SSH command. `remo shell` includes it in the picker.

**Acceptance Scenarios**:

1. **Given** a freshly created container, **When** I run `remo proxmox list`, **Then** the container appears with `CONTAINER`, `NODE`, `VMID`, `SSH HOST`, and `SSH COMMAND` columns.
2. **Given** containers exist on the node that weren't created by remo, **When** I run `remo proxmox sync --host prox01 --user root`, **Then** they are added to `known_hosts`.
3. **Given** a container has been destroyed, **When** I run `remo proxmox list`, **Then** it no longer appears.

---

### User Story 4 - Destroy Container (Priority: P3)

As a developer, I want to destroy a Proxmox LXC container from my workstation when I'm done with it, so I can reclaim resources.

**Why this priority**: Lifecycle completion. Less critical than create because containers can also be destroyed via the Proxmox UI.

**Independent Test**: `remo proxmox destroy dev1 --yes` removes the container and its registry entry.

**Acceptance Scenarios**:

1. **Given** a running container, **When** I run destroy, **Then** the container is stopped and `pct destroy <vmid>` removes it from the node.
2. **Given** a non-existent container name, **When** I run destroy, **Then** the operation completes without error and the registry entry is cleaned up.
3. **Given** `--remove-storage`, **When** I run destroy, **Then** the container's rootfs volume is purged from storage.

---

### User Story 5 - LAN Networking (Priority: P3)

As a developer, I want services running in my Proxmox containers to be reachable from my workstation (and any LAN device) on a stable LAN IP, so I can test web apps and APIs.

**Why this priority**: Network reachability matters but is a default consequence of bridged DHCP and not a separate feature.

**Independent Test**: Run a web server in the container and `curl <container-ip>:port` from the workstation.

**Acceptance Scenarios**:

1. **Given** a container on the default `vmbr0` bridge, **When** I check its IP, **Then** it has a LAN IP from the upstream DHCP server.
2. **Given** a container running a service on a port, **When** I access it from the workstation, **Then** the service responds.
3. **Given** the Proxmox host itself, **When** I `ping` the container from the host, **Then** it responds (unlike Incus macvlan, bridged containers and the host can talk to each other).

---

### Edge Cases

- What happens when the configured bridge (`vmbr0` by default) doesn't exist on the node?
- What happens when the configured storage (`local-lvm` by default) doesn't exist or has no free space?
- What happens when the requested LXC template hasn't been downloaded via `pveam`?
- What happens when `pvesh get /cluster/nextid` returns a value that's actually in use (race with another tool)?
- How does the system handle a container that's stuck in `Starting` and never gets an IP?
- What happens when the Proxmox node is in a cluster and the user passes the wrong `--node`?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST create Proxmox LXC containers via SSH+`pct` on a sudoer of the Proxmox node, using a single CLI command with a hostname parameter.
- **FR-002**: System MUST configure containers with SSH key-based authentication for a user named `remo` (configurable) as part of provisioning.
- **FR-003**: System MUST wait for an IPv4 address on `eth0` and for SSH to accept connections before declaring success.
- **FR-004**: System MUST apply the existing dev-tools Ansible roles (`docker`, `nodejs`, `user_setup`, `fzf`, `github_cli`, `zellij`, `devcontainers`) without modification.
- **FR-005**: System MUST register provisioned containers in `~/.config/remo/known_hosts` with `type=proxmox`, `name="<node>/<container>"`, `host=<ip>`, `user="remo"`, `instance_id="<vmid>"`, `access_mode="direct"`.
- **FR-006**: System MUST support container destruction via `pct stop` + `pct destroy`, with optional `--remove-storage` to purge the rootfs volume.
- **FR-007**: System MUST be idempotent — repeated create runs against an existing container preserve it and re-apply tool configuration; repeated destroy runs against a non-existent container exit cleanly.
- **FR-008**: System MUST default to the Ubuntu 24.04 LXC template (`ubuntu-24.04-standard_24.04-2_amd64.tar.zst`) and allow overriding via `--template`.
- **FR-009**: System MUST allocate VMIDs via `pvesh get /cluster/nextid` rather than asking the user to pick one.
- **FR-010**: System MUST attach containers to a Linux bridge (`vmbr0` by default) with DHCP so containers receive LAN IPs and are reachable from any LAN device including the Proxmox host itself.
- **FR-011**: System MUST default to **unprivileged** containers with `nesting=1` features enabled, so Docker-in-Docker works while keeping the security posture of the Incus provider.
- **FR-012**: System MUST verify that the configured bridge, storage, and LXC template are present on the node during `remo proxmox bootstrap`, and download the template via `pveam` if missing.

### Key Entities

- **Container**: A running Proxmox LXC instance with a unique VMID (numeric) and a hostname. Identified in remo by `(node, hostname)`. Stores VMID in the `instance_id` field of the registry.
- **Node**: A single Proxmox VE host. Containers live on a specific node; the user supplies the node name (defaults to `--host` if not separately set).
- **Template**: A downloaded LXC root filesystem tarball stored on a Proxmox storage pool (typically `local`), referenced as `<storage>:vztmpl/<filename>`.
- **Bridge**: A Linux bridge on the Proxmox node (e.g., `vmbr0`) that containers attach to via `--net0 bridge=...,ip=dhcp`.
- **Inventory Entry**: A `KnownHost` row with `type=proxmox` storing the container's runtime location and access info.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can provision a new Proxmox LXC container and SSH into it within 3 minutes from running `remo proxmox create`.
- **SC-002**: All existing dev-tool roles execute on Proxmox containers without modification.
- **SC-003**: Provision, update, and destroy operations complete without manual intervention on a node that has been bootstrapped.
- **SC-004**: The Proxmox CLI surface mirrors the Incus CLI surface — same subcommands, analogous flags. A user familiar with `remo incus` finds `remo proxmox` immediately usable.
- **SC-005**: Containers are reachable from the user's workstation (and the Proxmox host itself) via the bridge-assigned LAN IP immediately after provisioning completes.
- **SC-006**: `remo proxmox list` and `remo shell` both correctly include Proxmox containers alongside other providers.
- **SC-007**: Bootstrap is idempotent — running `remo proxmox bootstrap` against an already-prepared node makes no changes and exits 0.

## Assumptions

- Proxmox VE 8.x or newer is already installed on the target node. remo does **not** install Proxmox itself (it's an OS).
- The Proxmox node is reachable via SSH from the user's workstation as either `root` or a sudoer.
- A Linux bridge (default `vmbr0`) and a storage backend that supports rootfs volumes (default `local-lvm`; `local-zfs` and `local` directory storage also work) exist on the node.
- The user's SSH public key exists at `~/.ssh/id_rsa.pub` on the workstation.
- The Proxmox API token path is **deliberately not used** — see ADR rationale in `research.md`. SSH+`pct` is the canonical transport.
- Containers will run as the `remo` user (configurable), with sudo NOPASSWD.
- LXC containers do **not** use cloud-init (no native support in Proxmox LXC). User provisioning happens via `pct exec` from the playbook running on the Proxmox host.

## Scope Boundaries

### In Scope

- Single Proxmox node container management (cluster works, but user picks the node).
- LXC container lifecycle: create, configure, update tools, destroy.
- SSH-based Ansible connectivity to containers (workstation → container LAN IP).
- Inventory integration via `known_hosts`.
- Reuse of existing dev-tool roles unchanged.
- Default unprivileged containers with nesting (Docker-in-Docker).
- Bridge-based DHCP networking (`vmbr0`).

### Out of Scope

- KVM/QEMU VM creation on Proxmox (separate provider, separate effort).
- Multi-node cluster auto-routing (user explicitly picks `--node`).
- Snapshots, backups, restore.
- Storage migration, container migration between nodes.
- Privileged containers (would require a different security review).
- API-token transport (pure-SSH is sufficient; can be added later if a user needs to avoid SSH to the node).
- Custom LXC templates / template build pipelines.
- GPU passthrough or LXC device mapping beyond defaults.
- Windows or non-Linux containers (Proxmox LXC is Linux-only).

## Dependencies

- **Proxmox VE 8.x** installed on the target node with `pct`, `pveam`, `pvesh`, and `pvesm` available.
- **Existing Ansible roles**: `docker`, `nodejs`, `user_setup`, `fzf`, `github_cli`, `zellij`, `devcontainers` must keep their current behavior.
- **`KnownHost` model and core utilities**: `core/known_hosts.py`, `core/ansible_runner.py`, `core/ssh.py`, `core/validation.py`, `core/version.py`, `core/output.py`, `models/host.py` are reused unchanged.

## Differences from Incus Workflow

| Aspect              | Incus Containers                          | Proxmox LXC Containers                    |
| ------------------- | ----------------------------------------- | ----------------------------------------- |
| Provisioning CLI    | `incus`                                   | `pct`                                     |
| Image source        | On-demand pull from `images:` remote      | Pre-downloaded template via `pveam`        |
| User provisioning   | cloud-init                                | `pct exec` from host (no cloud-init)      |
| Default network     | macvlan (`incusbr0`) — host can't reach   | Bridge (`vmbr0`) — host can reach         |
| Identifier          | Name                                      | VMID (numeric); hostname stored separately |
| Bootstrap install   | Installs Incus from package repo          | Verify-only; Proxmox is the OS            |
| Template selection  | `images:ubuntu/24.04/cloud`               | `local:vztmpl/ubuntu-24.04-standard_*`    |
| Storage             | `default` (dir) pool                       | `local-lvm` / `local-zfs` / `local`       |
| Privilege model     | Unprivileged                              | Unprivileged with `nesting=1`             |

**Key similarity**: Identical access pattern — SSH from your workstation to a LAN IP — and identical dev-tools roles applied via the configure play.
