# Phase 0 Research: CLI Plane Separation

**Feature**: 021-cli-plane-separation | **Date**: 2026-07-28

No `NEEDS CLARIFICATION` items remained after `/speckit-clarify` (six decisions recorded in
spec.md). This document consolidates the codebase audit that grounds the design and records the
implementation-shaping decisions with alternatives considered.

## Codebase facts the design rests on

- **`update` is factory-built** (`cli/providers/factory.py:252-266`): options are
  `[--name, --volume-size, --only, --skip, *descriptor.update_options, -v]`, callback is
  `module.update(**kwargs)`. `--volume-size`/`--only`/`--skip` are factory-injected;
  `--cores`/`--memory`/`--user`/`--devcontainer-runtime` come from `update_options`
  (incus/proxmox only; aws/hetzner declare `update_options=()`).
- **`bootstrap` is nothing but an `extra_commands` `CommandSpec`** (incus_descriptor.py:93-105,
  proxmox_descriptor.py:114-128) mounted flat by `_build_extra_command` (factory.py:315-326).
  `CommandSpec` today supports options only — no positional arguments.
- **Every provider `update()` is already internally three sequential blocks** (marker → resize →
  configure), each with its own private helper: incus.py:371-467 (`_apply_managed_marker` :143,
  `_run_resize_playbook` :197), proxmox.py:499-623 (:140, :276; lazy `_resolve_vmid` :554-555),
  aws.py:595-673 (no marker; inline resize :646-658 + registry IP refresh :634-644),
  hetzner.py:186-264 (`_apply_managed_label` :521, label runs first). The split is a re-grouping
  of existing blocks, not new behavior.
- **`update_entry` (Protocol) already runs the instance-only path**: each impl calls
  `update(..., apply_marker=False)` (#105). `remo shell`'s accepted prompt calls
  `module.update_entry(host)` (cli/shell.py:193).
- **The `--user` value lands in registry fields whose JSON keys already match the new flag
  spellings**: incus `("instance_id", "host_user")`, proxmox `("region", "node_user")`. The
  destroy path routes the CLI hint via `kwargs.get("user")` into whichever registry field's JSON
  key ends in `_user` (factory.py:180-214, esp. :199).
- **Untruthful remedies confirmed**: migration notice prints `remo <type> sync[ --host <host>]`
  (core/known_hosts.py:100-107) though no sync path writes markers; sync's remedy prints
  `Mark permanently: remo <type> update --name <n>[ --host <h>]` (core/reconcile.py:328-350).
- **Surface tripwires**: `tests/unit/cli/surface_baseline.py` freezes the whole surface;
  `test_provider_conformance.py:90-170` asserts click-param ↔ impl-signature set equality per verb
  (with an `apply_marker` carve-out at :104-111) and only sees `click.Option`s (:86-87), so
  positional arguments are invisible to it today; :217 freezes the group's command-name tuple.
- **CI scripts use the old spellings**: `.github/workflows/smoke-test.yml:355,449`
  (`bootstrap --user`, `update --name`); `tests/integration/orbstack.sh:167,171`.

## Decisions

### D1 — Descriptor field migration: `update_options` is replaced, not kept

**Decision**: Remove `ProviderDescriptor.update_options`. Add `upgrade_options`,
`resize_dimensions`, `resize_options`, `tag_options`, `host_commands` (shapes in
contracts/descriptor-schema.md). Extend the `__post_init__` duplicate-option check loop
(provider_registry.py:160-166) to cover every new option-list field.

**Rationale**: `update` ceases to exist (FR-004); a vestigial field would be dead metadata and the
duplicate-check loop is a known silent-skip hazard for unlisted fields. The old field's contents
split cleanly: transport options (`--host`, user flag) + `--devcontainer-runtime` → `upgrade_options`;
`--cores`/`--memory` → `resize_dimensions`; transport options repeat in `resize_options`/`tag_options`.

**Alternatives considered**: Keeping `update_options` as a deprecated alias field — rejected: no
consumer, and Spec 019 established that dead metadata is a defect. A single `verb_options:
dict[str, tuple[OptionSpec, ...]]` map — rejected: loses per-field typing and the frozen-dataclass
ergonomics every existing descriptor uses.

### D2 — `resize` dimension enforcement lives in the factory, driven by `resize_dimensions`

**Decision**: The descriptor declares dimensions explicitly (`--volume-size` for all four;
plus `--cores`/`--memory` for incus/proxmox — no more factory-injected `VOLUME_SIZE` on this verb).
The factory's `resize` callback checks "at least one dimension param is set" *before* dispatching
to the provider and raises `PreconditionError` listing `[spec.name for spec in resize_dimensions]`
(FR-002). Provider `resize()` impls assume at least one dimension.

**Rationale**: One generic check written once beats four copies (Principle II's
duplicated-skeleton rule); the message is mechanically per-provider-correct because it derives
from the same metadata that built the flags. An argument-presence check is argument validation,
which is squarely the CLI layer's job (Principle I).

**Alternatives considered**: Each provider validating in `resize()` — rejected: four hand-written
copies of the same check and message. Making one dimension `required=True` in Click — rejected:
no single dimension is individually required.

### D3 — `tag` availability is gated by the existing `supports_managed_marker` flag

**Decision**: The factory adds the `tag` command only when `descriptor.supports_managed_marker`
is true (incus, proxmox, hetzner). No new boolean; `tag_options` supplies transport flags.
AWS (`supports_managed_marker=False`) gets no command — Click's standard unknown-command error
(US2 scenario 5).

**Rationale**: The flag already exists, already means exactly this (it gates the migration
notice's eligibility at known_hosts.py:85), and reusing it keeps one source of truth.

**Alternatives considered**: A separate `tag_command: bool` — rejected: it could drift from
`supports_managed_marker` and make the migration notice recommend a nonexistent command.

### D4 — `host` subgroup: new `host_commands` descriptor field + positional target on `CommandSpec`

**Decision**: Add `host_commands: tuple[CommandSpec, ...] = ()` to `ProviderDescriptor`. When
non-empty, `build_provider_group` mounts a `host` `click.Group` containing one command per spec.
`CommandSpec` gains `target: ArgumentSpec | None = None` (new frozen dataclass: `name`,
`default: str | None`, `required: bool`) so host commands declare their positional host argument
declaratively — incus bootstrap: `ArgumentSpec("host", default="localhost", required=False)`;
proxmox bootstrap: `ArgumentSpec("host", required=True)` (replacing today's
`PreconditionError` on empty `--host` with Click's missing-argument error). `bootstrap` moves out
of `extra_commands` into `host_commands`; `extra_commands` keeps AWS `stop`/`start`/`reboot`
(instance-plane) and remains flat.

**Rationale**: Matches the docker-1.13/incus-admin shape the spec adopted; the descriptor-only
declaration preserves the fifth-provider guarantee (FR-006). Extending `CommandSpec` (rather than
a parallel host-only spec type) lets `_build_extra_command`'s machinery be reused with one
argument-prepending addition, and gives future instance-plane extra commands positional support
for free.

**Alternatives considered**: Hardcoding `click.Argument(["host"])` for all host commands —
rejected: cannot express incus's `localhost` default vs proxmox's required host. A separate
`HostCommandSpec` — rejected: 90% field duplication with `CommandSpec`.

### D5 — Provider decomposition: three public functions per provider, helpers unchanged

**Decision**: Each provider module replaces `update()` with:

- `upgrade(name, ..., tools_only=(), tools_skip=(), verbose=False)` — validate → guard →
  registry/host lookup → IP resolution → configure playbook. AWS keeps its running-instance
  lookup and `save_known_host` IP refresh (explicitly permitted by FR-001). No `apply_marker`
  parameter anywhere — the marker concern leaves this code path entirely.
- `resize(name, ..., volume_size="", cores=0, memory=0, verbose=False)` — the existing resize
  block, calling the same `_run_resize_playbook`/inline playbook invocations. Proxmox resolves
  VMID (registry-cached, else host-side; `PreconditionError` on failure).
- `tag(name, ...)` — read-before-write marker application: report "already tagged" + return
  (exit 0) when the marker is present; on write failure raise `OperationFailedError` with the
  underlying stderr (NOT the warn-and-continue used by `create`). Incus adds an
  `incus config get user.remo` pre-read; proxmox's `_apply_managed_marker` already reads
  `pct config` (detect tag present); hetzner's `_apply_managed_label` already GETs labels
  (detect label present). Providers without markers (aws) simply don't define `tag`.
- `update_entry(entry, *, verbose=False)` (Protocol, unchanged signature) delegates to
  `upgrade(...)`. `create()` keeps its internal best-effort marker + configure composite.

All three new verbs call `guard_not_added_ssh_host` and `validate_name` exactly as `update` did.

**Rationale**: The three blocks already exist with clean seams; this is a re-grouping that keeps
every private helper, playbook, and extra-var construction byte-identical — minimizing behavior
risk and satisfying SC-001/SC-002 at the existing mock seams.

**Alternatives considered**: A shared `core/` template like `run_destroy` — rejected for now: the
three verbs' step sequences are provider-heterogeneous (lazy VMID, AWS IP refresh, label-first
ordering); forcing a template would need more hooks than it removes duplication.

### D6 — Positional `NAME` on the new verbs, matching impl kwarg `name`

**Decision**: `upgrade`/`resize`/`tag` take the instance positionally via a parameterized
`_instance_argument(descriptor, param="name")` (the existing helper grows a `param` argument;
snapshot commands keep `"instance"`). The click param name `name` matches the provider impl's
existing `name` kwarg, so the factory's `module.verb(**kwargs)` passthrough and the conformance
test's param↔signature set-equality both work unchanged. The conformance helper additionally
learns to include `click.Argument` param names (today it filters to `click.Option` only, which
would make positionals invisible).

**Rationale**: Reuses the completion wiring (`shell_complete=_make_name_completer`) and the
spec's decision that new verbs are positional while `create`/`destroy`/`info` keep `--name`.

**Alternatives considered**: Naming the param `instance` and remapping in the callback —
rejected: gratuitous divergence between click params and impl signatures breaks the
set-equality conformance pattern.

### D7 — `--user` rename mechanics

**Decision**: `incus_descriptor.HOST_USER` becomes a fresh `OptionSpec(name="--host-user",
param="host_user", ...)`; `proxmox_descriptor._NODE_USER` becomes `--node-user`/`node_user`.
Provider impl signatures rename their `user` kwarg accordingly (`host_user` / `node_user`);
internal locals/registry fields untouched. `factory._resolve_entry_for_destroy` stops reading
`kwargs.get("user")` and instead uses the descriptor: for the registry field whose JSON key ends
in `_user`, read `kwargs.get(<json_key>)` — which now equals the param name (`host_user`,
`node_user`) by construction. The shared catalog `USER` spec is removed if (as the audit
indicates) no consumer remains after the rename; `remo add --user` (cli/added.py — the *instance*
login, a different meaning) is out of scope and unchanged. The stale hint at incus.py:115-118
(`Try specifying --user... remo incus update...`) is rewritten for the new surface.

**Rationale**: Param-name = JSON-key symmetry removes the destroy path's magic string; renaming
impl kwargs keeps the conformance set-equality honest; deleting the orphaned catalog entry
follows the 019 hygiene precedent.

**Alternatives considered**: Keeping `param="user"` under the new flag name — rejected: leaves
the misleading name in every signature and keeps the `kwargs.get("user")` coupling.

### D8 — Truthful remedies: three string sites, one command shape each

**Decision**:
- `core/known_hosts.py:_print_tagging_notice` → `remo <type> tag <name>` plus
  `--host <host>` suffix for HOST_SCOPED types; docstring claim that update/sync tag is corrected
  (only `tag` and `create` write markers).
- `core/reconcile.py:render_plan` `mark_cmd` → `remo <type> tag <n>` (same HOST_SCOPED suffix).
- `cli/shell.py` prompts name the exact command: e.g. *"Instance 'dev1' tools are v0.8, local is
  v0.9. Run `remo proxmox upgrade dev1`?"* — accepting still calls `module.update_entry(host)`,
  which now delegates to `upgrade` (US1 scenario 3: the prompt names exactly what runs).
  `_run_provider_update`'s messages say "upgrade".

**Rationale**: FR-009/SC-003 — every printed remedy must be executable and perform the described
action; asserting these strings in tests closes the shipped `sync`-recommendation defect.

### D9 — Test strategy: rewrite the tripwires deliberately, re-home behavior tests

**Decision**: `surface_baseline.py` is rewritten to the new frozen surface (that file *is* the
intentional-breaking-change acknowledgment); conformance verb loops become
`("create", "upgrade", "resize", "info", "sync")` (+`tag` where applicable, the `apply_marker`
carve-out deleted), the group command tuple becomes
`("create", "destroy", "upgrade", "resize", "list", "info", "sync", "snapshot")` + `tag`/`host`
per descriptor; FakeProvider gains `upgrade`/`resize`/`tag` impls and a `host_commands` entry so
the conformance test proves the full new surface descriptor-only (SC-005). Marker tests re-home:
`test_update_applies_marker`-style cases become `tag` tests (strict-failure now), the
`update_entry`-doesn't-touch-host cases become `upgrade` invariants (SC-001's zero-provider-write
assertion for all four providers). Guard tests parametrize over `upgrade`/`resize`/`tag`. New
tests: no-dimension `resize` message, already-tagged no-op, `tag` hard-failure, absent
`tag`/`host` on non-supporting providers, remedy-string truthfulness (SC-003).

**Rationale**: Principle VI (every skip/fail path) + the spec's FR-012 enumeration; keeping the
behavior tests' assertions (rather than deleting them) preserves the characterization value.

### D10 — Rollout: single breaking commit series, CI scripts updated in-change

**Decision**: One PR, conventional-commit `feat(cli)!: ...` with a `BREAKING CHANGE:` footer
enumerating `update` removal, flat-`bootstrap` removal, and the `--user` rename (FR-013).
`.github/workflows/smoke-test.yml` and `tests/integration/orbstack.sh` move to the new spellings
in the same change. Historical archives (docs/feature-history.md, CHANGELOG) keep past-tense
references to the old surface; SC-004's zero-reference rule applies to current-surface docs,
help text, and code.

**Rationale**: The user waived compatibility; a shim would preserve the ambiguity (spec
clarification #2). Release-please surfaces the breaking marker in the changelog.
