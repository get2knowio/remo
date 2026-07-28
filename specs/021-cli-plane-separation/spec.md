# Feature Specification: CLI Plane Separation — Intent-Named Verbs and a Host Subgroup

**Feature Branch**: `021-cli-plane-separation`

**Created**: 2026-07-28

**Status**: Draft

**Input**: User description: "Research our CLI surface area and suggest a logical separation between commands that act outside a remo instance (on a proxmox/incus host or against the AWS/Hetzner APIs) and those which operate inside the instance. Today `remo <provider> update` uses Ansible to update the instance, and host functionality is starting to land there too. We need CLI clarity on actions which impact an instance (such as my dev1 LXC container) vs. those which impact the host (such as my lab1 proxmox host). No backwards compatibility is necessary in the CLI surface area if there's a better way to handle it."

## Context

A full audit of the command surface (every verb classified by which machine each step touches: local registry, cloud control plane, hypervisor host, or inside the instance) found the CLI is already single-plane at its edges — `bootstrap` touches only the host, `list`/`remove` only the local registry, `sync` is a provider-side read plus a local write, `cp` only the instance. The ambiguity is concentrated in one verb: **`update` performs three unrelated jobs in a single invocation, and a different subset of them per provider**:

| Step inside today's `update` | incus | proxmox | aws | hetzner |
|---|---|---|---|---|
| Managed-marker write (outside) | `incus config set` on host | `pct set --tags` on node | — (no marker) | API label PUT |
| Resize (outside) | disk/cores/memory | rootfs/cores/memory | volume only (+ in-guest fs grow) | volume only (+ in-guest fs grow) |
| Dev-tools configure playbook (inside) | ✅ | ✅ | ✅ | ✅ |

Observed consequences:

- A user who only wants to backfill the managed marker on a legacy container has no command for it — the closest verb (`update`) launches a full in-instance reconfigure as a side effect (reported in the field: the user Ctrl-C'd a playbook they never asked for).
- The registry-migration notice and `sync`'s "Mark permanently:" remedy line have no truthful command to print. The notice currently recommends `remo <type> sync`, which never writes a marker (the audit confirmed no marker-write call exists in any sync path, contradicting a docstring claim shipped with #105).
- The host plane has no home. `bootstrap` (which configures the *hypervisor* — packages, storage pool, network) sits in `--help` as an undifferentiated sibling of instance verbs, and any new host functionality would land in the same flat bucket.
- On incus/proxmox, `--user` means the login on the hypervisor, not the instance — a distinction #105 could only express through help text.

Precedent research across kubectl, incus/LXD, Proxmox tooling, docker, multipass/lima/vagrant/colima, gcloud, virsh, flyctl, and talosctl found two well-documented CLI restructurings (docker 1.13's management-command regrouping; LXD→Incus moving daemon commands into `incus admin`), both triggered by exactly this failure mode: flat-namespace ambiguity about what a verb operates on. Both landed on the same shape — common-case workload verbs stay terse and flat, host-plane operations are quarantined in one named subgroup, single binary. Separate host-management binaries (LXD's `lxd`, docker-machine) are documented retreats. Small VM managers (vagrant, multipass, lima) name the inside-the-guest operation as its own verb (`provision`, `exec`, `shell`) and never fold it into a lifecycle verb.

The organizing principle adopted by this spec: **a command is named and grouped by the resource whose state it changes, not by where it executes.** `create` runs `pct create` on the node, but what changes in the world is that an instance exists — it is an instance command. `host bootstrap` changes the host itself. Execution locus cannot be the organizing principle: for AWS/Hetzner every verb executes outside the instance, so grouping by locus would carry no information. A verb's steps may span planes (create provisions then configures; a volume resize grows the volume via the provider and the filesystem in-guest) provided they serve one user intent; `update` is defective because it bundles three intents, so its blast radius cannot be predicted from its name.

## Clarifications

### Session 2026-07-28

- Q: What is the inside-plane verb named? → A: `upgrade` — the playbook's first act is a full apt upgrade, so the verb is honest even when remo itself hasn't changed; it also covers "I upgraded remo locally, refresh the instance's remo tooling." Single verb, no `configure` synonym/alias (two names for one command would reintroduce ambiguity, and the first-touch case is covered by `create`, which runs the same configure play internally).
- Q: Is `update` kept as an alias or deprecated shim? → A: No — removed outright. The user explicitly waived CLI backward compatibility, and an alias would preserve the ambiguity being removed. clig.dev's `update`-vs-`upgrade` confusability warning is moot once `update` no longer exists.
- Q: Host operations: full noun split (`remo <p> instance <verb>`), separate binary, or subgroup? → A: A `host` subgroup under each provider (`remo <p> host <verb> HOST`). Instance verbs stay flat — they are the frequent case; host ops are occasional. Matches the incus-admin/docker-1.13 precedent; separate binaries are documented retreats.
- Q: How do the new verbs address their target? → A: New instance verbs (`upgrade`, `resize`, `tag`) take the instance name as a positional argument, matching the existing `snapshot` subcommands; host verbs take the host as a positional argument. Pre-existing verbs (`create`, `destroy`, `info`) keep `--name` unchanged — converging them is out of scope.
- Q: What happens to `--user` on incus/proxmox? → A: Renamed to `--host-user` (incus) / `--node-user` (proxmox) on every verb where it appears, completing #105's help-text fix at the flag level. Clean break, no deprecated alias, same as `update`'s removal.
- Q: Does `tag` exist for every provider? → A: Only where the descriptor declares managed-marker support (incus, proxmox, hetzner today). AWS has no marker write; it gets no `tag` command rather than a stub.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Refresh an instance's software with one predictable verb (Priority: P1)

A user upgrades remo on their workstation and wants the instance to pick up the updated remo-specific tooling (and current apt packages). They run `remo proxmox upgrade dev1` — or accept `remo shell`'s version-mismatch prompt, which now names that exact command. Only the in-instance play runs: no hypervisor marker write, no resize, no VMID resolution beyond what the SSH hop itself requires.

**Why this priority**: This is the most frequently executed mixed verb today and the primary maintenance workflow of the tool's users. It is also the verb whose current name (`update`) is the root ambiguity.

**Independent Test**: Run `upgrade` against an instance on each provider and assert the configure playbook runs while no marker-write or resize operation is invoked (mockable at the provider-implementation seam).

**Acceptance Scenarios**:

1. **Given** a registered incus or proxmox instance, **When** the user runs `remo <type> upgrade <name>`, **Then** the dev-tools configure playbook runs against the instance and no hypervisor state (marker, limits, disk) is written.
2. **Given** a registered aws or hetzner instance, **When** the user runs `remo <type> upgrade <name>`, **Then** the configure playbook runs; provider API access is limited to reads needed to reach the instance (plus AWS's existing local registry IP refresh), and no label/marker or volume mutation occurs.
3. **Given** `remo shell` detects a version mismatch, **When** it offers the tools update, **Then** the prompt names `remo <type> upgrade <name>` and accepting it performs exactly User Story 1's operation.
4. **Given** `--only`/`--skip` component selection, **When** passed to `upgrade`, **Then** they behave as they did on the removed `update` verb.

---

### User Story 2 - Tag a legacy instance without touching anything else (Priority: P2)

A user who migrated a pre-tagging registry sees `sync` report containers as unmarked. They run `remo proxmox tag jump` (host-scoped: `--host` resolvable from the registry as today). The managed marker is written — one hypervisor/API call — and nothing else happens. The migration notice and `sync`'s "Mark permanently:" remedy line print this command.

**Why this priority**: This closes a shipped defect: the current migration notice recommends a command (`sync`) that cannot tag, and the only command that can (`update`) launches a full reconfigure. The population that needs this is every pre-013 instance.

**Independent Test**: Run `tag` against an untagged instance and assert exactly one marker write occurs and no playbook runs; run it again and assert a no-op (idempotent).

**Acceptance Scenarios**:

1. **Given** an untagged registered instance on a marker-supporting provider, **When** the user runs `remo <type> tag <name>`, **Then** the managed marker is written and no configure playbook, resize, or other mutation occurs.
2. **Given** an already-tagged instance, **When** `tag` runs, **Then** it reports the instance is already tagged and exits 0 without writing (Principle VII).
3. **Given** a registry migration that includes marker-supporting provider types, **When** the migration notice prints, **Then** it recommends the `tag` command (not `sync`), and following the recommendation actually tags.
4. **Given** a `sync` plan that reports unmarked instances, **When** the remedy line prints, **Then** it names `remo <type> tag <n>` and no longer points at the removed `update`.
5. **Given** `remo aws tag`, **When** invoked, **Then** the command does not exist (AWS declares no managed marker) and Click reports an unknown command.

---

### User Story 3 - Resize an instance without reconfiguring it (Priority: P2)

A user wants more resources for dev1. They run `remo proxmox resize dev1 --memory 8192` (or `--cores`, `--volume-size`; on aws/hetzner, `--volume-size` only). The resource change is applied — including, where the provider requires it, the in-guest filesystem grow that completes the single "make it bigger" intent — and the dev-tools playbook does not run.

**Why this priority**: Second of the three intents trapped inside `update`. Less frequent than `upgrade` but currently forces a multi-minute reconfigure to change a cgroup limit.

**Independent Test**: Run `resize` with each dimension flag per provider and assert the resize path runs and the configure playbook does not; run with no dimension flags and assert an actionable error.

**Acceptance Scenarios**:

1. **Given** an incus/proxmox instance, **When** the user passes any of `--cores`/`--memory`/`--volume-size`, **Then** the corresponding host-side resize is applied and no configure playbook runs.
2. **Given** an aws/hetzner instance, **When** the user passes `--volume-size`, **Then** the volume grows via the provider API and the in-guest filesystem grow runs (one intent, two planes — allowed), with no dev-tools playbook.
3. **Given** `resize` invoked with no dimension flags, **When** it runs, **Then** it fails with an actionable message listing the dimensions available on that provider (exit 1, Principle III).
4. **Given** aws/hetzner, **When** the user views `resize --help`, **Then** `--cores`/`--memory` are absent (CPU/RAM are creation-time properties there today; adding a rescale is out of scope).

---

### User Story 4 - Host operations have one explicit home (Priority: P3)

An operator preparing infrastructure runs `remo incus host bootstrap lab2 --network-type bridge` or `remo proxmox host bootstrap lab1`. `remo <type> --help` shows `host` as a distinct subgroup, signaling "this changes the hypervisor, not an instance." Future host functionality (host health checks, host-level updates) lands under this subgroup and nowhere else. Providers without a host plane (aws, hetzner) have no `host` subgroup — the asymmetry is visible rather than implicit.

**Why this priority**: Prevention rather than cure — it gives the "host functionality creeping into instance verbs" trend a designated destination before more of it ships. Lower priority because only `bootstrap` moves today.

**Independent Test**: Verify `bootstrap` exists only under `host` for incus/proxmox, takes the host positionally, is absent for aws/hetzner, and that the descriptor mechanism generates the subgroup for a test provider declaring host commands.

**Acceptance Scenarios**:

1. **Given** incus or proxmox, **When** the user runs `remo <type> host bootstrap <host>` with provider-appropriate options, **Then** today's bootstrap behavior runs against that host.
2. **Given** incus or proxmox, **When** the user runs the old flat `remo <type> bootstrap`, **Then** Click reports an unknown command (clean break, consistent with `update`'s removal).
3. **Given** aws or hetzner, **When** the user inspects `remo <type> --help`, **Then** no `host` subgroup appears.
4. **Given** a descriptor declaring host commands, **When** the CLI is generated, **Then** the `host` subgroup and its commands exist without modifying any existing CLI file (extends the fifth-provider guarantee, SC-001 of Spec 018).

---

### Edge Cases

- **`upgrade`/`tag`/`resize` against an unregistered name**: same resolution and error behavior as today's `update` — actionable "not found, run sync" style message; the added-SSH-host guard (`type="ssh"` entries) applies to all three new verbs exactly as it applied to `update`.
- **Transport reads are not plane violations**: `upgrade` on incus/proxmox may resolve the container IP via the hypervisor; on AWS it may describe the instance and refresh the locally cached IP. The invariant is *no provider-side writes* from `upgrade`.
- **`tag` when the hypervisor call fails**: raises `OperationFailedError` → exit 1 with the underlying stderr, matching the taxonomy; it must not warn-and-continue the way create's best-effort marker write does (an explicit `tag` exists to tag).
- **Proxmox `tag` needs a VMID**: resolution (registry-cached, else host-side lookup) is a transport read and allowed; failure to resolve is a `PreconditionError`.
- **Removed surface**: `remo <type> update` and flat `remo <type> bootstrap` cease to exist — Click's standard unknown-command error, no custom shim. All documentation, notices, prompts, and error remedies must stop referencing them (Principle VIII; the docs-structure and help-text tests must not find stragglers).
- **`--host-user`/`--node-user` rename**: applies to every incus/proxmox verb that had `--user` (create, destroy, info, sync, host bootstrap, and the new verbs). The registry format is untouched — only the flag spelling changes.
- **`create` remains composite**: provisioning + first configure is one intent ("give me a working dev env"); its internal marker write stays best-effort warn-and-continue as today.
- **Idempotency (Principle VII)**: `tag` twice → second run no-op; `upgrade` twice → converges (playbook is already idempotent); `resize` to the current size → provider-level no-op or clean success, never an error loop.
- **Conformance/fake provider**: the Spec-018 `FakeProvider` conformance test extends to the new verbs and the `host` subgroup so a fifth provider gets the full new surface from its descriptor alone.

## Requirements *(mandatory)*

### Functional Requirements

**Verb decomposition**

- **FR-001**: The system MUST provide `remo <type> upgrade NAME` on every provider, performing exactly the in-instance configure play (apt upgrade + dev-tools + remo tooling, honoring `--only`/`--skip` and provider-declared options such as `--devcontainer-runtime`). It MUST NOT write any provider-side state (marker, labels, limits, volumes); provider-side reads required to reach the instance, and AWS's existing local registry IP refresh, are permitted.
- **FR-002**: The system MUST provide `remo <type> resize NAME` accepting the descriptor-declared dimension flags (`--cores`/`--memory`/`--volume-size` for incus/proxmox; `--volume-size` for aws/hetzner). It MUST apply only the resize (including any in-guest filesystem grow the provider's volume resize requires) and MUST NOT run the configure play. Invocation with no dimension flag MUST fail with a message listing that provider's available dimensions.
- **FR-003**: The system MUST provide `remo <type> tag NAME` on providers whose descriptor declares managed-marker support, performing only the marker write. Already-tagged instances are a reported no-op with exit 0. A failed marker write raises the typed taxonomy (exit 1) — not the warn-and-continue used by `create`'s best-effort tagging. Providers without marker support MUST NOT expose the command.
- **FR-004**: `remo <type> update` MUST be removed from every provider with no alias, shim, or deprecation window (explicit user decision waiving CLI compatibility). The three new verbs together MUST cover every capability `update` had.

**Host subgroup**

- **FR-005**: Each provider whose descriptor declares host-targeting commands MUST expose them under a `remo <type> host` subgroup; providers declaring none MUST NOT have the subgroup. `bootstrap` moves under `host` for incus and proxmox, taking the target host as a positional argument; the flat `bootstrap` spelling is removed.
- **FR-006**: The descriptor/factory mechanism MUST generate the `host` subgroup, the new instance verbs, and their option sets from descriptor metadata alone, preserving Spec 018's fifth-provider guarantee (new provider = one module + one descriptor, zero edits elsewhere) — verified by extending the existing conformance test.
- **FR-007**: The naming rule MUST be recorded in contributor documentation: commands are named and grouped by the resource whose state they change (instance verbs flat, host verbs under `host`); a verb's steps may span planes only in service of a single user intent. Future host functionality lands under `host`, never as a flat provider verb.

**Flag clarity**

- **FR-008**: The `--user` option on incus/proxmox verbs MUST be renamed `--host-user` (incus) and `--node-user` (proxmox) everywhere it appears, with help text stating it is the hypervisor login (the in-instance account remains `remo`, unchanged). No stored registry fields change.

**Truthful notices and docs**

- **FR-009**: The registry-migration tagging notice MUST recommend the `tag` command; `sync`'s "Mark permanently:" remedy line MUST name `tag`; `remo shell`'s version-mismatch prompt MUST name `upgrade`. Each printed remedy MUST be a command that performs the described action (the current notice's `sync` recommendation is a shipped defect this feature closes).
- **FR-010**: The docstring/comment claims that `sync` writes markers MUST be corrected; all user-facing docs (README, docs/*, CLAUDE.md command tables and structure diagram) MUST reflect the new surface in the same change (Principle VIII), with zero remaining references to `remo <type> update` or flat `bootstrap`.

**Behavior preservation and quality**

- **FR-011**: Everything not named above is unchanged: `create`/`destroy`/`list`/`info`/`sync`/`snapshot …`, `remo aws stop|start|reboot`, `remo shell`/`cp`/`add`/`remove`/`web …`, exit-code discipline (0/1/3), the registry file format, the remote-host protocol, and all web-service wire formats.
- **FR-012**: Every new verb's error and skip paths MUST be covered by tests (Principle VI): unregistered name, added-SSH-host guard, no-dimension `resize`, failed marker write, already-tagged no-op, absent-`tag`/absent-`host` providers.
- **FR-013**: The change MUST carry a breaking-change conventional-commit marker so release automation surfaces the removed/renamed surface (`update`, flat `bootstrap`, `--user`) in the changelog.

### Key Entities

- **Instance verb set (per provider)**: `create`, `destroy`, `upgrade`, `resize`, `tag` (marker-supporting providers only), `list`, `info`, `sync`, `snapshot …`, plus provider extras (`aws stop/start/reboot`). All named for their effect on one instance.
- **`host` subgroup (per provider, optional)**: Descriptor-declared commands whose target is the hypervisor host; currently `bootstrap` on incus/proxmox.
- **Provider descriptor (extended)**: Gains declarations for host commands and for the new verbs' option sets (resize dimensions, upgrade options); remains the single source from which the factory generates the surface.
- **Registry entry (existing)**: Unchanged in format and semantics.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `upgrade` on every provider performs zero provider-side writes — test-verified at the provider seam (no marker/label call, no resize call) for all four providers.
- **SC-002**: `tag` completes with exactly one provider-side write and zero Ansible invocations; a second run writes nothing. Tagging a legacy container no longer requires (or triggers) an instance reconfigure.
- **SC-003**: Every remedy string the CLI prints (migration notice, sync remedy, shell prompt) names a command that performs the described action — regression-tested by asserting the printed command against the real command surface.
- **SC-004**: `remo <type> --help` output contains no verb whose execution can touch a plane its name and help text don't state; `host` appears as a subgroup only on providers with host commands. Zero occurrences of `update` as a provider verb anywhere in code, help, or docs.
- **SC-005**: The extended conformance test proves a fifth provider obtains `upgrade`/`resize`/`tag`/`host` purely from its descriptor, modifying no existing files.
- **SC-006**: Full pre-existing test suite passes, with updates limited to the renamed/removed surface; docs-structure and schema-drift gates stay green.

## Assumptions

- **Breaking release accepted**: The user explicitly waived CLI compatibility. Removal of `update`/flat `bootstrap` and the `--user` rename ship as a clean break with changelog visibility (FR-013); release timing/versioning is out of scope for this spec.
- **`create` stays composite**: One intent, multiple planes — consistent with the naming rule, and with vagrant/multipass precedent.
- **No CPU/RAM rescale for aws/hetzner**: `resize` exposes only what exists today (`--volume-size`); adding instance-type/server-type rescale is a separate future feature.
- **Existing verbs keep `--name`**: Only the new verbs (and `host` commands) use positional targets; converging `create`/`destroy`/`info` onto positional arguments is deliberately out of scope to keep the diff reviewable.
- **`tag` semantics per provider**: incus `config set user.remo=true`, proxmox `pct set --tags` (append, preserving existing tags), hetzner API label — each is the provider's existing `_apply_managed_marker`/label mechanism, now reachable directly.
- **`remo shell`'s embedded update path** already runs the instance-only operation (`apply_marker=False` since #105); this spec renames what it offers, not what it does.
- **No new runtime dependencies; no registry schema change.**
