# Data Model: Bootstrap Incus Host

**Feature Branch**: `001-bootstrap-incus-host`
**Date**: 2025-12-28
**Status**: Complete

## Overview

This feature is an Ansible role for infrastructure automation. The "data model" consists of:
1. **Role Variables** - Configuration inputs that control behavior
2. **System State** - The expected state of the host after bootstrap
3. **Ansible Facts** - Dynamic system information used for conditionals

## Role Variables (defaults/main.yml)

### Core Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_storage_pool_name` | string | `"default"` | Name of the storage pool to create |
| `incus_storage_pool_driver` | string | `"dir"` | Storage backend driver (dir, zfs, btrfs, lvm) |
| `incus_network_name` | string | `"incusbr0"` | Name of the NAT bridge network |
| `incus_network_ipv4` | string | `"auto"` | IPv4 address/CIDR or "auto" for automatic |
| `incus_network_ipv6` | string | `"none"` | IPv6 address/CIDR or "none" to disable |
| `incus_user` | string | `"{{ ansible_user_id }}"` | User to add to incus-admin group |

### Behavioral Flags

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `incus_skip_init` | boolean | `false` | Skip initialization even if storage doesn't exist |
| `incus_enable_user_socket` | boolean | `true` | Enable incus-user.socket for unprivileged delegation |

### Derived/Internal Variables

| Variable | Source | Description |
|----------|--------|-------------|
| `incus_os_family` | `ansible_os_family` | Detected OS family (Suse, Debian) |
| `incus_is_initialized` | Runtime check | Whether Incus already has storage pools |

## System State Entities

### 1. Incus Installation

| Attribute | Expected State |
|-----------|----------------|
| Package installed | `incus` present |
| Service active | `incus.socket` enabled and running |
| User socket | `incus-user.socket` enabled and running (if `incus_enable_user_socket`) |

**State Transitions**:
- `not_installed` → `installed` → `service_started` → `initialized`
- Re-runs preserve state at current level

### 2. Storage Pool

| Attribute | Expected State |
|-----------|----------------|
| Name | `{{ incus_storage_pool_name }}` |
| Driver | `{{ incus_storage_pool_driver }}` |
| Status | Active |
| Path | `/var/lib/incus/storage-pools/{{ incus_storage_pool_name }}` (for dir driver) |

**Validation Rules**:
- Pool name must be alphanumeric with dashes/underscores
- Driver must be one of: `dir`, `zfs`, `btrfs`, `lvm`
- Only create if no storage pools exist (preserves custom pools)

### 3. Network Bridge

| Attribute | Expected State |
|-----------|----------------|
| Name | `{{ incus_network_name }}` |
| Type | `bridge` (managed) |
| IPv4 NAT | Enabled |
| IPv4 Address | Auto-assigned or `{{ incus_network_ipv4 }}` |
| IPv6 | Disabled (per `{{ incus_network_ipv6 }}`) |

**Validation Rules**:
- Network name must be alphanumeric with dashes/underscores
- Only create if no managed networks exist (preserves custom networks)

### 4. User Permissions

| Attribute | Expected State |
|-----------|----------------|
| User | `{{ incus_user }}` |
| Groups | Member of `incus-admin` |
| Effect | Can run `incus` commands without sudo (after re-login) |

**State Transitions**:
- User already in group → No change
- User not in group → Add to group
- New session required to activate group membership

## Ansible Facts Used

| Fact | Purpose |
|------|---------|
| `ansible_os_family` | Select package manager (zypper vs apt) |
| `ansible_distribution` | Verify supported distribution |
| `ansible_distribution_major_version` | Verify Ubuntu version for native repo support |
| `ansible_user_id` | Default value for `incus_user` |

## Entity Relationships

```
┌─────────────────────────────────────────────────────────────────┐
│                       Host System                                │
│                                                                 │
│  ┌─────────────────┐                                            │
│  │ Incus Package   │                                            │
│  │                 │                                            │
│  │ - incus binary  │                                            │
│  │ - incusd daemon │                                            │
│  └────────┬────────┘                                            │
│           │ enables                                             │
│           ▼                                                     │
│  ┌─────────────────┐    ┌─────────────────┐                     │
│  │ incus.socket    │◄───│ incus-user.socket│                    │
│  │ (systemd unit)  │    │ (optional)       │                    │
│  └────────┬────────┘    └─────────────────┘                     │
│           │ starts daemon when accessed                         │
│           ▼                                                     │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Incus Daemon                          │   │
│  │                                                          │   │
│  │  ┌─────────────────┐    ┌─────────────────┐             │   │
│  │  │ Storage Pool    │    │ Network Bridge  │             │   │
│  │  │ (default/dir)   │    │ (incusbr0)      │             │   │
│  │  │                 │    │                 │             │   │
│  │  │ ► Container     │◄───│ ► NAT to host   │             │   │
│  │  │   filesystems   │    │ ► DHCP/DNS      │             │   │
│  │  └─────────────────┘    └─────────────────┘             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─────────────────┐                                            │
│  │ User Permissions│                                            │
│  │                 │                                            │
│  │ incus-admin ────┼──► Full daemon control (root equivalent)   │
│  │ group           │                                            │
│  └─────────────────┘                                            │
└─────────────────────────────────────────────────────────────────┘
```

## Idempotency Model

Each entity has an independent idempotency check:

| Entity | Check | Idempotent Behavior |
|--------|-------|---------------------|
| Package | `rpm -q incus` / `dpkg -l incus` | Skip if installed |
| Service | `systemctl is-enabled incus.socket` | Enable only if not enabled |
| Storage | `incus storage show {{ pool }}` | Create only if no pools exist |
| Network | `incus network list --format=csv` | Create only if no managed networks |
| User group | `groups {{ user }} \| grep incus-admin` | Add only if not member |

## Validation at Boundaries

### Pre-flight Checks (Before Making Changes)

| Check | Failure Action |
|-------|----------------|
| Supported OS (Suse/Debian family) | Fail with clear message |
| Sudo privileges | Fail with clear message |
| Minimum disk space (10GB) | Warn, continue (may fail later) |

### Post-bootstrap Verification

| Verification | Expected Result |
|--------------|-----------------|
| `incus version` | Returns version string |
| `incus storage list` | Shows at least one pool |
| `incus network list` | Shows at least one managed network |
| `systemctl is-active incus.socket` | Returns "active" |
