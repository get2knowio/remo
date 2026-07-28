# Contract: ProviderDescriptor Extension (internal, `core/provider_registry.py`)

The internal contract between the descriptor layer and the CLI factory. A fifth provider obtains
the full 021 surface from these declarations alone (FR-006 / SC-005) — no CLI-file edits.

## New/changed types

```python
@dataclass(frozen=True)
class ArgumentSpec:
    """Declarative positional argument for descriptor-declared commands (spec 021, FR-005)."""
    name: str                       # click param name == impl kwarg name
    default: str | None = None
    required: bool = True
    completion: CompletionKind = CompletionKind.NONE

@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str
    impl: str
    options: tuple[OptionSpec, ...] = ()
    confirmable: bool = False
    target: ArgumentSpec | None = None      # NEW: positional target, prepended before options
```

## `ProviderDescriptor` fields (delta)

```python
# REMOVED
update_options: tuple[OptionSpec, ...]

# ADDED (all default to ())
upgrade_options: tuple[OptionSpec, ...]     # extras for `upgrade`; factory injects ONLY/SKIP/VERBOSE + positional NAME
resize_dimensions: tuple[OptionSpec, ...]   # dimension flags; >=1 required at invocation (factory-enforced)
resize_options: tuple[OptionSpec, ...]      # non-dimension extras for `resize`; factory injects VERBOSE + positional NAME
tag_options: tuple[OptionSpec, ...]         # extras for `tag`; factory injects positional NAME
host_commands: tuple[CommandSpec, ...]      # mounted under the `host` subgroup when non-empty
```

## Generation rules (factory obligations)

| Rule | Behavior |
|---|---|
| G-1 | `upgrade` and `resize` are generated for every descriptor. `tag` is generated iff `supports_managed_marker is True`. The `host` group is generated iff `host_commands` is non-empty. |
| G-2 | New instance verbs take `click.Argument(["name"], shell_complete=<instance completer>)` — param name `name`, matching the impl kwarg. Snapshot subcommands keep param `instance`. |
| G-3 | `resize` callback: if every `resize_dimensions` param is falsy, raise `PreconditionError` listing the dimension flag names (e.g. `--volume-size, --cores, --memory`) *before* importing/dispatching the provider module. |
| G-4 | Callbacks dispatch as `getattr(get_provider(t), verb)(**kwargs)` — kwargs are exactly the click params (positional included). Impl signatures MUST match param names (conformance-tested set equality; the checker counts `click.Argument` params too). |
| G-5 | Host commands: `target` (when set) becomes a prepended `click.Argument([target.name], required=target.required, default=target.default)`; `options` and `confirmable` behave as in flat extra commands. |
| G-6 | `_resolve_entry_for_destroy`'s user-hint lookup is descriptor-driven: for the `registry_fields` entry whose JSON key ends in `_user`, read `kwargs.get(<json_key>)`. No `"user"` literal. |
| G-7 | `__post_init__` duplicate-option validation covers: `create_options`, `upgrade_options`, `resize_dimensions + resize_options` (combined — cross-collisions are errors), `tag_options`, `destroy_options`, `sync_options`, `info_options`, and each `CommandSpec` in `extra_commands`/`host_commands`. |
| G-8 | Descriptor modules remain metadata-only (no SDK imports); `remo --help`/completion import zero provider SDKs (Spec 018 SC-008). |

## Conformance obligations (FakeProvider, SC-005)

The `fake` descriptor declares: `upgrade_options`/`resize_dimensions`/`tag_options`
(with `supports_managed_marker=True`) and one `host_commands` entry with a `target` — and the
implementation module provides matching `upgrade`/`resize`/`tag` and host-command functions.
The conformance suite then proves, with zero edits to existing files:

1. the generated group contains `upgrade`, `resize`, `tag`, and `host` with the declared shapes;
2. param↔signature set equality holds for the new verbs (positional included);
3. a descriptor with `supports_managed_marker=False` and empty `host_commands` yields no
   `tag` command and no `host` group.
