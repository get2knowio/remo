# Phase 1 Data Model: Provider Snapshots

**Date**: 2026-05-24

## Entities

### Snapshot (`src/remo_cli/models/snapshot.py` — NEW)

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SnapshotStatus(str, Enum):
    """Cross-provider snapshot status.

    Mapped from each provider's native states:
      - Incus / Proxmox: always AVAILABLE (creation is synchronous).
      - AWS: pending → AVAILABLE; completed → AVAILABLE; error → FAILED.
      - Hetzner: status=creating → PENDING; status=available → AVAILABLE; status=failed → FAILED.
    """
    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"


@dataclass(frozen=True)
class Snapshot:
    """Cross-provider snapshot record.

    Constructed from provider-native data at list/create/restore/delete time.
    Not persisted by remo — the provider is the system of record.
    """

    provider: str              # "incus" | "proxmox" | "aws" | "hetzner"
    instance_name: str         # user-facing remo instance name
    name: str                  # user-facing snapshot name
    backend_id: str            # provider-native id (snap-xxx, image id, etc.)
    created_at: datetime       # UTC
    size_bytes: int | None     # None when the provider doesn't report it
    description: str           # may be empty
    status: SnapshotStatus
```

**Why frozen**: Snapshots are immutable from remo's perspective. We construct them, display them, and pass them to provider calls. We never mutate.

**Why no `cost_per_month`**: Removed via Q4 clarification — remo does not estimate costs.

**Why `size_bytes: int | None`**: Incus reports size in JSON; Proxmox does not report a per-snapshot byte size in `pct listsnapshot` or the conf file (LXC snapshots are CoW and don't have a single billable size). AWS reports `VolumeSize` (GiB) which we convert to bytes. Hetzner reports `disk_size` and `image_size` in GB; we use `image_size` and convert.

## Provider-side identity scoping (FR-027)

Each provider has a stable "source identity" that we use to scope snapshots to their parent instance. This is **not** stored in the Snapshot dataclass — it's used at query time to filter the provider response:

| Provider | Source identity | Lookup |
|---|---|---|
| Incus | container name (intrinsic; snapshots are namespaced under the container) | Query `/1.0/instances/<container>/snapshots`. |
| Proxmox | VMID (intrinsic; snapshots are namespaced under the VMID) | Query `/etc/pve/lxc/<vmid>.conf`. |
| AWS | root EBS volume ID | `describe-snapshots(Filters=[{Name: "volume-id", Values: [<root-vol>]}, {Name: "tag:remo", Values: ["true"]}])`. |
| Hetzner | source server ID | `images.get_all(type=[SNAPSHOT], label_selector=f"remo=true,remo-source-server-id={server.id}")`. |

After an instance is destroyed:
- Incus / Proxmox: snapshots are auto-removed with the container (provider-enforced).
- AWS: the volume is deleted but snapshots persist; the volume ID no longer maps to any instance, so `describe-snapshots` filtered by that volume-id still returns the orphans — but remo has no instance to query "current volume ID" for, so the orphans become unreachable through remo.
- Hetzner: the server is deleted but snapshot images persist with `remo-source-server-id=<old-id>`; same orphan behavior.

This is the desired outcome per spec FR-027 / Q2.

## State transitions

```text
                       (provider accepts request)
                       v
                  ┌──────────┐    (AWS/Hetzner finish)    ┌────────────┐
   create  ─────> │ PENDING  │ ─────────────────────────> │ AVAILABLE  │ ─┐
                  └──────────┘                            └────────────┘  │
                       │                                                  │
                       │ (provider reports error)                         │
                       v                                                  │ delete
                  ┌──────────┐                                            │
                  │  FAILED  │                                            │
                  └──────────┘                                            v
                                                                  ┌──────────────┐
                                                                  │ (removed)    │
                                                                  └──────────────┘
```

- Incus / Proxmox transition straight to AVAILABLE on successful `create`.
- AWS / Hetzner start as PENDING; user calls `list` to see the transition to AVAILABLE.
- `restore` and `delete` require AVAILABLE (FR-028).

## Validation rules

Implemented in `src/remo_cli/core/snapshot.py`:

| Rule | Applied at | Action on violation |
|---|---|---|
| Name length 1–40 chars | `create` (CLI parse) | Click error, exit 2 |
| Name matches `^[A-Za-z0-9][A-Za-z0-9_-]*$` | `create` (CLI parse) | Click error, exit 2 |
| Name not already in use for this instance | `create` (after listing) | Print error, exit 1 (FR-006) |
| Snapshot status == AVAILABLE | `restore`, `delete` | Print error, exit 1 (FR-028) |
| Storage backend supports snapshots | Proxmox `create` | Print error naming the storage and supported alternatives, exit 1 (FR-005) |

## Relationships to existing entities

`Snapshot` references `KnownHost` (the parent instance) only by `instance_name` (string). There is no foreign-key-like binding because:

1. Snapshots live on the provider, not in remo's registry.
2. The provider-side identity scoping (volume ID, server ID, container/VMID) is the authoritative parent link; the `instance_name` is a convenience for display.

`KnownHost` is **not modified** by this feature. No new fields, no new files, no migration. The existing `instance_id` field (used for AWS instance ID and Proxmox VMID) is sufficient for snapshot operations.
