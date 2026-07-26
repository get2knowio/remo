# Research: Formal Provider Abstraction

**Feature**: 018-provider-abstraction | **Date**: 2026-07-26

All Technical Context unknowns resolved. Each decision below records what was chosen, why, and what was rejected.

## R1. Protocol style: modules-as-implementations, not classes

**Decision**: Keep providers as free-function modules. The contract is enforced two ways: (a) a `typing.Protocol` (`Provider`) covering the *entry-based* uniform surface (mypy supports modules as protocol implementations, so `providers/aws.py` itself satisfies the Protocol), and (b) descriptor-declared verb signatures for the *heterogeneous* surface (create/destroy options differ per provider by design), verified by an `inspect.signature` conformance test that checks each provider function's keyword parameters exactly match its descriptor's declared options.

**Rationale**: The four modules are 600–1,600 lines each; converting to classes is churn with no behavioral payoff and would break the repo's established free-function style. Signature scan confirmed create/destroy kwargs are irreducibly provider-specific (`server_type/location` vs `instance_type/region/use_spot/iam_profile` vs `host/user/domain/image/...`) — a single typed `create(spec)` protocol method would just move the heterogeneity into an untyped spec bag. Entry-based operations (update-from-entry, snapshot ops, sync probe) genuinely are uniform and get strict Protocol typing.

**Alternatives considered**: (1) ABC base class per provider — rejected: forces class conversion, eager imports, no structural-typing benefit. (2) One `create(spec: CreateSpec)` with a union spec — rejected: erases type safety exactly where providers differ most. (3) Dataclass-of-callables built at import — rejected: duplicates what the module namespace already is.

## R2. Where the descriptor/registry mechanism lives: `core/provider_registry.py`

**Decision**: The *mechanism* (descriptor dataclasses, option catalog, registration/lookup functions) lives in `core/provider_registry.py` — generic, provider-agnostic, stdlib-only. The *data* (four descriptor declarations) lives in per-provider metadata modules `providers/<type>_descriptor.py` that import nothing heavy; `providers/builtin.py` registers all built-ins (one line per provider). Implementation modules are referenced by dotted path and imported only on first verb execution.

**Rationale**: Core must stay free of provider knowledge (architecture rule), but `core/ssh.py`, `core/registry.py`, and `core/reconcile.py` need descriptor-driven data to shed their hard-coded type strings (FR-005/FR-018). A generic registry in core with data injected at registration preserves the layering: core defines the seam, providers fill it. Naming: `provider_registry` (never bare "registry") keeps it verbally distinct from the host registry (`core/registry.py`), per the clarify-phase terminology note.

**Alternatives considered**: (1) Registry in `providers/` — rejected: core would import providers, inverting layering. (2) `importlib.metadata` entry points (true plugin system) — rejected as over-engineering: SC-001 only requires "no existing CLI files touched"; a one-line addition to `providers/builtin.py` is acceptable and keeps registration order deterministic and testable. Revisitable later without contract changes.

## R3. Error taxonomy and the translation boundary

**Decision**: New `core/errors.py`:

- `ProviderError(Exception)` — base; `exit_code = 1`, carries a user-facing message
- `MissingDependencyError(ProviderError)` — optional SDK absent; message names the extra (`pip install remo-cli[aws]`)
- `PreconditionError(ProviderError)` — validation failures, entry not found, wrong state, added-host guard
- `OperationFailedError(ProviderError)` — subprocess/playbook/API failure; carries the underlying `rc`/detail in the message
- `UserAbortedError(ProviderError)` — declined confirmation; `exit_code = 3`

Business verbs return `None` on success and raise on failure — no returned exit statuses (clarify Q1). One translation boundary: a `provider_command` wrapper in the CLI factory catches `ProviderError` → `print_error(msg)` + `sys.exit(exc.exit_code)`. `core/reconcile.run_sync` keeps returning `EXIT_OK/EXIT_FAILURE/EXIT_ABORTED` internally (it is a core driver, i.e. part of the translation boundary — FR-002 permits this) but its provider-facing probe contract raises `ProbeError` as today.

**Rationale**: Exceptions compose with non-CLI consumers (web service) and make "no `sys.exit` in business logic" mechanically checkable. Exit-code *meanings* (0/1/3, 2=Click usage) are preserved per FR-003.

**Normalization note (documented behavior change)**: today `create`/`update` propagate the raw ansible-playbook rc as the process exit code (e.g. 4). Under the contract, any nonzero playbook rc raises `OperationFailedError` → exit 1, with the rc in the message. This falls under FR-003's uniform meanings; called out in the CHANGELOG.

**Alternatives considered**: returning rc ints as canonical — rejected in clarification Q1 (two-style contract isn't uniform; ints are lossy for the web service).

## R4. Enforcement of the no-`sys.exit` / no-private-reach-in gates

**Decision**: An architecture test (`tests/unit/test_architecture.py`) that AST-scans `src/remo_cli/providers/` asserting zero `sys.exit` calls, and scans `src/remo_cli/cli/` asserting zero `noqa: SLF001` and zero private-attribute access into `remo_cli.providers.*`. Additionally `ruff` per-file-ignores for the providers package are removed so any reintroduction fails lint.

**Rationale**: SC-003 requires CI enforcement, not convention. AST scanning is stdlib-only, fast, and immune to formatting.

**Alternatives considered**: custom ruff plugin — rejected (external packaging burden); grep in CI — rejected (fragile to comments/strings).

## R5. CLI generation approach

**Decision**: A single factory `cli/providers/factory.py`: `build_provider_group(descriptor) -> click.Group`. Shared verbs (create, destroy, update, list, info, sync, snapshot create/restore/delete/list) are built from a canonical `OptionSpec` catalog — one object per shared option, so `--volume-size` is literally the same spec everywhere. Provider-specific commands (AWS stop/start/reboot/info, Incus/Proxmox bootstrap) are declared in the descriptor as `CommandSpec`s (name, options, impl function name) and built by the same factory. `cli/main.py` iterates `provider_registry.all_descriptors()` and mounts one group per provider. The four hand-written `cli/providers/{incus,hetzner,aws,proxmox}.py` are deleted.

**Rationale**: Identical flags from identical specs makes SC-002 true by construction; drift becomes impossible rather than discouraged (FR-008/FR-012). Click supports fully programmatic command construction (`click.Command(params=[...])`), no decorators needed.

**Alternatives considered**: (1) Shared decorators applied in four hand-written modules — rejected: keeps four files to drift, fails SC-001's "no CLI files touched". (2) Jinja-style codegen — rejected: generated source to review/commit, no benefit over runtime construction.

**Startup cost (FR-024/SC-008)**: descriptors are pure metadata; the factory builds Click objects only from metadata; impl modules load inside command callbacks via `provider_registry.get_provider(type)` (lazy import + memoize). A test imports `cli.main`, builds the full CLI, and asserts `boto3`/`hcloud` absent from `sys.modules`.

## R6. Shared template placement

**Decision**:
- Destroy sequence → `core/lifecycle.py`: `run_destroy(descriptor, entry_resolution, auto_confirm, teardown_fn)` implementing guard → snapshot pre-cleanup (`core/snapshot.handle_destroy_snapshot_cleanup`) → confirm → teardown → best-effort `remove_known_host`, preserving today's ordering (Edge Case "Interrupted destroy").
- All-instances snapshot aggregation → `core/snapshot.py`: `list_all_snapshots(type_name, lister) -> tuple[list[Snapshot], bool]` (partial-failure flag preserved).
- Configure extra-vars → `core/ansible_runner.py`: `build_configure_extra_vars(tools_only, tools_skip) -> list[str]` (timezone via existing `detect_timezone()` + `build_tool_args` + `remo_version`), replacing all 8 inline copies.
- Resize helper → `core/ansible_runner.py`: `run_resize_playbook(playbook, extra_vars, verbose)` parameterized; Incus/Proxmox private copies deleted.
- Host list table → `core/output.py`: `render_host_table(entries, columns)` with descriptor-declared columns; the four `list_hosts()` implementations become descriptor data.

**Rationale**: Each lands in the existing module that already owns the neighboring concern; no new grab-bag module. Human-readable table output may change formatting (clarify Q5 / FR-025).

## R7. Dispatch-site migration

**Decision**:
- `cli/shell.py` `_run_provider_update`: replaced by Protocol verb `update_entry(entry: KnownHost, verbose: bool = False) -> None` implemented per provider (each absorbs its own name-splitting: incus `host/container`, proxmox node/vmid/user-from-region). Lookup via provider registry; unknown type → `PreconditionError` (explicit error, FR-006); `type == "ssh"` → explicit documented skip (added hosts are not provider-managed).
- `core/ssh.py` SSM branch: descriptor field `connection: ConnectionSpec` with optional `proxy_hook` (dotted path). `build_ssh_base_cmd` asks the provider registry for the host's descriptor; AWS's hook builds the SSM ProxyCommand (absorbing `get_aws_region`). Hosts of types without a hook keep the direct path. `ssh`-type hosts short-circuit before lookup.
- `core/completion.py`: replaced by a generic completer generated from `descriptor.name_format` (`FLAT` vs `HOST_SCOPED` — strip `host/` prefix). Module deleted; factory wires completion.
- `core/reconcile.SyncScope` type-validation and host-scoping: driven by `name_format` + registry membership instead of literal tuples.
- `core/registry.py` per-type serialization map: descriptor field `registry_fields: tuple[str, ...]`; the `ssh` pseudo-type keeps an explicit local definition (it is not a provider; FR-006).

**Rationale**: Each site consumes declared descriptor data (FR-018 "where practical"); none hard-codes type strings afterward. `core/registry.py` keeps a defensive fallback (serialize all known fields + warning) for unknown types so a hand-edited registry never crashes serialization — errors surface at dispatch, not at load (Edge Case "Unknown host type").

## R8. #87 — observed-vs-default merge semantics

**Decision**: `DiscoveredHost` gains `observed: frozenset[str] | None = None` (None ⇒ legacy behavior: all non-empty fields are observed — keeps Incus/Proxmox/Hetzner probes untouched initially). `merge_entry(existing, discovered)` consults it: a field is taken from `discovered` only if observed (and non-empty), else preserved from `existing`. The AWS probe marks `access_mode` observed only when the `remo_access_mode` tag is present; *additions* (no existing entry) always use the discovered value including defaults, so new adoption still yields a working `ssm` mode.

**Rationale**: Implements the "provider asserts only what it observed" contract (FR-019) with zero change to providers that don't need it, and directly implements option 2 from issue #87's write-up.

**Alternatives considered**: (1) blank default (`""`) — rejected in the issue itself (breaks new adoption). (2) split add-vs-merge entry construction paths — rejected: heavier reconcile surgery for the same semantics. (3) wontfix — rejected by clarification Q2.

## R9. Deprecations under the Spec-017 convention

**Decision**: One release window, printed notice, no behavior:
- `--yes/-y` on all four `create` commands: accepted, prints `Deprecated: --yes has no effect on create and will be removed in a future release.`, then removed next release (FR-010).
- No other flag or command changes surface; three default instance names are preserved as descriptor values (clarify Q1).

## R10. Delivery staging (input to /speckit-tasks)

**Decision**: Six stages, each leaving the tree green:
1. **Foundations**: `core/errors.py`, `core/provider_registry.py` (descriptor/OptionSpec/registry), Protocol definition, architecture-test + conformance-test harnesses (initially failing-allowed lists).
2. **Provider contract migration** (per provider, AWS first — worst offender): typed errors replace `sys.exit`/`RuntimeError`, add `update_entry`, entry-based snapshot verbs made public, signatures aligned to descriptors.
3. **Shared templates**: destroy/aggregation/extra-vars/resize/table extracted and consumed by all four providers.
4. **Generated CLI**: factory + descriptors mounted in `cli/main.py`; four hand-written CLI modules deleted; `--yes` deprecation notices; help/behavior uniformity tests.
5. **Dispatch migration**: shell.py, ssh.py SSM hook, completion, reconcile scoping, host-registry serialization.
6. **#87 + hardening + docs**: observed-fields merge, lint-gate flip to zero-tolerance, contributor guide (FR-023), CHANGELOG.

**Rationale**: US1's machinery (stages 1–4) lands before the long tail, per the spec's staging assumption; per-provider stage-2 slices keep PRs reviewable for the largest roadmap item to date.
