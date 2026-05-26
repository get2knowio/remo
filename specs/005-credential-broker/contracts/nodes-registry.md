# Contract: `~/.config/remo/nodes.yml`

Date: 2026-05-25
Branch: 005-credential-broker

Laptop-side, per-developer registry of Incus/Proxmox nodes. Mode 0600. YAML over flat-file (unlike `known_hosts`) because the entries have ≥3 fields each and YAML stays readable.

## File shape

```yaml
version: 1
nodes:
  - name: workstation-01
    provider: incus
    host: 192.168.4.10
    ssh_user: incusadmin
    admin_sa_fnox_key: incus_workstation_01_admin_sa
    registered_at: 2026-05-25T10:00:00Z
  - name: lab-prox-02
    provider: proxmox
    host: 10.0.0.42
    ssh_user: root
    admin_sa_fnox_key: proxmox_lab_prox_02_admin_sa
    registered_at: 2026-05-23T08:12:11Z
```

## Field reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | integer | yes | Initial: `1`. Future bumps require a Remo release that widens an in-code supported set. |
| `nodes[].name` | string | yes | `^[a-z][a-z0-9-]{0,31}$`. Unique within this file. |
| `nodes[].provider` | enum `incus`/`proxmox` | yes | |
| `nodes[].host` | string | yes | SSH-reachable hostname or IP. |
| `nodes[].ssh_user` | string | yes | Username Remo SSHes as for node-side helper invocations. |
| `nodes[].admin_sa_fnox_key` | string | yes | fnox key under which this developer's admin SA token is stored. No token value here. |
| `nodes[].registered_at` | RFC3339 string | yes | Set by `remo {incus,proxmox} add-node`. |

## Read/write surface

- Read by `core.nodes.list_nodes()`, `core.nodes.get_node(name)`.
- Written atomically by `core.nodes.add_node(...)` and `core.nodes.remove_node(name)` (tempfile + `os.replace`).
- Never contains secret values. Tests assert this with a grep gate over `nodes.yml` fixtures.

## Permissions

- File mode 0600 (enforced on every write).
- Parent dir `~/.config/remo/` is 0700.
- If the file is found with mode > 0600 on read, Remo refuses to use it and prints "refusing to read nodes.yml with permissions wider than 0600 — run `chmod 0600 ~/.config/remo/nodes.yml`".

## Lifecycle

- Created lazily on first `remo {incus,proxmox} add-node`.
- Surviving file MUST be backward-compatible across patch releases of Remo; major-version bumps may require an in-code migration in `core.nodes._migrate_v<N>_to_v<N+1>()`.
- Removal is manual (no `--purge` flag in this release).
