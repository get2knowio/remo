# Adding a Provider

A provider is a free-function module (`providers/<type>.py`) plus one
`ProviderDescriptor` (`providers/<type>_descriptor.py`) registered in
`providers/builtin.py`. There is no fifth CLI file to write: `cli/main.py`
builds every provider's `create`/`destroy`/`update`/`list`/`info`/`sync`/
`snapshot` command group from the descriptor via `cli/providers/factory.py`.
Adding DigitalOcean or Vultr touches zero existing CLI files — you add two
new files and one two-line registration (SC-001).

This describes the current state of the codebase, delivered by
`specs/018-provider-abstraction/`. The factory (`cli/providers/factory.py`),
the conformance suite (`tests/unit/providers/test_provider_conformance.py`)
and the destroy template (`core/lifecycle.py`) are all implemented and wired
in; along with the registry, protocol, error taxonomy and the four
descriptors below, they are the ground truth for API shapes.

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

**Part B — heterogeneous, CLI-facing verbs**: `create`, `update`, `destroy`
extras, and any `CommandSpec.impl` you declare (AWS's `stop`/`start`/`reboot`
are the existing example). These keep provider-natural keyword signatures —
there's no shared shape to conform to structurally, but there is a naming
rule (R-B1): **every keyword parameter must exactly match the `param` name
of an `OptionSpec` your descriptor declares for that command.** The
conformance suite checks this with `inspect.signature`, so a descriptor/impl
drift is a test failure, not a runtime surprise. Example — Hetzner's
`create_options` declare `param="server_type"` and `LOCATION` (`param="location"`),
so `providers/hetzner.py::create` takes `server_type` and `location` keyword
args (see `src/remo_cli/providers/hetzner.py:98`).

Confirmation prompts inside a verb take an injected `auto_confirm: bool`
(the factory wires this from `--yes`/`-y`); declining raises
`UserAbortedError` rather than the verb prompting itself.

## Step 2 — declare the descriptor

`providers/<type>_descriptor.py` is pure metadata: stdlib + `core/provider_registry`
types only, **no SDK import** (R-C2 — this is what keeps `remo --help` from
importing `boto3`/`hcloud`/your-new-SDK on every invocation). Look at
`src/remo_cli/providers/hetzner_descriptor.py` for the simplest real example
and `src/remo_cli/providers/incus_descriptor.py` for a `HOST_SCOPED` one with
more extras and an extra command.

Minimum shape:

```python
from remo_cli.core.provider_registry import (
    REGION, ConnectionSpec, NameFormat, OptionSpec, ProviderDescriptor,
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
    update_options=(),
    destroy_options=(),
    sync_options=(REGION,),
    info_options=(),
    extra_commands=(),
    deprecated_options=(),
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

A `FakeProvider` fixture registers a throwaway descriptor in the same suite
and asserts its full `remo fake ...` command group mounts with zero
modifications to any existing CLI file — that's the automated proof of the
"zero existing files touched" promise this doc opens with. Run it the same
way for your new provider:

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
blocks in play. It reuses `LOCATION` and `CREATE_YES_DEPRECATION` straight
from the catalog, and declares two extras — `--type`/`server_type` and
`--remove-volume`/`remove_volume` — that no other provider needs:

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
    deprecated_options=(CREATE_YES_DEPRECATION,),
)
```

`incus_descriptor.py` shows the other pattern you'll need for a
`HOST_SCOPED` provider (names like `host/container`): it takes shared
`HOST`/`USER` options but overrides their per-command `default` with
`dataclasses.replace(HOST, default="localhost")` rather than declaring a new
`OptionSpec` — same flag, same help text, different default for `create` vs
`update`/`destroy`.

## Shared vs provider-specific options

`core/provider_registry.py` defines a canonical catalog of `OptionSpec`
objects — `NAME`, `HOST`, `USER`, `DOMAIN`, `IMAGE`, `CORES`, `MEMORY`,
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
