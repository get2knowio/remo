"""Cross-provider snapshot record.

The :class:`Snapshot` dataclass is the unified view of a point-in-time
capture across all four providers. It is constructed from provider-native
data at list/create/restore/delete time; remo does not persist snapshot
records (the provider is the system of record).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class SnapshotStatus(str, Enum):
    """Cross-provider snapshot status.

    Mapped from each provider's native states:
      * Incus / Proxmox — always :attr:`AVAILABLE` (creation is synchronous).
      * AWS — pending/creating → :attr:`PENDING`; completed → :attr:`AVAILABLE`;
        error → :attr:`FAILED`.
      * Hetzner — status=creating → :attr:`PENDING`; status=available →
        :attr:`AVAILABLE`; status=failed → :attr:`FAILED`.
    """

    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"


@dataclass(frozen=True)
class Snapshot:
    """A point-in-time capture of an instance's primary storage."""

    provider: str               # "incus" | "proxmox" | "aws" | "hetzner"
    instance_name: str          # user-facing remo instance name
    name: str                   # user-facing snapshot name
    backend_id: str             # provider-native id (snap-xxx, image id, etc.)
    created_at: datetime        # UTC
    size_bytes: int | None      # None when the provider doesn't report it
    description: str            # may be empty
    status: SnapshotStatus
