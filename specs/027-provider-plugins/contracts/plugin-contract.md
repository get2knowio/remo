# Contract: out-of-tree provider distributions (PROVIDER_API_VERSION 1)

A provider distribution ships:

1. A **descriptor module** — metadata only, no SDK imports:
   ```python
   from remo_cli.core.provider_registry import ConnectionSpec, NameFormat, ProviderDescriptor, NAME
   REMO_PROVIDER_API_VERSION = 1
   DESCRIPTOR = ProviderDescriptor(
       type_name="acme", display_name="Acme", default_instance_name="acme",
       name_format=NameFormat.FLAT,
       registry_fields=(("instance_id", "box_id"), ("region", "zone")),
       connection=ConnectionSpec(),            # or proxy_hook="acme_provider.provider.ssh_proxy_hook"
       implementation="acme_provider.provider",
       create_options=(NAME,),
   )
   ```
2. An **implementation module** satisfying `remo_cli.core.provider_protocol.Provider` (`update_entry`, `teardown`, `probe`, `snapshot_create/restore/delete/list`) plus `create`/`destroy`/… entry points named by the descriptor; imported lazily on first use; raises `remo_cli.core.errors` types, never `sys.exit`.
3. An **entry point**: `[project.entry-points."remo.providers"] acme = "acme_provider.descriptor:DESCRIPTOR"` (a zero-arg factory is also accepted).

Discovery rules: built-ins first, then entry points sorted by name; first `type_name` wins; any load failure → one warning, skipped; `REMO_DISABLE_PROVIDER_PLUGINS=1` disables; API version mismatch/absence → warning, still registered. Pin the exact remo version you build against.

Visibility: `remo providers`; `remo web check` → `provider_plugins` line.

Registry: a plugin type's entries round-trip through `registry_fields`; on a machine without the plugin the entry is preserved verbatim and a warning names it.
