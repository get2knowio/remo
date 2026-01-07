# Research: Bootstrap Incus Host

**Feature Branch**: `001-bootstrap-incus-host`
**Date**: 2025-12-28
**Status**: Complete

## Research Tasks

### 1. Incus Package Installation on OpenSUSE Tumbleweed

**Decision**: Use the `incus` package from default openSUSE Tumbleweed repositories.

**Rationale**: Incus is available in the official openSUSE Factory repository, eliminating the need for custom repository configuration. This simplifies installation and ensures updates through standard system maintenance.

**Alternatives Considered**:
- **Building from source**: Rejected - unnecessary complexity for a packaged application
- **Flatpak/Snap**: Rejected - system daemons should be native packages for proper integration

**Key Details**:
- Package name: `incus`
- Current version: 6.19.1 (as of 2025)
- No custom repository required
- Dependencies handled automatically by zypper

---

### 2. Incus Initialization Method

**Decision**: Use `incus admin init --minimal` for non-interactive initialization, with idempotency checks before execution.

**Rationale**: The `--minimal` flag provides sensible defaults (directory storage pool, auto-configured NAT bridge) without requiring user input or complex preseed configuration. This aligns with the spec requirement for directory-based storage as the simplest, most compatible option.

**Alternatives Considered**:
- **Interactive `incus admin init`**: Rejected - not suitable for automation
- **Full preseed YAML**: Rejected - adds complexity without benefit for default configuration
- **Manual storage/network creation**: Rejected - more commands, more failure points

**Idempotency Check**:
```bash
# Check if already initialized (storage pool exists)
incus storage list --format=csv 2>/dev/null | grep -q .
```

If storage exists, skip initialization to preserve existing configuration.

---

### 3. User Group Configuration

**Decision**: Add user to `incus-admin` group for full administrative access.

**Rationale**: The spec requires users to manage containers without sudo. The `incus-admin` group provides full administrative access including `incus admin` commands needed for troubleshooting and maintenance.

**Alternatives Considered**:
- **`incus` group (restricted)**: Rejected - limited to per-user project, cannot run admin commands
- **Running as root**: Rejected - violates principle of least privilege for day-to-day use

**Security Note**: Adding users to `incus-admin` grants root-equivalent access. This is acceptable for local workstation use per the spec's scope.

---

### 4. Ansible Module Selection

**Decision**: Use `community.general.zypper` for OpenSUSE, `ansible.builtin.apt` for Ubuntu; use `ansible.builtin.systemd_service` for services.

**Rationale**: Distribution-appropriate package managers ensure proper package management. The systemd module is consistent across distributions and part of ansible-core.

**Modules Required**:
| Purpose | OpenSUSE | Ubuntu |
|---------|----------|--------|
| Package installation | `community.general.zypper` | `ansible.builtin.apt` |
| Service management | `ansible.builtin.systemd_service` | `ansible.builtin.systemd_service` |
| User management | `ansible.builtin.user` | `ansible.builtin.user` |

**Collection Dependency**: `community.general` (already in project's `requirements.yml`)

---

### 5. Ubuntu Extensibility Path

**Decision**: Support Ubuntu 24.04+ using native apt repository (Zabbly PPA as optional enhancement for older versions).

**Rationale**: Ubuntu 24.04 includes Incus in default repositories, matching the OpenSUSE pattern. Group names (`incus-admin`, `incus`) and service names (`incus.socket`, `incus.service`) are consistent across distributions.

**Alternatives Considered**:
- **Zabbly PPA required**: Rejected for 24.04+ - native packages preferred
- **Snap package**: Rejected - system daemon integration concerns

**Implementation Path**:
- Use `ansible_os_family` fact to branch package installation
- Share common tasks (service, user, initialization) across distributions

---

### 6. Service Configuration

**Decision**: Enable `incus.socket` (socket activation) rather than `incus.service` directly.

**Rationale**: Socket activation is the recommended approach per Incus documentation. The daemon starts on-demand when the socket is accessed, saving resources when not in use.

**Services to Enable**:
- `incus.socket` - Main daemon socket (required)
- `incus-user.socket` - Unprivileged user container delegation (recommended)

---

### 7. Idempotency Strategy

**Decision**: Check existing state before each modification; never overwrite existing configuration.

**Rationale**: Per spec requirement FR-006, existing storage pools, networks, and containers must be preserved.

**Checks Required**:
| Resource | Check Command | Action if Exists |
|----------|---------------|------------------|
| Incus package | `rpm -q incus` / `dpkg -l incus` | Skip install |
| Storage pool | `incus storage show default` | Skip init |
| Network | `incus network show incusbr0` | Skip network creation |
| User group | `groups $USER \| grep incus-admin` | Skip usermod |

---

## Summary Table

| Topic | Decision | Module/Command |
|-------|----------|----------------|
| OpenSUSE package | `incus` from Factory | `community.general.zypper` |
| Ubuntu package | `incus` from apt (24.04+) | `ansible.builtin.apt` |
| Admin group | `incus-admin` | `ansible.builtin.user` |
| Service | `incus.socket` (socket activation) | `ansible.builtin.systemd_service` |
| Initialization | `incus admin init --minimal` | `ansible.builtin.command` with check |
| Idempotency | Check storage/network existence | Shell conditionals |

## References

- [Incus Installation Documentation](https://linuxcontainers.org/incus/docs/main/installing/)
- [How to Initialize Incus](https://linuxcontainers.org/incus/docs/main/howto/initialize/)
- [Incus Authorization](https://linuxcontainers.org/incus/docs/main/authorization/)
- [community.general.zypper Module](https://docs.ansible.com/ansible/latest/collections/community/general/zypper_module.html)
- [openSUSE Software - Incus Package](https://software.opensuse.org/package/incus)
