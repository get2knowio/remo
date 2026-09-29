# Research: Provider Plugin Discovery

Grounded against `main` @ `e680e26` on 2026-09-29 (direct reads; line numbers as of that commit).

## R1. Where discovery hooks in
`core/provider_registry.py:_ensure_builtins_imported()` (line ~234) is the one-shot gate called by `get_descriptor`/`get_provider`/`all_descriptors`/`is_provider_type`. **Decision**: rename to `_ensure_discovered()`: import `remo_cli.providers.builtin` (unchanged order incus, proxmox, aws, hetzner — `providers/builtin.py`), record the four as source `builtin`, then call `core/provider_plugins.discover_entry_points()` unless disabled. Keep `_ensure_builtins_imported` as an alias for any external caller.

## R2. Entry-point loading
`importlib.metadata.entry_points(group="remo.providers")` (Python ≥ 3.10 selectable API). Sort by `(ep.name, ep.value)`; distribution name/version via `ep.dist.name`/`ep.dist.version` (may be `None` for synthetic entry points → "unknown"). Load: `obj = ep.load()`; if callable and not a `ProviderDescriptor`, call it with no args; result must be a `ProviderDescriptor` else skip with "expected ProviderDescriptor, got <type>". `register()` raises `ValueError` on duplicates → catch, record skipped with "duplicate type_name 'x' already registered by <dist>". Every exception subclass of `Exception` is caught; `BaseException` is not. **API version**: `ep.module` → `importlib.import_module(ep.module)` → `getattr(mod, "REMO_PROVIDER_API_VERSION", None)`; compare to `PROVIDER_API_VERSION = 1`; mismatch/absent → warning, continue.

## R3. Warning channel
`core/output.print_warning` prints yellow to stdout (all CLI warnings do). Decision: use it (consistent, testable via capsys); the prompt's "stderr" is noted as a follow-up for all warnings together (issue). Warnings are emitted once per process (discovery is one-shot).

## R4. Registry parse (`core/registry.py`)
- `KNOWN_TYPES` (line 37) gates `entry_to_known_host` (215), `legacy_fields_to_entry` (276), `_parse_legacy_lines` (431), `_parse_v2` (506). Replace with `is_known_type(type_) = type_ == "ssh" or is_provider_type(type_)` (lazy import of provider_registry inside functions, as `_provider_nested_fields` already does).
- Parse branches (221–241) → generic: `nested = entry.get(type_)`; for `(attr, key)` in `descriptor.registry_fields`: `values[attr] = str(nested.get(key, "") or "")`; then for `(legacy_key, attr)` in `descriptor.registry_legacy_keys`: if `values[attr]` empty and `nested.get(legacy_key)`: use it. Proxmox declares `registry_legacy_keys=(("node_user", "region"),)`. Only `instance_id`/`region` attrs exist today (as in serialize).
- Access rules: `legacy_fields_to_entry` line 290 and `_validate_single_host` line 368 → `_mode_field_aware(type_)`; validation message: `f"access 'ssm' is only valid for type {aware!r}"` where `aware` is the sorted list of aware type names joined — for builtins that is `'aws'`, byte-identical to today's `"access 'ssm' is only valid for type 'aws'"`.
- Unknown types: `_parse_v2` keeps `unknown_raw` (already preserved on write, `_write_v2_file` 524–525) and additionally appends a warning `f"entry {name!r} has type {type_!r} with no installed provider; preserved verbatim"`. `test_unknown_entry_survives_a_write_it_never_saw` already exists; add the plugin-flavoured round-trip.

## R5. Sync scope (`core/reconcile.py` 74–121)
`_requires_region` already keys off `--region` in `sync_options`. Replace `self.type == "aws"` (lines 101, 109, 115) with `descriptor.region_scoped_sync`; `describe()` uses `descriptor.sync_scope_description.format(host=…, region=…)` when set, else the existing generic string. Descriptor declarations: aws `region_scoped_sync=True, sync_scope_description="aws region {region}"`; incus `"incus host {host} (default project)"`; proxmox `"proxmox node {host} (this node only)"`; hetzner none.

## R6. Display names
`models/host.py:150–161` and `providers/added.py:80`. `core/known_hosts._is_host_scoped_type` (line 24) already exists. Add `display_name_for(host: KnownHost) -> str` in `core/known_hosts.py`; `KnownHost.display_name()` becomes a lazy-import shim (docstring says why: models stays import-free of core); `providers/added.py` uses `_is_host_scoped_type`. No other callers of `display_name()` exist in src/tests.

## R7. Visibility
- `remo providers` (new `cli/plugins.py`, command name `providers`): table of `type | display name | source | status`, then a "skipped" section. Registered in `cli/main.py` after `web`/`connector`; `EXPECTED_COMMANDS` gains `providers` plus the fixture's `fixture` group (present iff the plugin is installed — the test computes it from `plugin_load_records()`).
- `web/check.py::run_checks`: append `_provider_plugins_check()` (always `passed=True`; detail "N plugin(s) loaded: …; M skipped: …"; remediation when skipped). Placed after the executable check in both the unconfigured and configured branches.

## R8. Fixture plugin
`tests/fixtures/remo_fixture_provider/` with `pyproject.toml` (hatchling, name `remo-fixture-provider`, version 0.1.0, `[project.entry-points."remo.providers"] fixture = "remo_fixture_provider.descriptor:DESCRIPTOR"`), `src/remo_fixture_provider/descriptor.py` (`REMO_PROVIDER_API_VERSION = 1`, `DESCRIPTOR = ProviderDescriptor(type_name="fixture", display_name="Fixture", default_instance_name="fixture", name_format=FLAT, registry_fields=(("instance_id", "box_id"), ("region", "zone")), connection=ConnectionSpec(), implementation="remo_fixture_provider.provider", create_options=(NAME,), sync_options=())`), `provider.py` implementing the Provider Protocol with no-op/raising functions (create prints, destroy/teardown no-op, probe returns empty, snapshot_* raise `OperationFailedError("fixture provider has no snapshots")`). Installed via `pyproject.toml` `dev` extra entry `remo-fixture-provider` + `[tool.uv.sources] remo-fixture-provider = { path = "tests/fixtures/remo_fixture_provider", editable = true }`. CI's `uv sync --all-extras` installs it. `tests/unit/test_schema_drift.py::test_t8` switches to `builtin_descriptors()`.

## R9. Test isolation
Add `reset_discovery_for_tests()` in `provider_registry` (clears `_REGISTRY`, `_MODULE_CACHE`, flags, records) and a pytest fixture `fresh_provider_registry` in `tests/unit/core/test_provider_plugins.py` that resets before/after; synthetic entry points are simple objects with `name`, `value`, `module`, `dist` and `load()`; monkeypatch `remo_cli.core.provider_plugins._iter_entry_points`.

## R10. Architecture test addition
`tests/unit/test_architecture.py`: new test scanning `core/registry.py`, `core/reconcile.py`, `core/provider_plugins.py` for string literals in `{"incus","proxmox","hetzner","aws"}` (AST `Constant`), allowlist empty. (`core/known_hosts.get_aws_region` keeps its pre-existing `type_filter="aws"`; out of scope, recorded as a follow-up issue.)

## R11. Deferred → issues
1. Move CLI warnings to stderr consistently (discovery warnings included).
2. `core/known_hosts.get_aws_region` still names `aws` (pre-existing; move behind the AWS descriptor/proxy hook).
3. Tier 2 validation of the wheel with a real out-of-tree provider before the release that ships `PROVIDER_API_VERSION` 1.
4. Non-SSH connection extension point (the `ConnectionSpec` surface is SSH-shaped; a non-SSH plugin would need a public seam) — noted from the queue row's summary.
