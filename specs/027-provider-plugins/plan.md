# Implementation Plan: Provider Plugin Discovery

**Branch**: `027-provider-plugins` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/027-provider-plugins/spec.md`

## Summary

Add stdlib entry-point discovery (`remo.providers`) to the lazy one-shot registration step in `core/provider_registry.py`, implemented in a new `core/provider_plugins.py` that loads each entry point defensively (one warning per failure, first-wins duplicates, `REMO_DISABLE_PROVIDER_PLUGINS`, `PROVIDER_API_VERSION` handshake) and keeps a load record per plugin. Remove the last built-in type literals: the registry parse side becomes the reverse of `registry_fields` (+ descriptor-declared legacy keys) with `mode_field_aware` driving access-mode rules; `SyncScope` keys off `name_format`, a new `region_scoped_sync` flag and a `sync_scope_description` template; host-scoped display names key off `name_format` via a core helper with a lazy shim on the model. Operator visibility via `remo providers` and a `provider_plugins` line in `remo web check`. An in-repo fixture plugin distribution (dev extra) proves the path end to end; synthetic entry points cover the failure modes. See [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11+ (CI 3.11/3.12/3.13); `importlib.metadata` (stdlib)
**Primary Dependencies**: none new; click for the listing command
**Storage**: registry v2 unchanged on disk
**Testing**: pytest (unit: discovery with synthetic `EntryPoint`s, registry round-trips, reconcile, host model, CLI runner, web check; subprocess tests for `remo --help` under the disable variable); existing gates (architecture, docs-structure, schema drift, ruff, mypy)
**Target Platform**: same as remo
**Project Type**: CLI + web service library
**Performance Goals**: discovery adds one `entry_points()` scan per process; no SDK imports at load
**Constraints**: Constitution I (core knows no provider name; lazy imports by dotted name), II (this feature completes it), III, VI, VIII (both diagrams), IX (entry points are a packaging surface — Tier 2 owed before release); no OpenAPI/artifact change
**Scale/Scope**: 1 new core module, 1 new CLI command module, descriptor fields ×3, 4 de-literalized modules, 1 fixture distribution, ~8 test modules, 1 doc

## Constitution Check

| # | Principle | Check | Status |
|---|-----------|-------|--------|
| I | Layered Architecture | `core/provider_plugins.py` imports entry points by name only; no provider name in core (new literal-scan test); `cli/plugins.py` is Click only | PASS |
| II | Providers Declared | The feature removes the discovery and literal exceptions; new descriptor fields carry the per-type facts | PASS |
| III | Typed Errors | Discovery never raises to callers; `remo providers` returns 0; registry errors unchanged | PASS |
| IV | Generated Contracts | `KnownProviderType` and the four artifacts untouched; drift test compares to `builtin_descriptors()` | PASS |
| V | Ansible | N/A | N/A |
| VI | Test Skip/Fail Paths | every skip reason has a test; disable variable; mismatch warning; unknown-type round-trip | PASS |
| VII | Idempotent | discovery is one-shot; registry writes unchanged | PASS |
| VIII | Docs Reflect Reality | `docs/provider-plugins.md`, `docs/providers.md` cross-link, CLAUDE.md + AGENTS.md diagrams/prose | PASS |
| IX | Pre-Release Off-Index | entry-point discovery is a packaging surface: Tier 2 (`dev-build.yml` wheel) validation owed before the release that ships it; noted in the PR | PASS |

Post-design re-check: PASS.

## Project Structure

```text
src/remo_cli/
├── core/provider_registry.py   # + PROVIDER_API_VERSION, builtin_descriptors(), descriptor_source(), registry_legacy_keys/region_scoped_sync/sync_scope_description fields, _ensure_discovered()
├── core/provider_plugins.py    # NEW: discover_entry_points() → list[PluginLoadRecord]; plugin_load_records(); disable env var; API-version check
├── core/registry.py            # parse via registry_fields reverse map + legacy keys; is_known_type(); mode_field_aware access rules; unknown-type warning
├── core/reconcile.py           # SyncScope via name_format / region_scoped_sync / sync_scope_description
├── core/known_hosts.py         # + display_name_for(host)
├── models/host.py              # display_name() → lazy shim onto core.known_hosts.display_name_for
├── providers/added.py          # host-scoped check via core.known_hosts._is_host_scoped_type
├── providers/{aws,proxmox,incus}_descriptor.py  # new fields
├── cli/plugins.py              # NEW: `remo providers` listing
├── cli/main.py                 # + providers command
└── web/check.py                # + provider_plugins check line
tests/fixtures/remo_fixture_provider/   # NEW distribution: pyproject.toml, src/remo_fixture_provider/{__init__,descriptor,provider}.py
tests/unit/core/test_provider_plugins.py, test_registry_plugin_types.py, test_reconcile_descriptor_driven.py
tests/unit/cli/test_providers_cmd.py, tests/unit/web/test_check_plugins.py, tests/integration/test_fixture_plugin_e2e.py
docs/provider-plugins.md; docs/providers.md (link); CLAUDE.md; AGENTS.md; pyproject.toml (dev extra + uv source)
```

## Complexity Tracking

None.
