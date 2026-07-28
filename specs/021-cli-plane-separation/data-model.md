# Data Model: CLI Plane Separation

**Feature**: 021-cli-plane-separation | **Date**: 2026-07-28

No persistent data changes: the registry v2 format, `KnownHost`, `Snapshot`, and all web-service
wire shapes are untouched (FR-011). The "data model" of this feature is the descriptor metadata
that generates the CLI surface.

## 1. `ArgumentSpec` (new, `core/provider_registry.py`)

Declarative positional argument for descriptor-declared commands.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | `str` | — | Click param name (and kwarg name in the impl signature) |
| `default` | `str \| None` | `None` | Default value when `required=False` |
| `required` | `bool` | `True` | Required positional vs optional-with-default |
| `completion` | `CompletionKind` | `NONE` | Shell-completion source (host names have none today) |

Frozen dataclass, metadata-only (no Click import — the factory translates it to
`click.Argument`).

## 2. `CommandSpec` (extended)

| Field | Type | Default | Change |
|---|---|---|---|
| `name` | `str` | — | unchanged |
| `help` | `str` | — | unchanged |
| `impl` | `str` | — | unchanged (function name in the implementation module) |
| `options` | `tuple[OptionSpec, ...]` | `()` | unchanged |
| `confirmable` | `bool` | `False` | unchanged |
| **`target`** | **`ArgumentSpec \| None`** | **`None`** | **new** — when set, the factory prepends the positional argument; its value is passed to `impl` under `target.name` |

## 3. `ProviderDescriptor` (field migration)

| Field | Status | Contents |
|---|---|---|
| `update_options` | **removed** | (verb no longer exists) |
| **`upgrade_options`** | **new**, `tuple[OptionSpec, ...] = ()` | Transport + configure-play extras. Factory injects `--only`/`--skip`/`-v` and the positional `NAME` |
| **`resize_dimensions`** | **new**, `tuple[OptionSpec, ...] = ()` | The dimension flags; ≥1 must be passed at runtime or the factory raises `PreconditionError` naming them. Empty tuple ⇒ no `resize` command is generated (not the case for any built-in) |
| **`resize_options`** | **new**, `tuple[OptionSpec, ...] = ()` | Non-dimension transport flags for `resize` |
| **`tag_options`** | **new**, `tuple[OptionSpec, ...] = ()` | Transport flags for `tag`; command generated only when `supports_managed_marker=True` |
| **`host_commands`** | **new**, `tuple[CommandSpec, ...] = ()` | Commands mounted under the `host` subgroup; empty ⇒ no subgroup |
| `supports_managed_marker` | unchanged | Now additionally gates `tag` generation (D3) |
| `extra_commands` | unchanged | Flat instance-plane extras only (AWS `stop`/`start`/`reboot`) |
| all other fields | unchanged | |

**Validation**: `__post_init__`'s duplicate-option check iterates
`create/upgrade/resize(+dimensions)/tag/destroy/sync/info` option lists plus each
`host_commands`/`extra_commands` spec's options. `resize` additionally validates that
`resize_dimensions` params don't collide with `resize_options` params.

## 4. Renamed shared/per-provider `OptionSpec`s

| Spec | Old | New | Declared in |
|---|---|---|---|
| `HOST_USER` | `--user` / `param="user"` | `--host-user` / `param="host_user"` | `incus_descriptor.py` |
| `_NODE_USER` | `--user` / `param="user"` | `--node-user` / `param="node_user"` | `proxmox_descriptor.py` |
| catalog `USER` | `--user` / `param="user"` | **removed** (no consumers remain) | `core/provider_registry.py` |

Registry JSON keys `host_user` (incus, ← `KnownHost.instance_id`) and `node_user` (proxmox,
← `KnownHost.region`) are unchanged — the param names now equal the JSON keys, which is what lets
`_resolve_entry_for_destroy` drop its `kwargs.get("user")` magic string (research D7).

## 5. Per-provider descriptor values (target state)

| Field | incus | proxmox | aws | hetzner |
|---|---|---|---|---|
| `upgrade_options` | `HOST`, `HOST_USER` | `HOST`, `_NODE_USER`, `DEVCONTAINER_RUNTIME` | `()` | `()` |
| `resize_dimensions` | `VOLUME_SIZE`, `CORES`, `MEMORY` | `VOLUME_SIZE`, `CORES`, `MEMORY` | `VOLUME_SIZE` | `VOLUME_SIZE` |
| `resize_options` | `HOST`, `HOST_USER` | `HOST`, `_NODE_USER` | `()` | `()` |
| `tag_options` | `HOST`, `HOST_USER` | `HOST`, `_NODE_USER` | n/a (no `tag`) | `()` |
| `supports_managed_marker` | `True` | `True` | `False` | `True` |
| `host_commands` | `bootstrap` (target `host`, default `localhost`; options `HOST_USER`, `NETWORK_TYPE`, `VERBOSE`) | `bootstrap` (target `host`, required; options `_NODE_USER`, `_BRIDGE`, `_STORAGE`, `_TEMPLATE`, `VERBOSE`) | `()` | `()` |
| `extra_commands` | `()` (bootstrap moved) | `()` (bootstrap moved) | `stop`/`start`/`reboot` (unchanged) | `()` |

## 6. Provider implementation surface (Part B verbs)

New/changed public functions per implementation module; every function raises the
`core/errors.py` taxonomy, never `sys.exit` (Principle III). Private helpers
(`_apply_managed_marker`, `_apply_managed_label`, `_run_resize_playbook`, `_lookup_*`,
`_resolve_vmid`, `_resolve_container_ip`) are reused unchanged.

| Function | incus | proxmox | aws | hetzner |
|---|---|---|---|---|
| `upgrade(name, ...)` | `host=""`, `host_user=""` | `host=""`, `node_user=""`, `devcontainer_runtime=None` | (name only) + registry IP refresh | (name only) |
| `resize(name, ...)` | `host`, `host_user`, `volume_size`, `cores`, `memory` | + `node_user`, VMID resolution | `volume_size` (+ in-guest grow via playbook) | `volume_size` (+ in-guest grow) |
| `tag(name, ...)` | `host`, `host_user`; read-before-write `user.remo` | `host`, `node_user`; VMID required (`PreconditionError` if unresolvable) | — (undefined) | label read-merge-PUT, already-present ⇒ no-op |
| `update(...)` | **deleted** | **deleted** | **deleted** | **deleted** |
| `update_entry(entry, *, verbose)` | delegates to `upgrade` | delegates to `upgrade` | delegates to `upgrade` | delegates to `upgrade` |
| `bootstrap(host, ...)` | positional-host signature; `host_user=` kwarg | positional-host signature; `node_user=` kwarg | — | — |

State transitions of note:

- **`tag`**: `untagged → tagged` (one provider write); `tagged → tagged` (zero writes, reported
  no-op, exit 0); write failure → `OperationFailedError` (exit 1) — strict, unlike `create`'s
  best-effort warn-and-continue, which is preserved separately.
- **`upgrade`**: no provider-side state transition by invariant (SC-001); instance-internal
  package/tooling state converges (idempotent playbook).
- **`resize`**: resource limits/volume transition to requested size; resize-to-current is a
  provider-level no-op, never an error loop (Principle VII).
