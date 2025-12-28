# Data Model: Incus Container Support

**Feature Branch**: `002-incus-container-support`
**Date**: 2025-12-28
**Status**: Complete

## Overview

This feature implements Ansible roles for container lifecycle management. The "data model" consists of:
1. **Role Variables** - Configuration inputs that control behavior
2. **Ansible Facts** - Dynamic values registered during execution
3. **Inventory Entities** - Host definitions for container targeting
4. **Incus State** - Container and device configurations managed by roles

## Role: incus_container

Manages the complete container lifecycle: create, start, wait for readiness, register in inventory.

### Variables (defaults/main.yml)

#### Container Identity

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_container_name` | string | **required** | Unique name for the container |
| `incus_container_image` | string | `"images:ubuntu/24.04/cloud"` | Image to use (must be cloud-enabled) |
| `incus_container_profile` | string | `"default"` | Incus profile to apply |

#### SSH Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_container_ssh_user` | string | `"ubuntu"` | User for SSH access |
| `incus_container_ssh_key_path` | string | `"~/.ssh/id_rsa.pub"` | Path to public key for injection |
| `incus_container_ssh_private_key` | string | `"~/.ssh/id_rsa"` | Path to private key for connections |

#### Network Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_container_network` | string | `"incusbr0"` | Network to attach (from 001-bootstrap-incus-host) |
| `incus_container_wait_timeout` | integer | `180` | Seconds to wait for SSH availability |

#### Persistent Storage

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_container_mounts` | list | `[]` | Host directory mounts (see Mount entity below) |

### Registered Facts (set_fact)

| Fact | Type | Description |
|------|------|-------------|
| `incus_container_ip` | string | IPv4 address assigned to container |
| `incus_container_created` | boolean | Whether container was created in this run |
| `incus_container_exists` | boolean | Whether container exists (before/after) |

### State Transitions

```
                    ┌─────────────────────────────────────┐
                    │         Container Lifecycle          │
                    └─────────────────────────────────────┘
                                     │
     ┌───────────────────────────────┼───────────────────────────────┐
     ▼                               ▼                               ▼
┌─────────┐                    ┌───────────┐                   ┌───────────┐
│ absent  │───incus init──────▶│  created  │───incus start────▶│  running  │
└─────────┘                    │ (stopped) │                   └───────────┘
     ▲                         └───────────┘                         │
     │                               ▲                               │
     │                               │                               │
     │                          incus stop                           │
     │                               │                               │
     └───incus delete ───────────────┴───────incus delete --force────┘
```

**Idempotent Behavior**:
- If container exists and running → no changes, return current IP
- If container exists but stopped → start it, wait for IP
- If container absent → create, configure cloud-init, start, wait for IP

## Role: incus_container_teardown

Manages container destruction with data preservation options.

### Variables (defaults/main.yml)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_container_name` | string | **required** | Container to destroy |
| `incus_container_preserve_data` | boolean | `true` | Keep host mount directories |
| `incus_container_force` | boolean | `false` | Force delete running container |

### Registered Facts

| Fact | Type | Description |
|------|------|-------------|
| `incus_container_destroyed` | boolean | Whether container was destroyed |
| `incus_container_data_preserved` | list | Paths of preserved mount directories |

## Entity: Mount

Defines a host-to-container directory mount for persistent storage.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source` | string | yes | Absolute path on host |
| `target` | string | yes | Absolute path in container |
| `device_name` | string | no | Incus device name (default: derived from target) |

**Example**:
```yaml
incus_container_mounts:
  - source: "/home/{{ ansible_user_id }}/projects"
    target: "/home/ubuntu/projects"
    device_name: "projects"
  - source: "/mnt/data/{{ incus_container_name }}"
    target: "/data"
```

**Validation Rules**:
- `source` must be an absolute path
- `source` directory must exist or will be created
- `target` must be an absolute path
- `device_name` must be alphanumeric with underscores/dashes

## Entity: Inventory Entry

Represents a container in Ansible inventory.

| Field | Type | Description |
|-------|------|-------------|
| `ansible_host` | string | Container IP address (from `incus_container_ip`) |
| `ansible_user` | string | SSH user (default: `ubuntu`) |
| `ansible_ssh_private_key_file` | string | Path to SSH private key |
| `ansible_python_interpreter` | string | Python path (default: `/usr/bin/python3`) |

**Static Inventory Example** (`ansible/inventory/incus_containers.yml`):
```yaml
all:
  children:
    incus_containers:
      hosts:
        dev-container:
          ansible_host: 10.180.234.50
          ansible_user: ubuntu
          ansible_ssh_private_key_file: ~/.ssh/id_rsa
      vars:
        ansible_python_interpreter: /usr/bin/python3
```

**Dynamic Registration** (via `add_host`):
```yaml
- name: Add container to inventory
  ansible.builtin.add_host:
    name: "{{ incus_container_name }}"
    ansible_host: "{{ incus_container_ip }}"
    ansible_user: "{{ incus_container_ssh_user }}"
    ansible_ssh_private_key_file: "{{ incus_container_ssh_private_key }}"
    groups:
      - incus_containers
```

## Entity: Cloud-Init Configuration

User-data configuration injected into container at creation.

| Field | Type | Description |
|-------|------|-------------|
| `users` | list | User accounts to configure |
| `users[].name` | string | Username |
| `users[].groups` | string | Comma-separated group list |
| `users[].ssh_authorized_keys` | list | Public keys for SSH access |

**Generated Configuration**:
```yaml
#cloud-config
users:
  - name: "{{ incus_container_ssh_user }}"
    groups: sudo
    shell: /bin/bash
    ssh_authorized_keys:
      - "{{ lookup('file', incus_container_ssh_key_path) }}"
```

## Incus Object States

### Container State (incus info)

| Attribute | Expected Values |
|-----------|-----------------|
| Name | `{{ incus_container_name }}` |
| Status | `Running` (after provision), `Stopped` (after stop) |
| Type | `container` |
| Architecture | `x86_64` (or host architecture) |
| Created | Timestamp |

### Disk Device State

| Attribute | Expected Values |
|-----------|-----------------|
| Type | `disk` |
| Source | Host path |
| Path | Container mount point |

## Entity Relationships

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Ansible Control                              │
│                                                                          │
│  ┌──────────────────────┐      ┌───────────────────────────────────────┐ │
│  │   incus_container    │      │           Inventory                    │ │
│  │        Role          │      │                                       │ │
│  │                      │      │  ┌─────────────────────────────────┐  │ │
│  │ • container_name     │──────│  │       incus_containers         │  │ │
│  │ • container_image    │      │  │                                 │  │ │
│  │ • ssh_key_path       │      │  │  • dev-container                │  │ │
│  │ • mounts[]           │      │  │    - ansible_host: <ip>         │  │ │
│  └──────────┬───────────┘      │  │    - ansible_user: ubuntu       │  │ │
│             │                  │  │                                 │  │ │
│             │ creates/manages  │  └─────────────────────────────────┘  │ │
│             ▼                  │                   │                   │ │
│  ┌───────────────────────────────────────────────────────────────────┐ │ │
│  │                        Incus Host                                  │ │ │
│  │                                                                   │ │ │
│  │  ┌────────────────────────────────────────────────────────────┐  │ │ │
│  │  │                    Container Instance                       │  │ │ │
│  │  │                                                             │  │ │ │
│  │  │  Name: {{ incus_container_name }}                           │  │ │ │
│  │  │  Image: images:ubuntu/24.04/cloud                           │  │ │ │
│  │  │  Profile: default                                           │  │ │ │
│  │  │                                                             │  │ │ │
│  │  │  ┌─────────────────┐    ┌─────────────────┐                │  │ │ │
│  │  │  │ Cloud-Init      │    │ Network (eth0)  │                │  │ │ │
│  │  │  │                 │    │                 │                │  │ │ │
│  │  │  │ • SSH keys      │    │ • incusbr0      │◄───────────────│──│─┘ │
│  │  │  │ • ubuntu user   │    │ • DHCP IP       │  SSH connection│  │   │
│  │  │  └─────────────────┘    └─────────────────┘                │  │   │
│  │  │                                                             │  │   │
│  │  │  ┌─────────────────────────────────────────────────────┐   │  │   │
│  │  │  │ Disk Devices (Mounts)                                │   │  │   │
│  │  │  │                                                      │   │  │   │
│  │  │  │ projects: /host/projects → /home/ubuntu/projects    │   │  │   │
│  │  │  │ data: /mnt/data/name → /data                        │   │  │   │
│  │  │  └─────────────────────────────────────────────────────┘   │  │   │
│  │  └────────────────────────────────────────────────────────────┘  │   │
│  │                                                                   │   │
│  │  ┌─────────────────┐    ┌─────────────────┐                      │   │
│  │  │ Storage Pool    │    │ Network Bridge  │                      │   │
│  │  │ (from 001)      │    │ incusbr0        │                      │   │
│  │  │                 │    │ (from 001)      │                      │   │
│  │  └─────────────────┘    └─────────────────┘                      │   │
│  └───────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

## Idempotency Model

| Entity | Check | Idempotent Behavior |
|--------|-------|---------------------|
| Container | `incus info <name>` returns 0 | Skip creation if exists |
| Cloud-init | Container exists check | Only configure on new containers |
| Disk device | `incus config device list <name>` | Add only if device missing |
| Inventory | Host in group check | Update IP if changed |
| SSH connectivity | Connection test | Wait only if not connectable |

## Validation at Boundaries

### Pre-flight Checks (provisioning)

| Check | Failure Action |
|-------|----------------|
| Incus daemon running | `incus version` → fail with message |
| Image accessible | `incus image info <image>` → fail with message |
| Storage pool exists | `incus storage list` → fail with message |
| Network exists | `incus network list` → fail with message |
| SSH public key exists | `stat <key_path>` → fail with message |

### Post-provision Verification

| Verification | Expected Result |
|--------------|-----------------|
| Container running | `incus info <name>` shows Status: Running |
| IP assigned | `incus list <name>` shows IPv4 address |
| SSH accessible | `wait_for port=22` succeeds within timeout |
| Cloud-init complete | SSH command succeeds |

### Pre-teardown Checks

| Check | Action |
|-------|--------|
| Container exists | Skip if absent (idempotent) |
| Container running + force=false | Stop first, then delete |
| Mount directories exist | Preserve if `preserve_data=true` |
