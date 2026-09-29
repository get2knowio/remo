---

description: "Task list for 027-provider-plugins"
---

# Tasks: Provider Plugin Discovery

**Input**: `/specs/027-provider-plugins/{spec,plan,research,data-model}.md`, `contracts/plugin-contract.md`, `quickstart.md`

**Tests**: required (Constitution VI; spec FR-014/16).

## Phase 1: Setup
- [X] T001 Baseline: `uv run pytest --tb=short -q`, ruff, mypy; record counts.

## Phase 2: Foundational (descriptor fields + discovery core)
- [X] T002 `core/provider_registry.py`: add `PROVIDER_API_VERSION = 1`; fields `registry_legacy_keys`, `region_scoped_sync`, `sync_scope_description`; `_ensure_discovered()` (builtins → plugins) with `_ensure_builtins_imported` alias; `_SOURCES` dict; `builtin_descriptors()`, `descriptor_source()`, `reset_discovery_for_tests()`; `register()` unchanged.
- [X] T003 `core/provider_plugins.py`: `PluginLoadRecord`, `plugins_disabled()`, `_iter_entry_points()`, `discover_entry_points(register_fn) -> list[PluginLoadRecord]`, `plugin_load_records()`; warnings via `core.output.print_warning`.
- [X] T004 [P] Tests `tests/unit/core/test_provider_plugins.py`: synthetic entry points (loaded descriptor; factory callable; raising load → skipped + warning names dist/ep; wrong object; `None`; duplicate of builtin → first wins, warning names both dists; duplicate between plugins; API version mismatch/absent warnings; disable env var; ordering by name; records content; `reset_discovery_for_tests`).

## Phase 3: US1 + US2 (discovery end to end, robustness)
- [X] T005 Fixture distribution `tests/fixtures/remo_fixture_provider/` (pyproject, descriptor with `REMO_PROVIDER_API_VERSION`, provider module satisfying the Protocol); `pyproject.toml` dev extra + `[tool.uv.sources]` path; `uv sync --all-extras`; `uv.lock` updated.
- [X] T006 `tests/unit/test_schema_drift.py::test_t8` → `builtin_descriptors()`; `tests/unit/cli/test_main.py` expected set = built-ins + `providers` + loaded plugin types.
- [X] T007 `tests/integration/test_fixture_plugin_e2e.py`: `remo --help` shows `fixture`; `remo fixture --help` lists generated verbs; registry round-trip of a fixture entry (`box_id`/`zone`); `get_known_hosts` sees it; web hosts API `InstanceOut(instance_type="fixture")` serializes as a string (no enum change); subprocess `REMO_DISABLE_PROVIDER_PLUGINS=1 remo --help` has no `fixture`; exit codes 0.

## Phase 4: US3 (de-literalize)
- [X] T008 `core/registry.py`: `is_known_type()`, generic parse via `registry_fields` + `registry_legacy_keys`, `_mode_field_aware()` for inference and validation (message byte-identical for builtins), unknown-type warning in `_parse_v2`; remove `KNOWN_TYPES`.
- [X] T009 [P] `providers/proxmox_descriptor.py` `registry_legacy_keys=(("node_user","region"),)`, `sync_scope_description`; `aws_descriptor.py` `region_scoped_sync=True`, description; `incus_descriptor.py` description.
- [X] T010 `core/reconcile.py` `SyncScope` via descriptor fields.
- [X] T011 [P] `core/known_hosts.display_name_for`; `models/host.py` shim; `providers/added.py` via `_is_host_scoped_type`.
- [X] T012 [P] Tests: `tests/unit/core/test_registry_plugin_types.py` (fixture-type round-trip; uninstalled-type round-trip preserved + warned; mode-field-aware plugin inference/validation via `temporary_registration`; proxmox `node_user` migration still reads); reconcile/host-model existing suites unchanged; `test_reconcile.py` gains a region-scoped plugin case.

## Phase 5: US4 (visibility)
- [X] T013 `cli/plugins.py` `providers` command; `cli/main.py` registration; `tests/unit/cli/test_providers_cmd.py`.
- [X] T014 `web/check.py` `_provider_plugins_check`; `tests/unit/web/test_check_plugins.py`.

## Phase 6: US5 (docs + gates)
- [X] T015 `tests/unit/test_architecture.py`: literal scan of `core/registry.py`, `core/reconcile.py`, `core/provider_plugins.py`.
- [X] T016 `docs/provider-plugins.md`; `docs/providers.md` link; README one line; CLAUDE.md + AGENTS.md diagrams (`core/provider_plugins.py`, `cli/plugins.py`, updated `registry.py`/`reconcile.py`/`provider_registry.py` lines), Commands (`remo providers`), Recent Changes (+ move the displaced entry to `docs/feature-history.md`).

## Phase 7: Polish
- [X] T017 ruff, mypy, full suite; quickstart run; issues drafted in `specs/027-provider-plugins/issues.md` (filed at PR time).
