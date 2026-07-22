# Phase 1 Data Model: Managed-Instance Tagging & Filtered Sync

This feature adds provider-side metadata and a small amount of in-memory
sync-time state. It introduces **no** new persisted local entity and does not
change the `KnownHost` registry representation (FR-012, out-of-scope: registry
does not record marker state).

## Entity: Managed Marker (provider-side, authoritative)

A fixed, built-in piece of container metadata indicating the container was
created and is managed by `remo`. Not user-configurable (clarified).

| Provider | Form | Key/Value | Namespace safety |
|----------|------|-----------|------------------|
| Incus    | config key | `user.remo` = `true` | `user.*` is reserved for user metadata; cannot collide with Incus keys |
| Proxmox  | guest tag  | bare tag `remo` in the tag set | tag is one member of a set; other tags preserved |

**Constants** (single source, `core/config.py`):
- `INCUS_MANAGED_CONFIG_KEY = "user.remo"`
- `INCUS_MANAGED_CONFIG_VALUE = "true"`
- `PROXMOX_MANAGED_TAG = "remo"`

**Lifecycle / state transitions**:

```
unmarked ──create()──▶ marked        (FR-001: applied at provision time)
unmarked ──update()──▶ marked        (FR-004: backfill; idempotent)
marked   ──create()/update()──▶ marked   (FR-002: no-op re-apply)
marked   ──user removes tag/key manually──▶ unmarked  (Edge Case: intentional "unmanage"; sync does not re-add)
```

`sync` never transitions this state (FR-010: read-only on container state).

**Validation / invariants**:
- **Idempotency (FR-002)**: re-applying MUST NOT alter any other config.
  - Incus: `incus config set <name> user.remo=true` on an already-set key is a
    no-op by construction.
  - Proxmox: apply only writes when `remo ∉ tags`; otherwise it is skipped, so
    the tag list is never reordered (SC-005).
- **Tag preservation (FR-003)**: Proxmox apply computes `new = existing ∪
  {remo}` and writes `;`-joined; no existing tag removed or altered.
- **Apply-failure tolerance (FR-005)**: a failed apply during `create`/`update`
  warns but does not, by itself, fail the command when the container was
  otherwise created/configured.

## In-memory value: Discovered Container (sync-time, transient)

Produced while scanning a host; never persisted with marker state.

| Field | Type | Source (Incus) | Source (Proxmox) |
|-------|------|----------------|------------------|
| `name` | str | col `n` of `incus list` | Name column of `pct list` |
| `vmid` | str | (n/a) | VMID column of `pct list` |
| `marked` | bool | col `user.remo` == `true` | `remo ∈` tags from `/etc/pve/lxc/<vmid>.conf` |

**Sync selection rule**:
- Default (`all=False`): register iff `marked` is true. Collect names of
  `marked == False` into a `skipped` list for the hint.
- `--all` (`all=True`): register every discovered container; collect names of
  `marked == False` into an `adopted_unmarked` list for the summary.

## Entity: KnownHost (existing — unchanged)

The registry line format is untouched (FR-012). For reference, marker state is
**not** a field here:
- Incus: `name = "<host>/<container>"`, `instance_id = <host-user>`.
- Proxmox: `name = "<node>/<container>"`, `instance_id = <vmid>`, `region =
  <ssh-user>`.

## Relationships

```
KnownHost (registry, connection-only)
    │  1:1 by name
    ▼
Container (provider-side)
    │  carries 0..1
    ▼
Managed Marker  ── authoritative on provider; drives sync inclusion
```
