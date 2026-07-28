# Adding a Provider

A provider is a free-function module (`providers/<type>.py`) plus one
`ProviderDescriptor` (`providers/<type>_descriptor.py`) registered in
`providers/builtin.py`. There is no fifth CLI file to write: `cli/main.py`
builds every provider's `create`/`destroy`/`upgrade`/`resize`/`list`/`info`/
`sync`/`snapshot` command group (plus `tag` and a `host` subgroup, generated
conditionally — see below) from the descriptor via `cli/providers/factory.py`.
Adding DigitalOcean or Vultr touches zero existing CLI files — you add two
new files and one two-line registration (SC-001).

This describes the current state of the codebase, delivered by
`specs/018-provider-abstraction/` and extended by `specs/021-cli-plane-separation/`
(the `update` verb split into `upgrade`/`resize`/`tag`, and the `host` subgroup
for hypervisor-host-plane commands). The factory (`cli/providers/factory.py`),
the conformance suite (`tests/unit/providers/test_provider_conformance.py`)
and the destroy template (`core/lifecycle.py`) are all implemented and wired
in; along with the registry, protocol, error taxonomy and the four
descriptors below, they are the ground truth for API shapes.

## Generated command surface

Every descriptor produces `create`, `destroy`, `upgrade`, `resize`, `list`,
`info`, `sync`, and a `snapshot` subgroup (`create`/`restore`/`delete`/`list`)
unconditionally. Two more are generated conditionally, straight off descriptor
fields — no per-provider CLI code:

- **`tag`** — generated iff `supports_managed_marker=True`.
- **`host`** — a `click.Group` ("Operate on the hypervisor host, not an
  instance.") containing one command per `host_commands` entry — generated
  iff `host_commands` is non-empty.

`resize` is always generated, but its callback enforces that at least one of
`resize_dimensions`' flags was actually passed — see "The three-way `update`
split" below.

## Step 1 — implement the provider module

`providers/<type>.py` has two halves.

**Part A — the uniform `Provider` Protocol** (`core/provider_protocol.py`).
Every provider module must satisfy this structurally (no base class — mypy
checks a module against a `typing.Protocol`):

```python
def update_entry(self, entry: KnownHost, *, verbose: bool = False) -> None: ...
def teardown(self, entry: KnownHost, *, verbose: bool = False, **provider_opts: object) -> None: ...
def probe(self, scope: SyncScope, **opts: object) -> ProbeResult: ...
def snapshot_create(self, entry: KnownHost, snapshot_name: str, *, description: str = "") -> None: ...
def snapshot_restore(self, entry: KnownHost, snapshot_name: str) -> None: ...
def snapshot_delete(self, entry: KnownHost, snapshot_name: str) -> None: ...
def snapshot_list(self, entry: KnownHost) -> list[Snapshot]: ...
```

(`self` is notation only — these are top-level functions.) Rules (R-A1..R-A5,
`contracts/provider-protocol.md`):

- These take a **resolved registry entry** (`KnownHost`), not raw name
  strings. All name-format knowledge — splitting `host/container` for
  HOST_SCOPED providers, resolving a Proxmox vmid, whatever your provider
  needs — lives inside the module. Callers never parse names themselves.
- `teardown` does provider-side destruction only. Guard checks, snapshot
  pre-cleanup, the confirmation prompt and registry removal are the shared
  destroy template's job (`core/lifecycle.py`) — don't duplicate them.
- `probe` is read-only discovery for the sync engine (Spec-016 semantics,
  unchanged): every in-scope host, marked or not, `ProbeResult.complete`
  truthful, `ProbeError` on enumeration failure.
- `snapshot_list` must be public (no more private reach-ins from callers).
- `update_entry` is the one place the old, undifferentiated `update` verb
  still exists: it's the uniform entry-based hook `remo shell` calls, and
  every built-in provider implements it by delegating straight to `upgrade`
  (Part B, below) — `def update_entry(entry, *, verbose=False): upgrade(entry.name, ..., verbose=verbose)`.

**Part B — heterogeneous, CLI-facing verbs**: `create`, `upgrade`, `resize`,
`tag` (only on providers with `supports_managed_marker=True`), `destroy`
extras, and any `CommandSpec.impl` you declare — either flat
(`extra_commands`, AWS's `stop`/`start`/`reboot`) or under the `host`
subgroup (`host_commands`, Incus/Proxmox's `bootstrap`). These keep
provider-natural keyword signatures — there's no shared shape to conform to
structurally, but there is a naming rule (R-B1): **every keyword parameter
must exactly match the `param` name of an `OptionSpec` your descriptor
declares for that command** (a `CommandSpec.target`'s name counts too — see
"Positional arguments" below). The conformance suite checks this with
`inspect.signature`, so a descriptor/impl drift is a test failure, not a
runtime surprise. Example — Hetzner's `create_options` declare
`param="server_type"` and `LOCATION` (`param="location"`), so
`providers/hetzner.py::create` takes `server_type` and `location` keyword
args (see `src/remo_cli/providers/hetzner.py:98`).

### The three-way `update` split

Spec 021 replaced the single `update` verb (and `ProviderDescriptor.update_options`)
with three narrower verbs, each with its own descriptor field and generated
command:

| Verb | Descriptor field(s) | Factory-injected extras | Notes |
|---|---|---|---|
| `upgrade` | `upgrade_options` | `--only`/`--skip`/`-v` + positional `NAME` | Refreshes dev tools via the configure playbook; no provider-side state transition by invariant. |
| `resize` | `resize_dimensions`, `resize_options` | `-v` + positional `NAME` | Changes resource limits/volume size; generated only when `resize_dimensions` is non-empty (a provider with nothing to resize gets no `resize` command). The callback raises `PreconditionError` (listing the dimension flag names, e.g. `--volume-size, --cores, --memory`) if **none** of `resize_dimensions`' params were passed — one check, in the factory, not duplicated per provider. |
| `tag` | `tag_options` | positional `NAME` | Marks an instance remo-managed; generated only when `supports_managed_marker=True`. Read-before-write: already-tagged is a reported no-op (exit 0), a write failure is a strict `OperationFailedError` (exit 1) — unlike `create`'s best-effort warn-and-continue. |

`__post_init__` validates `resize_dimensions` and `resize_options` together
(a flag can't appear in both), on top of the existing per-command duplicate
check that now covers `upgrade_options`/`resize_dimensions`/`resize_options`/
`tag_options` alongside `create_options`/`destroy_options`/`sync_options`/
`info_options`.

Provider implementation modules mirror the split: `providers/incus.py` has
`upgrade(name, host="", host_user="", tools_only=(), tools_skip=(), verbose=False)`,
`resize(name, host="", host_user="", volume_size="", cores=0, memory=0, verbose=False)`,
and `tag(name, host="", host_user="")` — the old `update()` function is gone;
its private helpers (`_run_resize_playbook`, `_apply_managed_marker`, ...)
are reused unchanged across the three.

### Positional arguments (`ArgumentSpec` / `CommandSpec.target`)

`ArgumentSpec` (`core/provider_registry.py`) declares a positional argument
for a descriptor-driven command:

```python
@dataclass(frozen=True)
class ArgumentSpec:
    name: str                       # click param name == impl kwarg name
    default: str | None = None
    required: bool = True
    completion: CompletionKind = CompletionKind.NONE
```

The generated instance verbs (`upgrade`/`resize`/`tag`) always take a
positional `NAME` argument (param `name`) built internally by the factory —
you don't declare an `ArgumentSpec` for those yourself. Where you *do* use
`ArgumentSpec` directly is `CommandSpec.target`, a new field on the existing
`extra_commands`/`host_commands` dataclass:

```python
@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    impl: str
    options: tuple[OptionSpec, ...] = ()
    confirmable: bool = False
    target: ArgumentSpec | None = None  # positional target, prepended before options
```

When `target` is set, the factory prepends a `click.Argument` (not just an
option) before the command's declared `options`; its value is passed to
`impl` under `target.name`. Incus's `bootstrap` is the worked example — it
moved from a flat `extra_commands` entry taking `--host` to a `host_commands`
entry taking the host positionally:

```python
CommandSpec(
    name="bootstrap",
    help="Initialize an Incus host.",
    impl="bootstrap",
    target=ArgumentSpec("host", default="localhost", required=False),
    options=(HOST_USER, NETWORK_TYPE, VERBOSE),
)
```

— matched by `providers/incus.py::bootstrap(host="localhost", host_user="", network_type="", verbose=False)`.

### The `host` subgroup

A non-empty `host_commands: tuple[CommandSpec, ...]` mounts a `host`
`click.Group` (help: "Operate on the hypervisor host, not an instance.")
under the provider's group, one command per spec, built the same way as a
flat `extra_commands` entry (including `target`/`confirmable` support).
`extra_commands` still exists, unchanged in shape, for flat instance-plane
extras that aren't host operations (AWS's `stop`/`start`/`reboot`). Only
Incus and Proxmox declare `host_commands` today (both just `bootstrap`); AWS
and Hetzner have none, so neither gets a `host` group.

Confirmation prompts inside a verb take an injected `auto_confirm: bool`
(the factory wires this from `--yes`/`-y`); declining raises
`UserAbortedError` rather than the verb prompting itself.

## Step 2 — declare the descriptor

`providers/<type>_descriptor.py` is pure metadata: stdlib + `core/provider_registry`
types only, **no SDK import** (R-C2 — this is what keeps `remo --help` from
importing `boto3`/`hcloud`/your-new-SDK on every invocation). Look at
`src/remo_cli/providers/hetzner_descriptor.py` for the simplest real example
and `src/remo_cli/providers/incus_descriptor.py` for a `HOST_SCOPED` one with
more extras and a `host_commands` entry.

Minimum shape:

```python
from remo_cli.core.provider_registry import (
    REGION, VOLUME_SIZE, ConnectionSpec, NameFormat, OptionSpec, ProviderDescriptor,
)

_DROPLET_SIZE = OptionSpec(name="--size", param="size", default="", help="Droplet size slug.")

DESCRIPTOR = ProviderDescriptor(
    type_name="digitalocean",
    display_name="DigitalOcean",
    default_instance_name="remo",
    name_format=NameFormat.FLAT,          # or HOST_SCOPED for "host/container"-style names
    registry_fields=(),                    # extra fields your entries need in registry.json
    connection=ConnectionSpec(),
    implementation="remo_cli.providers.digitalocean",  # dotted path, imported lazily
    sdk_extra="digitalocean",              # None if no optional SDK
    create_options=(_DROPLET_SIZE, REGION),
    upgrade_options=(),
    resize_dimensions=(VOLUME_SIZE,),      # >=1 required here, or `resize` always raises PreconditionError
    resize_options=(),
    tag_options=(),                         # unused unless supports_managed_marker=True
    destroy_options=(),
    sync_options=(REGION,),
    info_options=(),
    extra_commands=(),
    host_commands=(),                       # non-empty mounts a `host` subgroup
    snapshot_region_scoped=False,
)
```

`type_name` must be lowercase and unique (`register()` raises `ValueError` on
a duplicate — R-C1, fail-loud at startup). `implementation` is only imported
the first time a command actually runs (`get_provider()` in
`core/provider_registry.py`), and an `ImportError` there is translated into
`MissingDependencyError` naming `sdk_extra` — so a missing SDK never crashes
`remo --help` for everyone else.

## Step 3 — register it

One addition to `providers/builtin.py`:

```python
from remo_cli.providers.digitalocean_descriptor import DESCRIPTOR as DIGITALOCEAN_DESCRIPTOR
...
register(DIGITALOCEAN_DESCRIPTOR)
```

`builtin.py` is imported lazily by `core/provider_registry.py` on first
lookup, so every entry point (CLI, `remo web serve`, tests) sees the new
provider with no other wiring.

## Step 4 — the conformance suite is the gate

`tests/unit/providers/test_provider_conformance.py`, parametrized over
`all_descriptors()`, checks:

1. Your implementation module structurally satisfies the `Provider`
   Protocol (Part A).
2. Every generated command's descriptor options agree with the impl's
   signature via `inspect.signature` (Part B, R-B1).
3. No `SystemExit` escapes any verb under induced failure (subprocess/SDK
   calls monkeypatched to fail).

A `FakeProvider` fixture registers a throwaway descriptor — declaring
`upgrade_options`/`resize_dimensions`/`tag_options` (with
`supports_managed_marker=True`) and a `host_commands` entry with a `target`
— in the same suite and asserts its full `remo fake ...` command group
(`create`/`destroy`/`upgrade`/`resize`/`tag`/`list`/`info`/`sync`/`snapshot`/`host`)
mounts with zero modifications to any existing CLI file — that's the
automated proof of the "zero existing files touched" promise this doc opens
with. The suite also asserts the negative: a descriptor with
`supports_managed_marker=False` and empty `host_commands` gets no `tag`
command and no `host` group. Run it the same way for your new provider:

```bash
uv run pytest tests/unit/providers/test_provider_conformance.py -v
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

See `specs/018-provider-abstraction/quickstart.md` for the full validation
checklist (architecture gates, CLI-uniformity comparison, startup-laziness
assertions).

## The error contract

Business logic in your module raises `core/errors.py` taxonomy errors —
never `sys.exit`, never a bare `RuntimeError`. There is exactly one
translation point from exception to process exit code, the CLI factory's
`provider_command` wrapper; your module should never call `sys.exit` itself.

| Raise | When | exit_code |
|-------|------|-----------|
| `MissingDependencyError` | optional SDK not installed — message must include the install command | 1 |
| `PreconditionError` | invalid input, entry not found, wrong state, unknown provider type | 1 |
| `OperationFailedError` | a subprocess/playbook/API call failed — message carries the underlying rc/error | 1 |
| `UserAbortedError` | user declined a confirmation prompt | 3 |

A nonzero playbook/API result becomes `OperationFailedError` with the rc
quoted in the message — don't propagate raw exit codes. Anything that isn't
one of these (a genuine bug) should propagate as a normal traceback; don't
catch-and-swallow into a generic `ProviderError`.

## Worked example: reusing the shared option catalog

`src/remo_cli/providers/hetzner_descriptor.py` shows the two building
blocks in play. It reuses `LOCATION` straight from the catalog, and declares
two extras — `--type`/`server_type` and `--remove-volume`/`remove_volume` —
that no other provider needs:

```python
_SERVER_TYPE = OptionSpec(
    name="--type", param="server_type", default="", help="Server type (default: cx22)."
)
_REMOVE_VOLUME = OptionSpec(
    name="--remove-volume", param="remove_volume", is_flag=True,
    help="Also remove persistent volume.",
)

DESCRIPTOR = ProviderDescriptor(
    ...
    create_options=(_SERVER_TYPE, LOCATION),
    destroy_options=(_REMOVE_VOLUME,),
)
```

`incus_descriptor.py` shows the other pattern you'll need for a
`HOST_SCOPED` provider (names like `host/container`): it takes the shared
`HOST` option but overrides its per-command `default` with
`dataclasses.replace(HOST, default="localhost")` rather than declaring a new
`OptionSpec` — same flag, same help text, different default for `create`/
`sync` (`"localhost"`) vs `upgrade`/`resize`/`tag`/`destroy`/`info`
(`""`). The SSH-user-on-the-host flag is *not* shared across providers —
Incus declares its own local `HOST_USER` (`--host-user`/`param="host_user"`)
and Proxmox its own local `_NODE_USER` (`--node-user`/`param="node_user"`),
each reused across `create`/`upgrade`/`resize`/`tag`/`destroy`/`info` the
same way `HOST` is. (The catalog used to have a generic `USER` entry; it was
removed once every consumer had migrated to a provider-local, differently-named
option — the JSON registry key each maps to, `host_user`/`node_user`, now
equals the click param name, which is what lets
`_resolve_entry_for_destroy` find the right kwarg generically instead of
special-casing the literal `"user"`.)

## Shared vs provider-specific options

`core/provider_registry.py` defines a canonical catalog of `OptionSpec`
objects — `NAME`, `HOST`, `DOMAIN`, `IMAGE`, `CORES`, `MEMORY`,
`VOLUME_SIZE`, `ONLY`, `SKIP`, `USE_IP`, `DEVCONTAINER_RUNTIME`, `REGION`,
`LOCATION`, `VERBOSE`, plus the factory-injected `YES`, `DRY_RUN`, `ALL_FLAG`.

**If your provider needs a flag another provider already has, reference the
same object** (via `dataclasses.replace()` if only the default/required-ness
differs) — don't declare a new `OptionSpec` with matching `name`/`param`.
This is what makes cross-provider uniformity structural rather than a
convention someone can forget (SC-002): because `--host` is *the same
object* everywhere, its spelling, short form, metavar and help text cannot
drift between providers by accident. A new `OptionSpec` in your own
descriptor module is for anything genuinely provider-specific — Hetzner's
`--type`/`server_type`, Incus's `--network-type` — that no other provider
exposes.
