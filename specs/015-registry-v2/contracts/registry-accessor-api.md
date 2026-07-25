# Contract: Registry Accessor API (`remo_cli.core.registry`)

The single module owning parse/serialize/validate/lock/migrate for the registry (FR-012). All consumers — CLI, providers, web service — go through this surface. Signatures are the contract; bodies are implementation.

## Types

```python
@dataclass(frozen=True)
class RegistryView:
    hosts: list[KnownHost]        # parsed, known-type entries (in-memory model unchanged)
    warnings: list[str]           # per-entry tolerant-read diagnostics (FR-014, FR-025)
    source_format: str            # "v2" | "legacy" | "empty"
    unknown_entries: int          # count of preserved unknown-type entries

class RegistryError(Exception): ...            # base — accessor NEVER raises SystemExit (FR-013)
class RegistryReadError(RegistryError): ...    # unreadable file / invalid top-level document
class RegistryValidationError(RegistryError): ...  # V1–V6 failed; names field + entry
class RegistryBusyError(RegistryError): ...    # lock not acquired within timeout (FR-017)
class RegistryNewerVersionError(RegistryError): ...  # file version > supported (FR-023)
```

## Read

```python
def read_registry(*, readonly: bool = False) -> RegistryView
```

- `readonly=True` (web service, `remo web check`): never creates directories or files, never migrates, never locks; reads `registry.json` if present else legacy `known_hosts` in place; per-entry problems become `warnings`, structural problems raise `RegistryReadError` / `RegistryNewerVersionError` (caller decides — the web service maps them to the `broken` state).
- `readonly=False` (CLI default): identical read semantics PLUS triggers migration when the source is legacy (see below). Uses the mkdir-capable path helpers.
- Never returns partial torn state: reads see complete old or complete new content (writes are `os.replace`-atomic, FR-018).

## Write

```python
def mutate_registry(mutator: Callable[[list[KnownHost]], list[KnownHost]]) -> RegistryView
```

The ONLY write primitive. Sequence: acquire lock (≤ 5 s, else `RegistryBusyError`) → read current state (migrating first if legacy) → apply `mutator` → validate all entries (V1–V6; on failure `RegistryValidationError`, disk untouched) → serialize (sorted, deterministic) → atomic write → release lock. Unknown-type entries pass through untouched (mutator never sees them; they are re-emitted verbatim).

```python
def replace_registry(hosts: list[KnownHost], *, allow_empty: bool = False) -> RegistryView
```

Wholesale replacement (web setup PUT apply). Same lock/validate/atomic pipeline; refuses empty without `allow_empty` (mirrors existing setup guard).

## Migration

```python
def migrate_if_needed() -> MigrationReport | None   # called internally by non-readonly paths
```

- CLI-only trigger (clarification #1); no-op when `registry.json` exists (idempotent, FR-010).
- Under lock: re-check → write v2 → rename legacy to `known_hosts.v1.bak` (non-clobbering suffixes) → report.
- `MigrationReport`: entries migrated, backup path, skipped lines (verbatim), push-cache-reset notice flag. The CLI prints it in plain language (FR-025); the accessor itself never prints.
- Both-present resolution per data-model §6: equivalent → complete rename silently; divergent → warning in `RegistryView.warnings`, v2 wins, never merge (FR-024).

## Locking

```python
@contextmanager
def registry_lock(timeout_s: float = 5.0): ...
```

`fcntl.flock` on `${REMO_HOME}/registry.lock`; `RegistryBusyError` on timeout; one-time warning + unlocked fallback where flock is unsupported (FR-019). Exposed for multi-step callers (migration); ordinary writers just use `mutate_registry`.

## Compatibility delegates (unchanged public surface, FR-015)

`core/known_hosts.py` keeps its public functions as thin delegates so existing call sites in `providers/*` and `cli/*` are untouched this feature:

| Existing function | Delegates to |
|-------------------|--------------|
| `get_known_hosts()` | `read_registry().hosts` |
| `save_known_host(h)` | `mutate_registry(upsert by (type, name))` |
| `remove_known_host(t, n)` | `mutate_registry(drop by (type, name))` |
| `clear_known_hosts_by_type(t)` / `_by_prefix(p)` | `mutate_registry(filter)` |
| `resolve_remo_host_by_name` / `guard_not_added_ssh_host` / `get_aws_region` | unchanged logic over `read_registry().hosts` (their `SystemExit` behavior is unchanged this feature — they are CLI-boundary helpers; the accessor beneath them never exits) |

Deleted (parser duplication collapse, FR-012 / SC-003):
- `web/discovery.py:_read_known_hosts_readonly` → `read_registry(readonly=True)`
- `web/api/setup.py:_read_registry_readonly` → `read_registry(readonly=True)`
- `core/known_hosts.py` line-parsing internals → the accessor's legacy codec (single implementation, shared with migration and payload-v1 mapping)

## Guarantees summary

| Guarantee | Mechanism |
|-----------|-----------|
| No torn reads | same-dir temp file + `os.replace` |
| No lost updates | all writes inside `registry_lock` RMW |
| No side effects in readonly mode | readonly path uses no-mkdir helpers, no lock file, no migration |
| No process termination from the accessor | error taxonomy instead of `SystemExit` |
| Unknown content survives | unknown-type entries round-trip verbatim |
| Deterministic files | sorted entries, sorted keys, fixed indent |
