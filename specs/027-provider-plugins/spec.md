# Feature Specification: Provider Plugin Discovery — Entry-Point Providers and De-literalized Registry

**Feature Branch**: `027-provider-plugins`

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "Make remo's provider mechanism pluggable: an installed Python distribution can contribute a complete provider (descriptor + implementation) via package metadata, with zero edits to the remo codebase. Discover plugins from the `remo.providers` entry-point group; a broken plugin never bricks the CLI; an API-version handshake; registry v2 accepts plugin types by making the parse path descriptor-driven like the serialize path already is; descriptor-driven sync scoping and host-scoped display names; operator visibility of discovered plugins; an end-to-end fixture plugin; docs and gates." (Spec Prompts queue, "Provider plugin discovery", authored 2026-08-28, grounded against `main` on 2026-09-29.)

## Context

remo's providers are declared, not special-cased (Constitution Principle II): every provider is a metadata-only descriptor registered with `core/provider_registry.py`, and everything downstream — CLI group mounting, `remo shell` dispatch, completion, snapshots, known-hosts scoping, the web service — operates on descriptors and the Provider Protocol. Two things still make the built-in set special:

1. **Discovery.** The only registration path is `providers/builtin.py`, imported lazily once. A provider that lives in another distribution has no way in without editing this repository.
2. **Leftover type literals** that predate the descriptor refactors: the registry's parse-side gate (`KNOWN_TYPES`) and its per-type nested-key branches; two AWS-literal access-mode rules; the sync scope's `aws`/`incus`/`proxmox` branches; and the `{"incus", "proxmox"}` display-name check in the host model and in `providers/added.py`.

This feature closes both. It is product-neutral: it names no out-of-tree provider. The public motivation is completing Principle II; the descriptor's `sdk_extra` comment already anticipates third-party providers.

## Clarifications

### Session 2026-09-29

- Q: How does a plugin declare the provider API version it targets? → A: A module attribute `REMO_PROVIDER_API_VERSION` (int) on the module that holds the entry-point object. Missing or different from remo's `PROVIDER_API_VERSION` → one warning naming both values (or "undeclared"), registration still attempted.
- Q: Where does the proxmox `node_user` legacy-key read live once the literal branches go? → A: In the descriptor: a new `registry_legacy_keys` field maps legacy nested JSON keys to `KnownHost` attributes (Proxmox declares `node_user → region`). The parser applies it generically; the lazy migration keeps working byte-for-byte.
- Q: How does `SyncScope.describe()` keep its four exact strings without literals? → A: A new descriptor field `sync_scope_description` (a format template with `{host}`/`{region}`), declared by the three providers that had bespoke text; the generic fallback is `"<display name lower> (all servers in project)"`.
- Q: Does a broken plugin fail `remo web check`? → A: No. The new `provider_plugins` check line always PASSES (a broken plugin must never block the container's startup gate, which is `remo web check`); its detail lists every skipped plugin with distribution, entry point and reason, and its remediation names the fix. Failure is visible, not fatal.
- Q: How is the fixture plugin installed, and what do the exact-set tests do about it? → A: As a path dependency in the PEP 735 `dev` dependency group (installed by every `uv sync`, in CI and locally, but never part of the published wheel's metadata). The tests that pin the built-in set (`KnownProviderType` drift, the exact CLI command set) compare against the **built-in** descriptors, exposed by a new `builtin_descriptors()` accessor, and separately assert the fixture group is present when the plugin is installed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An installed distribution contributes a provider (Priority: P1)

A developer packages a provider as its own distribution: a metadata-only descriptor module, an implementation module satisfying the Provider Protocol, and an entry point in the `remo.providers` group. After `pip install`, `remo --help` lists the new provider group, every generated verb works, the registry stores and reads its hosts, known-hosts scoping and completion see them, and the web console lists them as an off-union type string. No file in remo changed.

**Why this priority**: it is the feature; without discovery the rest is refactoring.

**Independent Test**: the in-repo fixture plugin, installed in the test environment, is discovered end to end (CLI group in `--help`, registry v2 round-trip of a fixture-type entry, known-hosts and completion, web hosts API returns the type as a string).

**Acceptance Scenarios**:

1. **Given** the fixture plugin is installed, **When** `remo --help` runs, **Then** its provider group appears after the four built-ins and its verbs are generated from its descriptor.
2. **Given** a registry entry of the fixture type, **When** the registry is read and rewritten, **Then** the entry round-trips byte-identically through the descriptor's `registry_fields`.
3. **Given** the fixture plugin, **When** the web hosts API lists instances, **Then** its instances carry the plugin's type name as a plain string (the OpenAPI enum is untouched; the console's runtime fallback presents it).
4. **Given** two plugins, **When** discovered, **Then** they register after the built-ins in entry-point-name order, every run.

---

### User Story 2 - A broken plugin never bricks the CLI (Priority: P1)

An operator has a plugin whose import fails, or whose entry point returns the wrong object, or whose descriptor duplicates a built-in type. Every remo command still works; one warning line per problem names the distribution and entry point and why it was skipped; the command's exit code is unaffected. Setting `REMO_DISABLE_PROVIDER_PLUGINS=1` skips discovery entirely.

**Why this priority**: an escape hatch that fails closed is worse than none; discovery must be safe before it is useful.

**Independent Test**: synthetic entry points that raise on load, return a non-descriptor, or duplicate a type; assert the warning text, that the other plugins and all built-ins still load, and that `remo --help` exits 0.

**Acceptance Scenarios**:

1. **Given** an entry point whose load raises, **When** discovery runs, **Then** one warning names the distribution and entry point and the exception, the plugin is skipped, and every other provider loads.
2. **Given** an entry point returning something that is neither a descriptor nor a callable returning one, **Then** it is skipped with a warning naming the type of object received.
3. **Given** a plugin whose `type_name` equals a built-in's or an earlier plugin's, **Then** the first registration wins and the later one is skipped with a warning naming both distributions.
4. **Given** a plugin declaring a different `REMO_PROVIDER_API_VERSION` (or none), **Then** a warning names both versions and registration is still attempted.
5. **Given** `REMO_DISABLE_PROVIDER_PLUGINS=1`, **Then** no entry point is loaded and the built-ins work unchanged.
6. **Given** any of the above, **When** an otherwise-successful command runs, **Then** its exit code is 0.

---

### User Story 3 - The registry and sync are descriptor-driven on both sides (Priority: P2)

The four built-in providers behave byte-identically, but no code path names them: parse mirrors serialize through `registry_fields` (plus declared legacy keys), access-mode inference and validation key off `ConnectionSpec.mode_field_aware`, sync scoping keys off `name_format` and a new `region_scoped_sync` flag, and host-scoped display names key off `name_format`. An entry of a type with no installed provider is preserved verbatim across writes and reported, never dropped.

**Why this priority**: without it a plugin's hosts would be silently discarded by the registry, and Principle II would still be half true.

**Independent Test**: the existing registry-format, reconcile and host-model suites pass unchanged; new tests round-trip a fixture-type entry and an uninstalled-type entry; a literal-scan test proves the de-literalized modules contain no built-in type names.

**Acceptance Scenarios**:

1. **Given** a v2 file holding built-in entries (incl. a pre-rename proxmox `node_user` entry), **When** read and rewritten, **Then** the bytes are identical to today's output.
2. **Given** an entry whose type has no installed provider, **When** read and rewritten, **Then** it is preserved verbatim, a warning names it, and it is not dropped.
3. **Given** a mode-field-aware plugin, **When** its host is written with an instance id and no access mode, **Then** access is inferred as `ssm` exactly as for AWS today; a non-aware type with `ssm` is rejected with the same message form.
4. **Given** the four built-ins, **When** sync scopes are validated, matched and described, **Then** every existing reconcile test passes unchanged.

---

### User Story 4 - Operators can see what was discovered (Priority: P2)

`remo providers` lists every provider with its source (built-in or distribution and version) and, for plugins, the load status including "skipped: <reason>"; `remo web check` carries a `provider_plugins` line with the same facts. A user with a broken plugin sees what failed and why without reading source.

**Independent Test**: CLI runner output for the fixture plugin and for a synthetic broken one; the check line's detail and remediation.

**Acceptance Scenarios**:

1. **Given** the fixture plugin installed, **When** `remo providers` runs, **Then** the table shows four built-ins and the fixture with its distribution name and version.
2. **Given** a skipped plugin, **Then** `remo providers` shows it with status `skipped` and the reason, and `remo web check`'s `provider_plugins` line PASSES with the same detail and a remediation.

---

### User Story 5 - Documented and gated (Priority: P3)

`docs/provider-plugins.md` tells a developer how to build a provider distribution; the CLAUDE.md/AGENTS.md structure diagrams and any prose claiming full descriptor-drivenness are updated; the docs-structure gate passes; the architecture test additionally proves the de-literalized core modules stay provider-name-free.

**Acceptance Scenarios**:

1. **Given** the docs, **Then** a developer can build the fixture plugin's equivalent from the guide alone (descriptor, entry point, implementation contract, `registry_fields`, `proxy_hook`, API version, version-pin guidance).
2. **Given** the gates, **Then** docs-structure, architecture, ruff, mypy and the full suite pass.

---

### Edge Cases

- Two entry points with the same name in different distributions: both load in name-then-value order; a duplicate `type_name` still resolves first-wins with a warning.
- A plugin descriptor whose `__post_init__` validation raises: skipped with the validation message.
- A plugin whose implementation module is missing: discovery succeeds (descriptors are metadata-only); the first command needing the implementation raises `MissingDependencyError`/`ImportError` exactly as today for built-ins.
- Entry point load succeeds but the callable returns `None`: skipped ("returned None").
- Discovery runs in `remo web serve`, tests and completion, and is one-shot per process; test code can reset it.
- `REMO_DISABLE_PROVIDER_PLUGINS` set to anything other than `1`/`true`/`yes` (case-insensitive) is treated as unset.
- A registry entry of an uninstalled plugin type also carries an `access` of `ssm`: preserved verbatim (no validation runs on raw entries).
- The `ssh` pseudo-type is never an entry point and is never registered; its registry branch stays local.

## Requirements *(mandatory)*

### Functional Requirements

**Discovery**

- **FR-001**: Providers MUST be discoverable from installed distributions via the entry-point group `remo.providers` using only the standard library. Each entry point MUST resolve to a `ProviderDescriptor` or a zero-argument callable returning one; descriptor modules follow the metadata-only rule (no SDK imports at load).
- **FR-002**: Discovery MUST run in the same lazy one-shot step as built-in registration: built-ins first in today's order, then entry points sorted by entry-point name. All existing descriptor consumers MUST see plugin providers with no further changes.
- **FR-003**: Any exception while loading one entry point MUST be caught, reported as one warning line naming the distribution and entry point, and skipped. A duplicate `type_name` MUST keep the first registration and skip the later one with a warning naming both distributions. Discovery failures MUST NOT affect the exit code of an otherwise-successful command.
- **FR-004**: `REMO_DISABLE_PROVIDER_PLUGINS=1` MUST skip entry-point discovery entirely.
- **FR-005**: `core/provider_registry.py` MUST export `PROVIDER_API_VERSION` (int). A plugin declares its target via a `REMO_PROVIDER_API_VERSION` module attribute; on mismatch or absence a warning names both values and registration is still attempted. Compatibility guarantees are out of scope.

**Registry parse side**

- **FR-006**: The registry MUST accept a type when it is a registered provider type or the `ssh` pseudo-type; the `KNOWN_TYPES` literal set MUST be removed.
- **FR-007**: Provider nested fields MUST be parsed by the reverse of `registry_fields`, plus descriptor-declared legacy keys (`registry_legacy_keys`), so parse and serialize agree by construction; the `ssh` branch stays local; the proxmox `node_user` migration keeps working.
- **FR-008**: Write-time `ssm` inference and the "access 'ssm' is only valid for …" validation MUST key off `ConnectionSpec.mode_field_aware`; built-in behavior and messages stay byte-identical.
- **FR-009**: An entry of a genuinely unknown type MUST be preserved verbatim across reads and writes and reported by a warning; a regression test MUST round-trip such an entry.

**Sync and display**

- **FR-010**: `SyncScope` MUST key host-scoped semantics off `name_format == HOST_SCOPED` and region-scoped semantics off a new descriptor field `region_scoped_sync` (True for AWS); `describe()` MUST use a descriptor-declared template; the four built-ins' behavior and strings stay identical.
- **FR-011**: `KnownHost.display_name()` and `providers/added.py`'s host-scoped check MUST derive from `NameFormat.HOST_SCOPED`; `models/` stays dependency-free at import time (a lazy shim is acceptable).

**Visibility**

- **FR-012**: `remo providers` MUST list every provider with source (built-in, or distribution name and version) and load status; skipped plugins MUST appear with their reason.
- **FR-013**: `remo web check` MUST include a `provider_plugins` line that always passes, whose detail lists loaded and skipped plugins with reasons and whose remediation names the fix when any was skipped.

**Fixture and gates**

- **FR-014**: A minimal fixture plugin distribution inside the repository, installed only in the test environment, MUST exercise discovery, CLI mounting, registry round-trip, known-hosts/completion and the web hosts API off-union path; broken-plugin skip, duplicate skip, the disable variable and the version-mismatch warning MUST be covered with synthetic entry points.
- **FR-015**: `docs/provider-plugins.md` MUST exist; CLAUDE.md and AGENTS.md diagrams and prose MUST be updated; the docs-structure gate MUST pass.
- **FR-016**: An architecture-test addition MUST prove the de-literalized core modules (`core/registry.py`, `core/reconcile.py`, `core/provider_plugins.py`) contain no built-in provider type literal.

**Constraints**

- **FR-017**: `web/api/hosts.py::KnownProviderType` and all generated artifacts MUST NOT change; plugin types ride the `KnownEnum | str` path. Tests that pin the built-in set MUST compare against built-ins, not all descriptors.
- **FR-018**: No optional SDK extras (issue #94), no `ssh` registration change, no new runtime dependency, no registry schema change; every existing test passes; the four built-ins' CLI surface and behavior are byte-identical.

### Key Entities

- **Entry point** (`remo.providers`): name → object (descriptor or factory), in a distribution with a name and version.
- **Plugin load record**: distribution, version, entry-point name, resulting `type_name` (if any), status (`loaded`/`skipped`), reason.
- **Descriptor fields added**: `registry_legacy_keys`, `region_scoped_sync`, `sync_scope_description`.
- **Fixture plugin**: `remo-fixture-provider` distribution under `tests/fixtures/`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the fixture plugin installed, `remo --help` lists five provider groups and the registry round-trips a fixture-type entry byte-identically (tests).
- **SC-002**: 100% of the broken/duplicate/mismatch/disabled cases produce exactly one warning line each and exit code 0 for `remo --help`.
- **SC-003**: Every pre-existing test passes unchanged except the two that pin the built-in set, which now read built-ins explicitly.
- **SC-004**: The literal scan finds zero built-in type names in the three de-literalized core modules.
- **SC-005**: ruff, mypy, architecture and docs-structure gates pass; the full suite has no new failures or skips.

## Assumptions

- Entry-point discovery is a packaging surface (Constitution IX): the release that ships it is validated on a CI-built wheel (Tier 2) before PyPI; this run validates from the source tree.
- The fixture plugin is not published anywhere; it is a path dependency in the `dev` dependency group.
- Plugins pin the exact remo version they build against; `PROVIDER_API_VERSION` starts at 1 and is bumped by hand when the descriptor/protocol surface changes.
- Warnings go through `core/output.print_warning` (stdout, yellow) like every other CLI warning today; the prompt's "stderr" is satisfied in spirit by the existing warning channel, and a follow-up may move all warnings to stderr together.
