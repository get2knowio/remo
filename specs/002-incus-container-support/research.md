# Research: Incus Container Support

**Feature Branch**: `002-incus-container-support`
**Date**: 2026-01-07
**Status**: Complete

## Overview

This research consolidates technical findings for implementing Incus container management in remo, focusing on achieving workflow parity with the existing Hetzner provisioning system.

## Research Areas

### 1. Container Lifecycle Commands

**Decision**: Use `incus init` + `incus start` (two-step) rather than `incus launch` (one-step)

**Rationale**: Two-step approach allows cloud-init configuration to be set before the container starts, which is required for SSH key injection to work reliably.

**Alternatives Considered**:
- `incus launch` with inline config: More complex quoting, less readable in Ansible
- Manual SSH setup via `incus exec`: Not idempotent, doesn't match cloud patterns

**Key Commands**:

| Operation | Command |
|-----------|---------|
| Create (stopped) | `incus init images:ubuntu/24.04/cloud <name>` |
| Start | `incus start <name>` |
| Stop | `incus stop <name>` |
| Delete (force) | `incus delete <name> --force` |
| Check exists | `incus info <name>` (returns non-zero if not exists) |
| Get IP | `incus list <name> --format=json \| jq -r '.[0].state.network.eth0.addresses[0].address'` |

### 2. SSH Access Configuration

**Decision**: Use cloud-init `user-data` configuration with the cloud-enabled image variant

**Rationale**:
- Ubuntu cloud images (`images:ubuntu/24.04/cloud`) have cloud-init pre-configured
- SSH keys injected at first boot via cloud-init are idempotent
- Matches the pattern used by Hetzner cloud-init for VMs
- Default `remo` user aligns with existing role expectations

**Alternatives Considered**:
- Manual `incus exec` to install openssh-server and add keys: Not idempotent, error-prone
- Custom profile with static cloud-init: Less flexible for per-container SSH keys

**Cloud-Init User-Data Format**:
```yaml
#cloud-config
users:
  - name: remo
    groups: sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - <public_key_content>
```

**Image Selection**:
- Use `images:ubuntu/24.04/cloud` (not `images:ubuntu/24.04`)
- Cloud variant includes cloud-init, smaller size, faster boot

### 3. Ansible Integration Patterns

**Decision**: Use `ansible.builtin.shell` for Incus operations with dynamic inventory via `add_host`

**Rationale**:
- No stable Ansible module for Incus container lifecycle (community.general has connection/inventory plugins only)
- Shell commands match the existing Hetzner role pattern (`hetzner.hcloud` modules wrap API calls)
- `add_host` for dynamic inventory matches `hetzner_server_ip` pattern

**Alternatives Considered**:
- `community.general.incus` connection plugin: Only for running commands inside containers, not lifecycle
- Third-party `kmpm.incus` collection: WIP, not production-ready
- Static inventory only: Requires manual IP updates, not suitable for dynamic provisioning

**Wait Patterns**:

```yaml
# Wait for network address assignment
- name: Wait for container IP
  ansible.builtin.shell:
    cmd: incus list {{ container_name }} --format=json | jq -r '.[0].state.network.eth0.addresses[] | select(.family == "inet") | .address'
  register: container_ip_result
  retries: 30
  delay: 2
  until: container_ip_result.stdout | length > 0

# Wait for SSH availability
- name: Wait for SSH
  ansible.builtin.wait_for:
    host: "{{ container_ip }}"
    port: 22
    timeout: 180
```

### 4. Persistent Storage Mounts

**Decision**: Use Incus disk devices with `shift=true` for host directory mounts

**Rationale**:
- Disk device syntax is straightforward and idempotent
- `shift=true` handles UID/GID mapping for unprivileged containers
- Matches the spec requirement for data preservation on teardown

**Alternatives Considered**:
- Storage pool volumes: More complex, overkill for development use case
- No persistence: Doesn't meet spec requirement FR-006

**Mount Syntax**:
```bash
incus config device add <container> <device_name> disk \
  source=/host/path \
  path=/container/path \
  shift=true
```

### 5. Network Configuration

**Decision**: Use default `incusbr0` macvlan network (created by 001-bootstrap-incus-host)

**Rationale**:
- Macvlan provides containers with LAN IP addresses via DHCP
- Containers are directly accessible from any machine on the network
- Mirrors the Hetzner workflow - SSH to containers from workstation using LAN IPs
- No NAT or port forwarding complexity

**Alternatives Considered**:
- Bridge network with NAT: Simpler setup but containers only reachable from host
- Port forwarding: Adds configuration overhead for each service

**Container Accessibility**:
- Containers get IPs from LAN's DHCP server (e.g., 192.168.x.x)
- **Important**: The Incus host CANNOT reach containers directly (macvlan kernel limitation)
- Access containers from a separate workstation (same as Hetzner workflow)
- IP assignment may take a few seconds after container start - polling required

### 6. Role Compatibility Analysis

**Decision**: Existing configuration roles (docker, nodejs, user_setup) can be reused without modification

**Rationale**:
- Roles target Ubuntu-based hosts, same as containers
- SSH connectivity is the only requirement (satisfied by incus_container_ssh role)
- `become: true` works identically inside containers
- Package managers (apt) work identically

**Verification Points**:
- `docker` role: Uses `apt`, starts systemd services (works in unprivileged containers)
- `nodejs` role: Uses apt repository, binary installation
- `user_setup` role: Creates users, sets permissions

**Potential Issues**:
- Docker-in-Docker: May require privileged container profile (out of initial scope)
- Systemd services: Work in containers but may need `security.nesting=true` for Docker

## Inventory Architecture

**Decision**: Hybrid static + dynamic inventory approach

**Static Inventory** (`ansible/inventory/incus_containers.yml`):
- Pre-defined container entries for known environments
- Connection parameters (user: remo, SSH key path)
- Group membership for role application

**Dynamic Inventory** (via `add_host` in provisioning playbook):
- Container IP discovered at provisioning time
- Added to `incus_containers` group dynamically
- Allows immediate configuration without inventory file updates

**Pattern Alignment**:
| Hetzner Pattern | Incus Pattern |
|-----------------|---------------|
| `provision.yml` creates VM, outputs IP | `incus_container.yml` creates container, outputs IP |
| `configure.yml` targets `hetzner_server` | `incus_container_configure.yml` targets `incus_containers` |
| `teardown.yml` destroys VM | `incus_container_teardown.yml` destroys container |

## Technical Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Cloud-init not running | Low | High | Use cloud-variant image, wait for cloud-init completion |
| SSH timeout | Medium | Medium | Retry logic with exponential backoff, clear error messages |
| Docker-in-Docker fails | Medium | Low | Document privileged mode requirement, defer to future iteration |
| IP address changes on restart | Low | Medium | Use container name for identification, refresh IP on configure |

## Sources

- [Incus Documentation - First Steps](https://linuxcontainers.org/incus/docs/main/tutorial/first_steps/)
- [Incus Documentation - Instance Management](https://linuxcontainers.org/incus/docs/main/howto/instances_manage/)
- [Incus Documentation - Cloud-Init](https://linuxcontainers.org/incus/docs/main/cloud-init/)
- [Incus Documentation - Disk Devices](https://linuxcontainers.org/incus/docs/main/reference/devices_disk/)
- [Ansible community.general.incus Connection Plugin](https://docs.ansible.com/ansible/latest/collections/community/general/incus_connection.html)
- [Ansible community.general.incus Inventory Plugin](https://docs.ansible.com/ansible/latest/collections/community/general/incus_inventory.html)
