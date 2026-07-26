# Contract: Shared Lifecycle Templates

Single implementations parameterized per provider (FR-013…FR-016). Providers/CLI MUST NOT re-implement these sequences.

**Transitional window**: between the CLI-generation swap (tasks T019–T021) and the template landing (T038), the generated `destroy`/`list` commands dispatch to the providers' legacy full-sequence functions. This is the sanctioned interim state; the MUST NOT applies from T038 onward and is enforced by the destroy-ordering tests and the final sweep (T051).

## Destroy template — `core/lifecycle.run_destroy` (FR-013)

Ordering is normative (Edge Case "Interrupted destroy" — identical failure-state behavior to today):

1. Validate/resolve target (registry entry when present; provider args otherwise).
2. Added-host guard: `type == "ssh"` for the name → `PreconditionError` (today's FR-012-016 guard, unchanged message).
3. Snapshot pre-cleanup via `core/snapshot.handle_destroy_snapshot_cleanup` (existing hook, unchanged).
4. Confirmation unless `auto_confirm`; decline → `UserAbortedError` (exit 3).
5. `provider.teardown(entry, **provider_opts)` — the only provider-specific step.
6. Best-effort `remove_known_host(...)` — failures warn, never fail the command (unchanged).

## Snapshot aggregation — `core/snapshot.list_all_snapshots` (FR-014)

`(type_name, lister: Callable[[KnownHost], list[Snapshot]]) -> tuple[list[Snapshot], bool]` — iterates the provider's registry slice, collects, records per-instance failures as warnings, returns `(snapshots, any_failure)`. Partial-failure exit behavior preserved (nonzero when any instance failed).

## Configure extra-vars — `core/ansible_runner.build_configure_extra_vars` (FR-015)

Returns the `-e` list for: `timezone=` (existing `detect_timezone()`), tool args (`build_tool_args(only, skip)`), `remo_version=`. Replaces all 8 inline copies; the *_site.yml and *_configure.yml paths both consume it.

## Resize helper — `core/ansible_runner.run_resize_playbook` (FR-016)

`(playbook: str, extra_vars: list[str], verbose: bool) -> None`; nonzero rc → `OperationFailedError`. Replaces the Incus/Proxmox private copies; available to any provider.

## Host list table — `core/output.render_host_table` (FR-016)

`(entries: list[KnownHost], columns: tuple[Column, ...]) -> None` with descriptor-declared columns. Formatting unified (allowed by FR-025); information content per provider preserved.
