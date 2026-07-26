# Feature Specification: Formal Provider Abstraction

**Feature Branch**: `018-provider-abstraction`

**Created**: 2026-07-26

**Status**: Draft

**Input**: User description: "Introduce a formal provider abstraction to replace the current convention-by-copy provider layer, where four free-function modules (providers/{incus,hetzner,aws,proxmox}.py) mirror each other's shape with no base class, Protocol, or registry, and dispatch is done by string-matching host.type at each call site. Define a Provider protocol (lifecycle verbs, sync query, snapshots, uniform error contract), a ProviderDescriptor + registry replacing scattered type-string dispatch, and generated Click command groups replacing the four hand-written CLI modules. Deduplicate the shared skeletons (destroy sequence, snapshot-list aggregation, configure extra-vars, resize helper, registry list table), normalize the inconsistencies (flag drift, default names, ignored --yes, private-helper reach-ins), and migrate provider-specific semantics out of core. Success criterion: a fifth provider means implementing the protocol and registering one descriptor, touching no existing CLI files."

## Context

Remo supports four instance providers (Incus, Proxmox, AWS, Hetzner). Each is implemented twice over, by copy: a business-logic module (`providers/<type>.py`) and a hand-written CLI module (`cli/providers/<type>.py`, ~1,375 lines across the four). There is no shared interface — the modules merely mirror each other's shape, and every consumer that needs provider-specific behavior string-matches on the host's type at its own call site. The audit for this spec confirmed the resulting drift and defects:

- **Silent misdispatch**: the update chain in the shell command matches four type strings and silently returns success for anything else — an unknown or future host type is ignored without any signal.
- **Mixed error contract**: the business layer mixes three failure styles — returned exit codes, 15 direct process-exit calls (12 of them in the AWS module, whose stop/start/reboot/info bypass the return-code convention entirely), and raised generic runtime errors.
- **Accidental inconsistency**: three different default instance names (`dev1` for Incus/Proxmox, `remo` for Hetzner, the login user for AWS); a `--yes` flag accepted on all four create commands but forwarded to nothing; ten `noqa: SLF001` suppressions where CLI modules reach into private provider helpers (all in Incus/Proxmox snapshot paths).
- **Copy-pasted skeletons**: the destroy sequence (guard → snapshot pre-cleanup → confirm → teardown → registry removal) appears four times; the all-instances snapshot-list aggregation loop four times; the configure extra-vars assembly (timezone + tools + version) eight times; the resize-playbook helper twice; the registry list table four times.
- **Provider knowledge leaked into core**: the SSM connection branch and AWS region lookup in the SSH module, per-provider shell completers, and Incus/Proxmox `host/container` name-format handling all live in `core/` or scattered call sites.

Spec 016 already proved the target pattern for one verb: `sync` is a shared engine that providers plug a read-only probe into. This spec extends that pattern to the whole provider surface. It also intersects open issue #87 (AWS sync's always-truthy default access mode silently overwrites a differing existing entry on merge), which lives exactly on the sync-query contract this spec formalizes.

## Clarifications

### Session 2026-07-26

- Q: Preserve or unify the three per-provider default instance names? → A: Preserve current values as descriptor-declared single sources of truth (`dev1` is a historical project choice, not a Proxmox-mandated default); no user-facing change.
- Q: Fold the issue #87 fix in, or reference it as a follow-up? → A: Fold it in — the formalized sync contract distinguishes observed values from defaults; this feature closes #87.
- Q: Which failure-signaling mechanism is canonical for the provider contract? → A: Typed errors — provider business logic raises typed, catchable errors; exit-status integers exist only at the core drivers/CLI boundary that translate them.
- Q: Are the old per-provider public functions a compatibility surface? → A: No — all in-repo consumers (web service, tests) migrate to the new contract within this feature; no compatibility delegates ship in a release. The stable surfaces are the CLI (commands/flags/exit codes), the registry file format, and wire protocols.
- Q: What happens to the accepted-but-ignored `--yes` on the four create commands? → A: Deprecate per the Spec-017 convention — accepted with a printed deprecation notice (no behavior) for one release, then removed. (Audit confirmed no create-path confirmation exists to wire it to.)
- Q: Is CLI startup performance a constraint on descriptor registration? → A: Yes — registration/command generation is metadata-only; optional provider SDKs (boto3, hcloud) are imported only when a command of that provider executes; `remo --help` and shell completion must not regress.
- Q: Is human-readable output (list/snapshot tables, help text) a compatibility surface? → A: No — formatting may change when renderers are unified, provided information content is preserved; only exit codes, flags/arguments, and file/wire formats are stable surfaces.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Add a fifth provider without touching existing files (Priority: P1)

An outside contributor wants to add support for a new provider (e.g., DigitalOcean). They implement the provider interface in one new business-logic module, declare one descriptor (type name, default instance name, which options apply, any provider-specific commands), and register it. The full CLI surface — command group with create/destroy/update/list/sync/snapshot, shell completion, shell/cp dispatch, registry listing — comes into existence from the registration, with the same flags, help text, and exit-code discipline as the existing four providers.

**Why this priority**: This is the stated primary extensibility path for outside contributors and the feature's headline success criterion. Everything else in this spec (protocol, descriptors, generated commands, dedup) is the machinery that makes this journey possible.

**Independent Test**: Register a minimal in-test provider (fake/stub implementation) against the registry and verify its command group, completers, and dispatch appear and behave identically to the built-in providers' — with zero modifications to any existing CLI module.

**Acceptance Scenarios**:

1. **Given** a new provider module implementing the protocol and one registered descriptor, **When** the CLI starts, **Then** `remo <newtype>` exists with the standard command set, standard flags, and standard help text, and no existing CLI file was modified.
2. **Given** the new provider is registered, **When** a host of the new type is stored in the registry and the user runs the shell command's update path, **Then** the new provider's update logic is dispatched — without any change to the shell command module.
3. **Given** a descriptor that opts out of an option (e.g., no `--use-ip`), **When** the user views the generated command's help, **Then** the option is absent for that provider but present for providers that declare it.

---

### User Story 2 - Identical commands behave identically everywhere (Priority: P2)

A Remo user who works across providers runs the same verbs (`create`, `destroy`, `update`, `list`, `sync`, `snapshot …`) against Incus, Proxmox, AWS, and Hetzner. Shared commands present the same flags with the same spellings and semantics, the same help-text conventions, and the same exit codes for the same outcomes; provider-specific additions (e.g., Proxmox `--purge`, AWS `stop`/`start`/`reboot`, region-scoped snapshot commands) remain available and clearly belong to that provider.

**Why this priority**: Inconsistency is today's most user-visible symptom of the convention-by-copy layer. It erodes trust ("does `--yes` even do anything here?") and makes scripting against the CLI fragile.

**Independent Test**: Compare the generated help output and exit-code behavior of every shared command across all four providers; verify flag names, defaults, and confirmation semantics match, and that every advertised flag is honored.

**Acceptance Scenarios**:

1. **Given** any two providers, **When** the user compares a shared command's options, **Then** shared options have identical names, short forms, help text, and semantics.
2. **Given** a destructive command with `--yes`, **When** the flag is passed, **Then** the confirmation prompt is suppressed — identically on every provider.
3. **Given** a create command that advertises a flag, **When** the user passes it, **Then** it has a real effect; no command accepts a flag it silently ignores outside a declared deprecation window (flags with no remaining purpose print a deprecation notice for one release, then are removed).
4. **Given** the four providers' create commands, **When** the user omits `--name`, **Then** the default instance name is the one declared in that provider's descriptor, and the effective default is stated in the command's help text.

---

### User Story 3 - Failures are predictable and never silent (Priority: P2)

A user (or a script wrapping the CLI) hits an error: a provider tool is missing, a cloud API rejects a call, an instance isn't found, or the registry contains a host whose type no software component recognizes. In every case, the outcome is an actionable error message and a meaningful nonzero exit code. No path silently succeeds while doing nothing, and no business-logic path terminates the process on its own.

**Why this priority**: The silent-ignore path in shell dispatch and the AWS module's process-exit habit are latent correctness bugs — they misreport outcomes to users and to scripts. A uniform error contract is also a prerequisite for embedding provider logic anywhere other than the CLI (the web service already imports this layer).

**Independent Test**: Exercise failure paths per provider (missing SDK, unknown host type, failed operation) and assert: nonzero exit code, actionable message, and — for the business layer — no process termination from within business logic.

**Acceptance Scenarios**:

1. **Given** a registry entry whose type matches no registered provider, **When** any dispatching command encounters it, **Then** the user sees an explicit error naming the unknown type (not silent success).
2. **Given** any business-logic failure (e.g., AWS stop on a nonexistent instance), **When** it occurs, **Then** the business layer raises a typed error from the contract's taxonomy and the CLI boundary converts it to the correct exit code and message.
3. **Given** the sync engine, **When** a provider probe fails, **Then** failure semantics are unchanged from Spec 016 (failure exit code, removals never applied on incomplete enumeration).

---

### User Story 4 - Maintainers change shared behavior in one place (Priority: P3)

A maintainer needs to change a shared behavior — e.g., add a step to the destroy sequence, change the snapshot table, or add a variable to the configure assembly. They make the change in exactly one shared template, and all providers pick it up. CLI modules no longer reach into providers' private helpers.

**Why this priority**: This is the compounding-interest payoff. It is lower priority than the user-visible stories only because its value accrues over future changes rather than immediately.

**Independent Test**: Verify each previously-duplicated skeleton now has a single implementation, parameterized per provider, and that the private-helper suppressions are gone.

**Acceptance Scenarios**:

1. **Given** the destroy sequence template, **When** a maintainer adds a step to it, **Then** all four providers' destroy commands gain the step without per-provider edits.
2. **Given** the codebase after this feature, **When** searched for CLI-to-private-provider-helper suppressions (`noqa: SLF001`), **Then** none remain in the provider CLI surface.
3. **Given** the configure extra-vars assembly (timezone + tools + version), **When** counted, **Then** it exists once, used by all create and configure paths.

---

### User Story 5 - Formalized sync contract closes issue #87 (Priority: P3)

A user with a hand-edited or legacy AWS registry entry whose access mode differs from the provider default runs `remo aws sync`. Today the probe's always-present default access mode silently rewrites their entry every run (a spurious "updated" line each time). Under the formalized sync-query contract, a provider only asserts an attribute value it actually observed; absent observations preserve the existing entry's value, so the entry is left alone and the plan shows no phantom update.

**Why this priority**: Small blast radius (hand-edited/legacy entries only) but it sits exactly on the contract boundary this spec formalizes — fixing it here avoids formalizing a known-defective semantic. **Decision**: the #87 fix is folded into this spec (the contract is being rewritten anyway); this feature closes issue #87.

**Independent Test**: Sync against a registry entry whose stored access mode differs from the provider default and no explicit observation (tag) exists; assert the entry is unchanged and the plan reports no update.

**Acceptance Scenarios**:

1. **Given** an AWS entry with a non-default access mode and no explicit access-mode tag on the instance, **When** the user runs sync, **Then** the entry's access mode is preserved and no update line is shown for it.
2. **Given** an instance with an explicit access-mode tag, **When** synced, **Then** the tagged value is applied (observed values still win).
3. **Given** a newly discovered untagged instance with no existing entry, **When** adopted via sync with `--all`, **Then** it still receives a working default access mode (the fix must not break new adoption).

---

### Edge Cases

- **Unknown host type in the registry** (hand-edited file, or a registry written by a newer Remo): every dispatch site must surface an explicit error for that host while continuing to operate on known-type hosts where the operation is per-host (e.g., listing).
- **The `ssh`/added pseudo-type**: hosts registered via `remo added` are not provider-managed. They must remain excluded from provider dispatch by design (e.g., the shell update path and destroy guard), and this exclusion must be explicit and tested — not a side effect of falling through a match chain.
- **Duplicate or conflicting descriptor registration**: registering two descriptors with the same type name must fail loudly at startup, not last-write-wins.
- **Provider-specific commands and flags**: Proxmox `--purge` and `bootstrap`, AWS `stop`/`start`/`reboot`/`info` and region-scoped snapshot flags, Incus `bootstrap` — generation must support per-provider extensions without breaking uniformity of the shared surface.
- **Missing optional SDK** (boto3, hcloud): the lazy-import error experience must be preserved — a clear message naming the extra to install, correct exit code, no traceback.
- **Host-scoped naming** (`host/container` for Incus/Proxmox): completers, sync scoping, and registry lookups must keep working when the name-format knowledge moves from core into the descriptors.
- **Full-migration requirement**: all in-repo consumers of the old per-provider functions (web service, shell/cp dispatch, tests) are migrated to the new contract within this feature; no compatibility delegates ship in a release. Transitional delegates may exist only between commits inside the feature's PR sequence.
- **Interrupted destroy**: the shared destroy template must preserve today's ordering guarantees (snapshot pre-cleanup before teardown; registry removal last and best-effort) so an interruption never orphans registry state differently than today.

## Requirements *(mandatory)*

### Functional Requirements

**Provider contract**

- **FR-001**: The system MUST define a single provider contract covering the lifecycle verbs — create, destroy, update — the Spec-016 desired-hosts sync query (read-only probe returning discovered hosts plus an enumeration-completeness signal), and the snapshot operations (create, restore, delete, list), such that all four existing providers conform to it and a conformance test can verify any implementation against it.
- **FR-002**: The provider contract MUST specify a uniform error contract with one canonical mechanism: business-logic implementations report failure by raising typed, catchable errors from a defined taxonomy. Business logic MUST NOT terminate the process directly (today: 15 direct process-exit sites in the business layer, 12 in AWS) and MUST NOT return ad-hoc exit statuses; exit-status integers exist only at the translation boundary — the core drivers and the CLI layer — which converts typed errors into process exit codes and user-facing messages in exactly one place.
- **FR-003**: The existing exit-code meanings (0 success, 1 failure, 3 user-aborted; 2 reserved for CLI usage errors) MUST be preserved and applied uniformly across all providers and verbs.

**Descriptor and registry**

- **FR-004**: The system MUST define a provider descriptor carrying per-provider CLI metadata: type name, display name, default instance name, which shared options apply (host/user, region/location, use-ip, devcontainer-runtime, cores/memory/volume-size, etc.), provider-specific commands and flags, name format (flat vs host-scoped), connection semantics (e.g., SSM vs direct SSH), and shell-completion behavior.
- **FR-005**: The system MUST provide a single registry of descriptors that is the sole source of provider dispatch. All current type-string match sites MUST be replaced by registry lookups, including: the shell command's update chain, sync scope validation, registry serialization's per-type field mapping, name-format handling in host lookups, and per-provider completers.
- **FR-006**: A registry lookup for an unrecognized type MUST produce an explicit, actionable error; no dispatch site may silently skip or silently succeed for an unknown type. The `ssh`/added pseudo-type MUST be handled as an explicit, documented exclusion (it is not a provider), preserving today's user-visible behavior for added hosts.
- **FR-007**: Registering a descriptor with a type name that is already registered MUST fail at registration time with a clear error.

**Generated CLI**

- **FR-008**: The per-provider CLI command groups MUST be generated from the descriptors, replacing the four hand-written CLI modules. For any command shared by multiple providers, the generated flags, spellings, short forms, defaults, help-text conventions, and confirmation semantics MUST be identical across providers; per-provider differences may exist only where the descriptor declares them.
- **FR-009**: The generated CLI MUST preserve today's public command surface: every currently working command invocation (names, flags, arguments) MUST keep working, except where this spec explicitly normalizes behavior — and any removed or renamed flag MUST go through the project's established one-release deprecation convention (accepted, warns, delegates).
- **FR-010**: The accepted-but-ignored `--yes` flag on the four create commands MUST be deprecated per FR-009: accepted with a printed deprecation notice (and no behavior) for one release, then removed (audit confirmed no create-path confirmation exists to wire it to). No generated command may advertise a flag with no effect, other than flags in their declared deprecation window.
- **FR-011**: Default instance names MUST be declared in each provider's descriptor as the single source of truth and surfaced in generated help text. The current per-provider values are preserved unchanged (`dev1` for Incus and Proxmox — historical project choices, not provider-mandated values — `remo` for Hetzner, the login user for AWS); unifying the values is out of scope, and no user-facing default changes.
- **FR-012**: Destructive-command confirmation MUST be uniform: the same flag spelling (`--yes`/`-y`) and the same internal semantics on every provider (the audit found spellings already aligned but parameter semantics drifting; generation must make future drift impossible).

**Shared templates (deduplication)**

- **FR-013**: The destroy sequence (added-host guard → snapshot pre-cleanup → confirmation → provider teardown → best-effort registry removal) MUST exist as one shared, provider-parameterized template used by all providers, preserving today's ordering and best-effort semantics.
- **FR-014**: The all-instances snapshot-list aggregation (iterate a provider's registry slice, collect per-instance snapshots, report partial failures) MUST exist once, shared by all providers.
- **FR-015**: The configure extra-vars assembly (timezone detection + tool selection + version pin) MUST exist once, used by every create and configure path (currently 8 inline copies).
- **FR-016**: The resize-playbook helper and the registry list table rendering MUST each exist once, parameterized per provider.
- **FR-017**: CLI code MUST NOT access private provider helpers; the ten current `noqa: SLF001` suppressions in the provider CLI surface MUST be eliminated by promoting the needed operations into the provider contract.

**Provider semantics out of core**

- **FR-018**: Provider-specific knowledge currently in core MUST migrate to the descriptors/providers where practical: the SSM connection branch and AWS-region lookup in the SSH command builder, the per-provider shell completers, and Incus/Proxmox host-scoped name-format handling. Where full migration is impractical in this feature, the remaining core touchpoint MUST consume descriptor-declared data rather than hard-coded type strings.

**Sync contract and #87**

- **FR-019**: The formalized sync-query contract MUST distinguish observed attribute values from provider defaults, such that merging a discovered host into an existing entry only overwrites attributes the provider actually observed (closing issue #87's silent access-mode overwrite), while newly adopted hosts still receive working defaults. This feature closes issue #87.
- **FR-020**: All Spec-016 sync behaviors MUST be preserved: scope-first plan rendering, consent gating for removals, single conflict-checked registry write, enumeration-completeness gating of removals, and the `--yes`/`--dry-run`/`--all` flag semantics.

**Compatibility and verification**

- **FR-021**: The existing automated test suite MUST pass after the change, with test updates limited to the explicitly normalized behaviors and the contract migration itself; the registry file format, the remote host protocol, and the web service's observable behavior MUST be unaffected (the web service's imports are migrated to the new contract per the full-migration decision).
- **FR-022**: The feature MUST include a conformance test exercised against all registered providers plus a minimal in-test provider, proving the fifth-provider journey (User Story 1) without modifying existing CLI files.
- **FR-023**: Contributor documentation MUST describe the fifth-provider path: implement the contract, declare a descriptor, register it — with the conformance test as the acceptance gate.
- **FR-024**: Descriptor registration and CLI command generation MUST be metadata-only at startup: optional provider SDKs (boto3, hcloud) are imported only when a command of that provider actually executes, preserving today's lazy-import behavior and error experience; `remo --help` and shell completion MUST NOT trigger provider SDK imports.
- **FR-025**: Human-readable output (list tables, snapshot tables, plan rendering, help text) is NOT a byte-level compatibility surface: unified renderers MAY change formatting provided the information content is preserved. Exit codes, command/flag names and semantics, the registry file format, and wire protocols ARE the stable surfaces and MUST NOT change.

### Key Entities

- **Provider contract (protocol)**: The behavioral interface every provider implements — lifecycle verbs, sync probe, snapshot operations — plus the uniform error contract. Consumed by the generated CLI, the shell/cp dispatch, the sync engine, and the web service.
- **Provider descriptor**: Declarative per-provider metadata — type name, default instance name, applicable options, provider-specific commands, name format, connection semantics, completion behavior. One per provider; the unit of registration.
- **Provider registry**: The single lookup table from type name to (descriptor, implementation). Sole dispatch mechanism; rejects duplicates; explicit errors for unknown types.
- **Typed provider errors**: The catchable error taxonomy business logic raises (e.g., precondition failure, missing dependency, operation failure, user abort) that the CLI boundary maps to exit codes and messages.
- **Shared command templates**: The parameterized single implementations of destroy, snapshot aggregation, extra-vars assembly, resize, and list-table rendering.
- **Registry entry (existing)**: Unchanged. The stored host record (format v2) whose `type` field is what the provider registry resolves.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A fifth provider can be added by creating new files (implementation + descriptor registration) only — demonstrated by the in-test provider in the conformance suite adding a complete command group with zero modifications to existing CLI modules.
- **SC-002**: 100% of shared commands present identical flag names, spellings, defaults-declaration, and confirmation semantics across all four providers, verified by an automated cross-provider help/behavior comparison.
- **SC-003**: Zero direct process-exit calls remain in provider business logic (down from 15), and zero private-helper lint suppressions remain in the provider CLI surface (down from 10) — both enforced by lint/CI check, not convention.
- **SC-004**: Zero silent-ignore dispatch paths: an unknown host type produces an explicit error at every dispatch site, verified by tests (including the shell update path that today returns success silently).
- **SC-005**: Each of the five duplicated skeletons (destroy sequence, snapshot aggregation, extra-vars assembly ×8, resize helper, list table) exists exactly once, verified by inspection/tests; the four hand-written per-provider CLI modules (~1,375 lines) are replaced by declarative descriptors.
- **SC-006**: The full pre-existing test suite passes; every advertised CLI flag has an observable effect; no user-reported behavior change outside the explicitly normalized items and their deprecation notices.
- **SC-007**: A sync against an entry whose stored connection mode differs from the provider default (with no explicit observation) leaves the entry untouched — zero phantom "updated" lines, closing issue #87.
- **SC-008**: Invoking top-level help or shell completion imports zero optional provider SDKs (test-verified), preserving today's startup and lazy-import behavior.

## Assumptions

- **Destroy-flag spellings**: The description cites "three different destroy-flag spellings"; the audit found all four providers currently accept `--yes`/`-y` (drift exists in internal parameter naming and per-provider extras like Proxmox `--purge`, not the user-facing spelling). The requirement is therefore stated as guaranteed uniformity via generation (FR-012), not a user-facing spelling change.
- **Deprecation convention**: The project's established pattern (Spec 017's `remo web adopt` alias — accepted for one release, prints a notice, delegates) is the mechanism for any flag or command this spec renames or removes.
- **`bootstrap` stays provider-specific**: Only Incus and Proxmox have `bootstrap`; it remains a descriptor-declared provider-specific command, not part of the shared verb set.
- **AWS `stop`/`start`/`reboot`/`info`** remain AWS-specific commands but are brought under the uniform error contract (they are today's worst process-exit offenders).
- **The `ssh`/added pseudo-type** stays outside the provider registry: added hosts are user-registered, not provider-managed. Their handling becomes an explicit exclusion rather than a fall-through.
- **No registry schema change**: This feature changes code structure and CLI behavior only; registry format v2, the remote-host protocol, and all web-service wire formats are untouched.
- **Behavior preservation is the default**: Except for the explicitly listed normalizations, every currently passing user workflow continues to work unchanged; all in-repo consumers (including the web service) are migrated to the new contract in this feature, with no released compatibility delegates.
- **Scale expectation**: This is flagged as the largest roadmap item to date — larger spec/plan/task surface than Spec 017 is expected and accepted; the plan phase should stage delivery so User Story 1's machinery lands before the long tail of dedup/migration items.
