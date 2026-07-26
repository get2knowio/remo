"""Shared destroy template (contracts/lifecycle-templates.md).

``run_destroy`` is the single implementation of the guard -> snapshot
pre-cleanup -> confirm -> teardown -> registry-removal sequence. Providers
implement only ``teardown`` (Protocol Part A); everything else lives here.
"""

from __future__ import annotations

from collections.abc import Callable

from remo_cli.core.errors import ProviderError, UserAbortedError
from remo_cli.core.known_hosts import guard_not_added_ssh_host, remove_known_host
from remo_cli.core.output import confirm, print_error, print_info, print_warning
from remo_cli.core.snapshot import handle_destroy_snapshot_cleanup
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot


def run_destroy(
    entry: KnownHost,
    *,
    type_name: str,
    display_name: str,
    provider_label: str,
    teardown: Callable[[], None],
    list_snapshots: Callable[[], list[Snapshot]],
    delete_snapshot: Callable[[Snapshot], None],
    auto_confirm: bool,
    show_status: bool = False,
    location_suffix: str = "",
) -> None:
    """Destroy *entry*. Ordering is normative (Edge Case "Interrupted destroy").

    1. Added-host guard (``type == "ssh"``) -> ``PreconditionError``.
    2. Snapshot pre-cleanup (``core.snapshot.handle_destroy_snapshot_cleanup``).
    3. Confirmation unless *auto_confirm* -> ``UserAbortedError`` (exit 3) on decline.
    4. ``teardown()`` — the only provider-specific step.
    5. Best-effort registry removal — always runs, even if teardown failed;
       removal failures warn but never mask a real teardown failure.
    """
    guard_not_added_ssh_host(display_name, type_name)

    try:
        pre_destroy_snapshots = list_snapshots()
    except ProviderError as e:
        print_warning(
            f"Could not list snapshots before destroy ({e}); proceeding without snapshot cleanup."
        )
        pre_destroy_snapshots = []

    def _delete_one(snap: Snapshot) -> int:
        try:
            delete_snapshot(snap)
            return 0
        except ProviderError as e:
            print_error(str(e))
            return 1

    handle_destroy_snapshot_cleanup(
        provider_label=provider_label,
        instance=display_name,
        snapshots=pre_destroy_snapshots,
        delete_one=_delete_one,
        auto_confirm=auto_confirm,
        show_status=show_status,
    )

    if not auto_confirm:
        prompt = f"Destroy {provider_label} instance '{display_name}'{location_suffix}? This cannot be undone."
        if not confirm(prompt):
            raise UserAbortedError("Aborted.")

    print_info(f"Destroying {provider_label} instance '{display_name}'...")

    try:
        teardown()
    finally:
        try:
            remove_known_host(type_name, entry.name)
        except Exception as e:  # noqa: BLE001 — best-effort, never masks teardown's own error
            print_warning(f"Could not remove '{entry.name}' from the registry: {e}")
