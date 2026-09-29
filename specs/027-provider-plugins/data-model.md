# Data Model: Provider Plugin Discovery

## E1. `ProviderDescriptor` additions (`core/provider_registry.py`)
| Field | Type | Default | Meaning |
|---|---|---|---|
| `registry_legacy_keys` | `tuple[tuple[str, str], ...]` | `()` | `(legacy_json_key, attr)` read on parse when `attr` is empty (Proxmox: `("node_user", "region")`) |
| `region_scoped_sync` | `bool` | `False` | sync scope matches by region (AWS) |
| `sync_scope_description` | `str \| None` | `None` | `str.format` template with `{host}`/`{region}` for `SyncScope.describe()` |

## E2. `PluginLoadRecord` (`core/provider_plugins.py`, frozen dataclass)
`distribution: str`, `version: str`, `entry_point: str`, `type_name: str | None`, `status: Literal["loaded","skipped"]`, `reason: str` (empty when loaded), `api_version: int | None`.

## E3. Registry accessors
`PROVIDER_API_VERSION = 1`; `builtin_descriptors() -> tuple[ProviderDescriptor, ...]`; `descriptor_source(type_name) -> str` (`"builtin"` or `"<dist> <version>"`); `plugin_load_records() -> tuple[PluginLoadRecord, ...]`; `reset_discovery_for_tests()`.

## E4. Entry-point contract
Group `remo.providers`; object = `ProviderDescriptor` or zero-arg callable returning one; module attribute `REMO_PROVIDER_API_VERSION: int`.

## E5. Disable switch
`REMO_DISABLE_PROVIDER_PLUGINS` ∈ {`1`,`true`,`yes`} (case-insensitive) → discovery skipped; records empty; `remo providers` says so.

## E6. Registry v2 (unchanged on disk)
Provider entries: `{type,name,host,user,access,<type>:{registry_fields keys}}`. Parse: reverse map + legacy keys. Unknown type → `unknown_raw`, preserved verbatim, warned.
