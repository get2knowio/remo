# Provider plugins

remo's providers are declared, not special-cased: every provider is a
metadata-only **descriptor** registered with `core/provider_registry.py`, and
the CLI groups, `remo shell`, completion, snapshots, the registry and the web
console all operate on descriptors. Since spec 027 that registration is open to
**any installed Python distribution** through the `remo.providers` entry-point
group — no edit to remo is needed to add a provider.

Provider API version: **1** (`remo_cli.core.provider_registry.PROVIDER_API_VERSION`).
Compatibility across remo versions is not promised: pin the exact remo version
your plugin was built against.

## What a provider distribution contains

```
acme-provider/
├── pyproject.toml
└── src/acme_provider/
    ├── __init__.py
    ├── descriptor.py     # metadata only — no SDK imports
    └── provider.py       # the implementation, imported lazily on first use
```

### 1. The descriptor module (metadata only)

```python
# src/acme_provider/descriptor.py
from remo_cli.core.provider_registry import (
    NAME, REGION, ConnectionSpec, NameFormat, ProviderDescriptor,
)

REMO_PROVIDER_API_VERSION = 1   # the API this plugin targets

DESCRIPTOR = ProviderDescriptor(
    type_name="acme",                       # lowercase; the CLI group name and registry `type`
    display_name="Acme",
    default_instance_name="acme1",
    name_format=NameFormat.FLAT,            # or HOST_SCOPED for "host/container" names
    registry_fields=(("instance_id", "box_id"), ("region", "zone")),
    connection=ConnectionSpec(),            # see "SSH connections" below
    implementation="acme_provider.provider",
    create_options=(NAME, REGION),
    sync_options=(REGION,),                 # declares `--region` for `remo acme sync`
    region_scoped_sync=True,                # sync scopes by region (like AWS)
)
```

Rules the built-ins follow and plugins must too:

- **No SDK imports in the descriptor module.** It is imported at discovery
  time by every remo command, including `--help` and shell completion.
- `registry_fields` maps `KnownHost` attributes (`instance_id`, `region`) to
  the nested JSON keys under `registry.json`'s `"<type_name>": {...}` block.
  The registry serializes **and parses** through this declaration, so your
  entries round-trip on any machine that has the plugin installed. On a machine
  without it, the entry is preserved verbatim and reported, never dropped.
- `registry_legacy_keys` (optional) lists `(old_json_key, attribute)` pairs to
  read when the current key is absent — a lazy key migration.
- `ConnectionSpec(mode_field_aware=True)` opts the provider into the
  `access: ssm` handling; `proxy_hook="acme_provider.provider.ssh_proxy_hook"`
  names a function `(KnownHost) -> SshProxyPlan | None` that returns an SSH
  `ProxyCommand` plan (see `providers/aws.py:ssh_proxy_hook` for the shape).
- `sync_scope_description` (optional) is a `str.format` template with `{host}`
  / `{region}` used by `remo <type> sync`'s plan header.
- Shared options (`NAME`, `HOST`, `REGION`, `VOLUME_SIZE`, …) come from the
  catalog in `core/provider_registry.py`; reuse them so flags stay identical
  across providers (`dataclasses.replace` for a different default).

### 2. The implementation module

Imported lazily by `get_provider()` the first time a verb needs it. It must
satisfy `remo_cli.core.provider_protocol.Provider` — `update_entry`,
`teardown`, `probe`, `snapshot_create/restore/delete/list` — plus the
functions the generated verbs call: `create`, `destroy` (via `teardown`),
`upgrade`, `resize`, `list_hosts`, `info`, `sync` (via `probe`), and any
`CommandSpec.impl` you declare. Raise `remo_cli.core.errors` types
(`PreconditionError`, `OperationFailedError`, `MissingDependencyError`,
`UserAbortedError`); never call `sys.exit`. Registry writes go through
`core.known_hosts` / `core.registry`.

If your implementation needs an optional SDK, importing it lazily inside the
implementation module is enough: an `ImportError` there becomes a
`MissingDependencyError` naming `sdk_extra` when set.

### 3. The entry point

```toml
# pyproject.toml
[project.entry-points."remo.providers"]
acme = "acme_provider.descriptor:DESCRIPTOR"
```

The object may also be a zero-argument callable that returns the descriptor.

## Discovery rules

- Built-ins register first (incus, proxmox, aws, hetzner), then entry points
  in **entry-point-name order**, once per process.
- A plugin that fails to load (import error, wrong object type, descriptor
  validation error) is **skipped with one warning** naming the distribution
  and entry point. remo keeps working; the command's exit code is unaffected.
- A duplicate `type_name` (against a built-in or an earlier plugin) keeps the
  **first** registration and skips the later one with a warning naming both
  distributions.
- A different or missing `REMO_PROVIDER_API_VERSION` warns and still registers.
- `REMO_DISABLE_PROVIDER_PLUGINS=1` skips entry-point discovery entirely.

## Seeing what was discovered

```bash
remo providers          # TYPE / NAME / SOURCE / STATUS, plus skipped plugins with reasons
remo web check          # a `provider_plugins` line (always PASS; lists loaded and skipped)
```

## The web console

The console's OpenAPI contract enumerates only the built-in types
(`KnownProviderType`); a plugin's instances are served with their type as a
plain string and the console presents them through its runtime fallback. That
is by design (spec 020 FR-004a): installing a plugin must never change the
generated artifacts.

## Testing your plugin

The in-repo fixture distribution `tests/fixtures/remo_fixture_provider/` is a
complete, minimal example (descriptor + implementation + entry point); remo's
own suite installs it through the `dev` dependency group (`uv sync`) and runs
`tests/integration/test_fixture_plugin_e2e.py` against it. Copy it, rename the
type, and start from there.
