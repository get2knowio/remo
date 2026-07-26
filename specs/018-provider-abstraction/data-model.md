# Data Model: Formal Provider Abstraction

**Feature**: 018-provider-abstraction | **Date**: 2026-07-26

All entities are in-process Python structures — this feature persists nothing new (registry format v2 is frozen; FR-025). Types below are normative for the plan; exact field ordering may shift during implementation without spec impact.

## ProviderDescriptor (`core/provider_registry.py`)

Frozen dataclass; pure metadata, no SDK imports (FR-024).

| Field | Type | Notes |
|-------|------|-------|
| `type_name` | `str` | Registry key and CLI group name (`"incus"`, `"aws"`, …). Unique (FR-007). |
| `display_name` | `str` | Human label for help/tables (`"Incus"`). |
| `default_instance_name` | `DefaultName` | `Literal value ("dev1", "remo")` or the `LOGIN_USER` sentinel (AWS). Surfaced in generated help (FR-011). |
| `name_format` | `NameFormat` | `FLAT` \| `HOST_SCOPED` (`host/container`). Drives completion, sync scoping, lookup handling. |
| `registry_fields` | `tuple[str, ...]` | Which `KnownHost` fields serialize for this type (drives `core/registry.py` v2 per-type map). |
| `connection` | `ConnectionSpec` | Direct SSH vs proxy-hooked (SSM); see below. |
| `sdk_extra` | `str \| None` | Optional extra name (`"aws"`→boto3, `"hetzner"`→hcloud) for `MissingDependencyError` messaging. |
| `implementation` | `str` | Dotted module path (`"remo_cli.providers.aws"`); imported lazily on first verb execution. |
| `create_options` | `tuple[OptionSpec, ...]` | Drawn from the shared catalog + provider-specific specs. |
| `update_options` | `tuple[OptionSpec, ...]` | Same. |
| `destroy_options` | `tuple[OptionSpec, ...]` | Provider extras only (e.g. Proxmox `--purge`); `--yes/-y` is injected by the factory uniformly (FR-012). |
| `sync_options` | `tuple[OptionSpec, ...]` | Provider scope options for `sync` (incus: host/user/use-ip; aws: region; `--all` where Spec-016 defined it); `--yes`/`--dry-run` injected uniformly. |
| `info_options` | `tuple[OptionSpec, ...]` | Provider extras for the shared `info` command (incus/proxmox: host/user; aws/hetzner: none). |
| `snapshot_region_scoped` | `bool` | AWS: snapshot commands accept `--region`. |
| `extra_commands` | `tuple[CommandSpec, ...]` | Provider-specific commands (AWS `stop/start/reboot/info`, Incus/Proxmox `bootstrap`). |
| `deprecated_options` | `tuple[DeprecatedOption, ...]` | e.g. create `--yes` (one-release window, FR-010). |

**Validation rules**: `type_name` nonempty, lowercase, unique in registry; `implementation` importable (checked by conformance test, not at registration); option names within a command unique.

## OptionSpec / CommandSpec / DeprecatedOption (`core/provider_registry.py`)

- **OptionSpec**: `name` (`"--volume-size"`), `param` (kwarg name, must match the impl function's signature — conformance-checked), `short` (`"-v"` etc.), `type` (click type/choices), `default`, `required`, `help`, `completion` (`NONE | INSTANCE_NAME`). The **shared catalog** defines one canonical instance per shared option (HOST, USER, DOMAIN, IMAGE, CORES, MEMORY, VOLUME_SIZE, ONLY, SKIP, USE_IP, DEVCONTAINER_RUNTIME, REGION, LOCATION, VERBOSE, …) so identical flags are identical objects (SC-002).
- **CommandSpec**: `name`, `help`, `options: tuple[OptionSpec, ...]`, `impl` (function name in the provider module), `confirmable: bool` (injects `--yes/-y`).
- **DeprecatedOption**: `name`, `notice`, `removal_release`.

## Provider registry (`core/provider_registry.py`)

Module-level mapping `type_name -> (descriptor, memoized implementation)`.

| Operation | Behavior |
|-----------|----------|
| `register(descriptor)` | Adds; raises `ValueError` on duplicate `type_name` (FR-007). |
| `get_descriptor(type_name)` | Returns descriptor; raises `UnknownProviderError` (a `PreconditionError`) naming the type (FR-006). |
| `get_provider(type_name)` | Lazy-imports `implementation`, memoizes, returns module. `ImportError` of an optional SDK → `MissingDependencyError` naming `sdk_extra`. |
| `all_descriptors()` | Registration order; used by `cli/main.py` to mount groups and by conformance tests. |
| `is_provider_type(type_name)` | `False` for `"ssh"`/unknown — used by explicit-exclusion sites. |
| `temporary_registration(descriptor)` | Context manager: registers on enter, unregisters on exit. Test-only affordance (FakeProvider isolation); production code never unregisters. |

**Lifecycle**: `providers/builtin.py` registers the four built-ins at import. To keep every entry point safe (CLI, `remo web serve`, tests importing `core/registry.py` after T048), the registry **lazily auto-imports `remo_cli.providers.builtin` on first lookup** (`get_descriptor`/`all_descriptors`/`is_provider_type`); explicit entry-point imports become an optimization, not a correctness requirement. The lazy import is by dotted name at call time, so the static core→providers layering rule is preserved. The `ssh` pseudo-type is never registered (explicit exclusion, FR-006).

## Provider protocol (`core/provider_protocol.py`)

`typing.Protocol` satisfied by each provider *module* (mypy modules-as-protocols). Uniform, entry-based surface only; heterogeneous verbs (create/destroy/update CLI kwargs) are contract-checked against descriptor `OptionSpec.param`s via `inspect.signature` instead.

| Member | Signature | Notes |
|--------|-----------|-------|
| `update_entry` | `(entry: KnownHost, *, verbose: bool = False) -> None` | Absorbs per-provider name-splitting; used by `remo shell` update path. |
| `teardown` | `(entry: KnownHost, *, verbose: bool = False, **provider_opts) -> None` | Called by the shared destroy template after guard/cleanup/confirm. |
| `probe` | `(scope: SyncScope, **opts) -> ProbeResult` | Existing Spec-016 seam, made public/uniform. |
| `snapshot_create` | `(entry: KnownHost, snapshot_name: str) -> None` | |
| `snapshot_restore` | `(entry: KnownHost, snapshot_name: str) -> None` | |
| `snapshot_delete` | `(entry: KnownHost, snapshot_name: str) -> None` | |
| `snapshot_list` | `(entry: KnownHost) -> list[Snapshot]` | Public on all four (today private on Incus/Proxmox — FR-017). |

All raise `ProviderError` subclasses on failure; none return exit codes; none call `sys.exit` (FR-002).

## Error taxonomy (`core/errors.py`)

| Class | Parent | `exit_code` | Raised for |
|-------|--------|-------------|-----------|
| `ProviderError` | `Exception` | 1 | Base; user-facing `message` |
| `MissingDependencyError` | `ProviderError` | 1 | Optional SDK absent; names the extra |
| `PreconditionError` | `ProviderError` | 1 | Validation, not-found, wrong state, added-host guard, unknown provider type |
| `OperationFailedError` | `ProviderError` | 1 | Subprocess/playbook/API failure; carries underlying rc/detail |
| `UserAbortedError` | `ProviderError` | 3 | Declined confirmation |

**State transition**: raised in providers/core templates → caught once by the CLI factory's `provider_command` wrapper → `print_error` + `sys.exit(exit_code)`. The web service catches `ProviderError` instead of today's mixed rc/RuntimeError handling.

## ConnectionSpec (`core/provider_registry.py`)

| Field | Type | Notes |
|-------|------|-------|
| `mode_field_aware` | `bool` | Whether `KnownHost.access_mode` selects behavior (AWS). |
| `proxy_hook` | `str \| None` | Dotted path to `(host: KnownHost) -> SshProxyPlan \| None`; AWS's builds the SSM ProxyCommand + `user@instance_id` target (absorbs `get_aws_region`, FR-018). `None` ⇒ direct SSH always. |

`SshProxyPlan`: `proxy_command: str`, `ssh_target: str`, `extra_opts: tuple[str, ...]`.

## DiscoveredHost — extended (`core/reconcile.py`)

Existing frozen dataclass gains one field (FR-019, #87):

| Field | Type | Notes |
|-------|------|-------|
| `observed` | `frozenset[str] \| None = None` | Field names the provider actually observed. `None` ⇒ legacy semantics (all non-empty fields observed) — Incus/Proxmox/Hetzner probes unchanged. |

**Merge rule** (`merge_entry`): for each mergeable field — take `discovered.<f>` iff `f` observed and value non-empty, else keep `existing.<f>`. **Additions** (no existing entry) always use the discovered entry wholesale, defaults included. AWS probe: `access_mode` ∈ observed iff the `remo_access_mode` tag exists.

## Test-only entities

- **FakeProvider** (`tests/…/fake_provider.py`): minimal descriptor + in-memory module satisfying the Protocol; registered in a fixture. Proves SC-001 (full command group appears with zero existing-file edits) and exercises conformance generically.
- **Conformance suite**: parametrized over `all_descriptors()` + FakeProvider — asserts Protocol satisfaction (runtime attr/signature checks), descriptor↔signature agreement for create/update/destroy/extra commands, and error-contract behavior (no `SystemExit` escapes).

## Deleted / superseded

- `cli/providers/{incus,hetzner,aws,proxmox}.py` → generated by factory (FR-008).
- `core/completion.py` → generic completer from `name_format` (R7).
- Per-provider `_run_resize_playbook`, inline extra-vars blocks, four `list_hosts()` table renderers, four destroy skeletons → shared templates (FR-013…FR-016).
