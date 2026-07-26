"""Provider Protocol — uniform, entry-based surface (contracts/provider-protocol.md Part A).

Providers are free-function modules, not classes (research.md R1); mypy
supports modules as structural implementations of a ``typing.Protocol``.
Heterogeneous, CLI-facing verbs (create/destroy/update/extra commands) are
*not* part of this Protocol — they are contract-checked against descriptor
``OptionSpec`` declarations via ``inspect.signature`` instead (Part B).

Rules (R-A1..R-A5): success returns the annotated value; failure raises a
``core/errors.py`` taxonomy error — never ``sys.exit``, never bare
``RuntimeError``, never a returned exit code. ``update_entry``/snapshot verbs
receive a resolved registry entry; all name-format knowledge (host/container
split, vmid resolution, ...) lives inside the provider. ``teardown`` performs
provider destruction only — guard/cleanup/confirm/registry-removal are the
shared destroy template's job (core/lifecycle.py).
"""

from __future__ import annotations

from typing import Protocol

from remo_cli.core.reconcile import ProbeResult, SyncScope
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot


class Provider(Protocol):
    """Structural contract every registered provider module must satisfy."""

    def update_entry(self, entry: KnownHost, *, verbose: bool = False) -> None:
        """Re-apply tool configuration to an existing instance."""
        ...

    def teardown(self, entry: KnownHost, *, verbose: bool = False, **provider_opts: object) -> None:
        """Destroy the provider-side instance. No guard/confirm/registry work."""
        ...

    def probe(self, scope: SyncScope, **opts: object) -> ProbeResult:
        """Read-only discovery of provider-side instances in *scope* (Spec-016)."""
        ...

    def snapshot_create(
        self, entry: KnownHost, snapshot_name: str, *, description: str = ""
    ) -> None: ...

    def snapshot_restore(self, entry: KnownHost, snapshot_name: str) -> None: ...

    def snapshot_delete(self, entry: KnownHost, snapshot_name: str) -> None: ...

    def snapshot_list(self, entry: KnownHost) -> list[Snapshot]: ...
