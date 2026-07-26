# Feature Specification: Unified Sync Reconcile

**Feature Branch**: `016-sync-reconcile`

**Created**: 2026-07-25

**Status**: Draft

**Input**: User description: "Rework `remo <provider> sync` from four hand-rolled clear-then-repopulate implementations into one shared reconcile primitive with identical semantics across incus, hetzner, aws, and proxmox. Each provider contributes only a 'desired hosts' query returning the set of KnownHost entries for a stated scope (provider type, type+region for AWS, or type+host for incus/proxmox); shared core code diffs desired vs. current registry entries within that scope and applies the result as one atomic registry rewrite, reporting added / removed / unchanged. This must fix three concrete bugs: (1) AWS region wipe; (2) AWS stopped-instance wipe; (3) empty-result wipe. Preserve existing behaviors: sync never mutates provider-side state; managed-marker semantics still determine membership; incus/proxmox `--all` adoption and `--use-ip` still work. Where practical, close the marker asymmetry so hetzner/aws can also adopt matching-but-unlabeled instances behind an explicit flag."

## Overview

`remo <provider> sync` reconciles the local host registry with what actually exists at a provider. Today each of the four providers implements this independently as *clear the registry, then re-add whatever the query returned*. That shape is destructive by construction: the clear is unconditional, it is often broader than the query that repopulates it, and it happens before anyone knows whether the query returned anything useful.

This feature replaces the four implementations with a single reconcile behavior. A provider's only job becomes answering one question — *"for this scope, what hosts should be in the registry?"* — and shared logic does the rest: compute a plan against the current registry, show the user what will change, get consent for anything destructive, and commit the result in one atomic write.

It also separates two questions the current code conflates. The managed marker decides what sync *pulls in*; whether a host still exists at the provider decides what sync *drops*. Treating an unmarked host as a deletion candidate is what makes today's `--all` adoptions evaporate on the next run, and it is why an unlabeled fleet reads as an empty one.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync never silently deletes registry entries (Priority: P1)

A user runs `sync` in a situation where the provider query comes back empty or partial — wrong credentials, wrong region, a provider outage, a host that is temporarily unreachable, or instances that were never marked as remo-managed. Today the registry is emptied for that provider before the user is told anything, and the only message they see is a cheerful "no instances found". The user loses the record of every environment they can reach, with no undo.

Under this feature, sync always computes a plan first. If the plan removes nothing, it applies silently. If the plan would remove any entry, sync names each entry it intends to remove, asks for explicit confirmation, and makes no change at all if the user declines.

**Why this priority**: This is the data-loss guard that makes every other change safe. It is also the only fix that protects users from failure modes nobody has enumerated yet — a query that returns partial results for any reason is caught by the same gate.

**Independent Test**: Populate a registry, force a provider query to return zero results, run sync, decline the prompt, and verify the registry is byte-identical to its prior state. Repeat with `--yes` and verify the removals are applied and reported.

**Acceptance Scenarios**:

1. **Given** a registry with three entries in scope and a provider query returning zero hosts, **When** the user runs sync and declines the prompt, **Then** all three entries remain and the command reports that no changes were made.
2. **Given** the same situation, **When** the user runs sync with `--yes`, **Then** the three entries are removed and each removed entry is named in the output.
3. **Given** a provider query that fails with an error, **When** the user runs sync, **Then** the registry is unchanged, the error is reported, and the command exits non-zero.
4. **Given** a plan that only adds and updates entries, **When** the user runs sync, **Then** no confirmation is requested and the changes are applied.
5. **Given** a plan containing removals and a session with no interactive terminal and no `--yes`, **When** the user runs sync, **Then** nothing is removed, the command explains that `--yes` is required for non-interactive removal, and it exits with the aborted code.
6. **Given** any plan, **When** the user runs sync with `--dry-run`, **Then** the full plan is printed, no confirmation is requested, the registry is unchanged, and the command exits zero.
7. **Given** a provider listing that cannot be enumerated completely, **When** the user runs sync, **Then** additions and updates are applied, no entry is removed, and the output warns that removals were skipped because the listing was incomplete.

---

### User Story 2 - AWS sync respects region boundaries (Priority: P1)

A user runs remo instances in more than one AWS region. Today, syncing one region deletes the registry entries for every region and re-adds only the region that was queried, silently destroying the record of instances elsewhere. Under this feature, an AWS sync only ever considers entries belonging to the region it queried; entries in other regions are outside the scope and are neither examined nor touched.

**Why this priority**: This is silent, unprompted destruction of correct data during a routine, non-destructive-sounding command. It is trivially reachable by anyone who passes `--region`.

**Independent Test**: Register instances in two regions, sync one region, and verify the other region's entries survive untouched and are not counted in the sync report.

**Acceptance Scenarios**:

1. **Given** registry entries for `us-west-2` and `eu-central-1`, **When** the user runs `remo aws sync --region eu-central-1`, **Then** the `us-west-2` entries are unchanged and are reported as neither added, removed, nor unchanged — they are out of scope.
2. **Given** the same registry, **When** an `eu-central-1` instance no longer exists at the provider, **Then** the removal prompt lists only that entry and never mentions `us-west-2` entries.
3. **Given** a registry entry whose region is recorded and an `aws sync` for a different region, **When** the sync completes, **Then** the out-of-scope entry retains its recorded region.

---

### User Story 3 - Stopped instances survive sync (Priority: P1)

Stopping an instance is a first-class, intentional remo operation — it is how a user parks an environment without paying for it. Today, `sync` filters to running instances only, so any sync while an instance is stopped deletes it from the registry. The user then cannot start it by name, and the recorded region is gone too, so recovery may require knowing which region to look in.

Under this feature, sync retains instances in any non-terminal state and reports their state so the user can see at a glance which environments are parked. The state is shown, never stored — it is read fresh from the provider on every sync, so it cannot go stale after an out-of-band start or stop.

**Why this priority**: The bug directly contradicts a supported workflow — `remo aws stop` followed by any `sync` destroys the thing that was stopped.

**Independent Test**: Register an instance, stop it, run sync, and verify the entry survives with its recorded region intact and is reported as stopped.

**Acceptance Scenarios**:

1. **Given** a remo-managed instance in a stopped state, **When** the user runs sync for its region, **Then** the entry is retained, reported as unchanged, and annotated with its stopped state.
2. **Given** a stopped instance whose public address is no longer published by the provider, **When** the user runs sync, **Then** the entry keeps its last recorded connection address rather than losing it or having it replaced with a placeholder.
3. **Given** an instance that has been terminated at the provider, **When** the user runs sync, **Then** it appears in the removal list and is removed only after confirmation.
4. **Given** a mix of running and stopped instances, **When** the user runs sync, **Then** the report distinguishes them.

---

### User Story 4 - One consistent sync report across providers (Priority: P2)

A user who works across incus, proxmox, AWS, and Hetzner currently gets four different sync experiences: different flags, different messages, different amounts of detail, and different counts (two providers report a total that includes instances they did not actually register). Under this feature, every provider's sync states its scope, then reports the same four outcomes — added, updated, unchanged, removed — with the affected names, and the counts always reflect what was actually written.

**Why this priority**: Consistency is what makes the safety guarantees legible. A user who has learned to trust `sync` on one provider should be able to trust it identically on the next. It is lower priority than the three correctness fixes because it improves comprehension rather than preventing loss.

**Independent Test**: Run sync against each of the four providers with a mixed plan and verify the output structure, wording, and counts follow one template.

**Acceptance Scenarios**:

1. **Given** any provider, **When** sync runs, **Then** the output first states the scope being reconciled.
2. **Given** a plan with additions, updates, unchanged entries, and removals, **When** sync completes, **Then** each category is reported with an accurate count, and non-empty categories name their entries.
3. **Given** a host whose connection address changed at the provider, **When** sync runs, **Then** it is reported as updated rather than as removed-and-added, and no confirmation is requested.
4. **Given** a plan that changes nothing, **When** sync completes, **Then** it says so explicitly rather than printing an empty report.

---

### User Story 5 - Adoption parity across providers (Priority: P3)

Incus and Proxmox let a user pull in containers that lack the remo managed marker via `--all`. Hetzner and AWS have no equivalent, so a user with instances created outside remo — or created before markers existed — has no path to bring them under management short of hand-editing. This feature gives Hetzner and AWS an explicit opt-in adoption flag with the same shape and the same warning, so the four providers behave alike.

It also closes the Hetzner marker gap at its source: Hetzner servers created by remo will carry the `remo` label that sync already looks for, and an existing unlabeled server can be backfilled through `remo hetzner update` the same way Incus and Proxmox already backfill their markers. Adoption becomes the escape hatch for genuinely foreign infrastructure rather than the only way to see your own fleet.

**Why this priority**: This closes a real asymmetry and is explicitly requested, but it adds capability rather than fixing loss. It is safely deferrable behind the three P1 fixes.

**Independent Test**: With an unmarked instance present at a Hetzner or AWS provider, run sync without the flag and verify it is skipped with a hint; run with the flag and verify it is adopted with a warning.

**Acceptance Scenarios**:

1. **Given** an unmarked instance at any of the four providers, **When** the user runs sync without the adoption flag, **Then** the instance is not registered, and the output names it and states how to adopt it for this run or mark it permanently.
2. **Given** the same instance, **When** the user runs sync with the adoption flag, **Then** it is registered and the output warns that it is not remo-created.
3. **Given** an unmarked instance that cannot be reliably distinguished from unrelated infrastructure at a provider, **When** the user runs sync with the adoption flag, **Then** the provider's adoption criteria are stated in the output so the user can see what was matched.
4. **Given** an instance adopted via the flag on a previous run, **When** the user runs a plain sync with no adoption flag, **Then** the entry is retained, reported as unchanged and unmarked, and no confirmation is requested.
5. **Given** a Hetzner server created by remo after this feature ships, **When** the user runs a plain `remo hetzner sync`, **Then** the server is discovered and registered without any adoption flag.
6. **Given** an unlabeled Hetzner server created before this feature, **When** the user runs `remo hetzner update` against it and then syncs, **Then** the label is applied, the server is discovered by the plain sync, and re-running `update` reports no change.

---

### Edge Cases

- **Hetzner's marker is never applied.** Hetzner sync selects servers by a `remo` label, but nothing in remo's provisioning path ever applies that label. Every Hetzner sync therefore matches zero servers and, under today's behavior, wipes the entire Hetzner registry. Two independent changes address this: entries are no longer dropped merely for lacking a marker (FR-022), and creation now applies the label so the fleet is discoverable at all (FR-031).
- **A Hetzner server created before this feature.** It carries no label and will not be added by a default sync until it is backfilled via `remo hetzner update`. It is not removed either, because it still exists at the provider. Sync must make the unmarked state visible so the user knows a backfill is available.
- **A host exists in scope at the provider and in the registry, but its recorded details differ** (address changed, access mode changed, region tag corrected). This is an update, not a remove-plus-add, and must not trigger a confirmation prompt.
- **A host is destroyed and rebuilt under the same name.** Its provider-side identifier changes, but its registry identity does not. This is an update — the new identifier is recorded, and no removal is proposed (FR-039).
- **The provider's listing is paginated and only the first page is retrieved.** Under clear-then-repopulate this silently lost entries; under reconcile it would silently propose deleting them. Enumeration must be exhaustive, and an incomplete listing must suppress removals rather than act on them (FR-040).
- **The registry entry was hand-edited** — a customized login user, for example. Sync must refresh only what it observes at the provider and leave everything else intact (FR-041).
- **Two providers claim the same registry name.** Registry identity is provider type plus name, so this cannot collide across providers — but two instances within one provider scope resolving to the same name can. Sync must detect this and refuse to write an ambiguous result rather than silently letting one win.
- **The registry file does not exist yet.** Sync against an empty or absent registry is a pure-addition plan and must apply without prompting.
- **A provider query partially succeeds** — for example, per-container address resolution fails for one container while the listing succeeded. The affected host must not be dropped from the desired set on that basis alone; it should retain its prior recorded address if one exists, or be reported as a problem rather than silently omitted (which would turn a transient failure into a proposed deletion).
- **Concurrent sync runs.** Two syncs for different scopes running at once must not interleave into a lost update; the plan a user confirmed must be the plan that gets written, or the write must fail loudly (FR-046). Syncs of *different* scopes must not obstruct each other — only a change within the same scope invalidates a plan.
- **A host still exists but has lost its managed marker** — its tag or label was removed by hand, or by another tool. It must be retained and reported as unmarked, never removed. This is only possible if the query enumerated it despite the missing marker (FR-044); a marker-filtered query would report it as absent and propose deleting it.
- **A container is moved to another Incus project, or to another Proxmox node.** It leaves the enumerated boundary and therefore looks absent. Sync will propose removing it, so the output must name the boundary it examined (FR-045) and the confirmation gate must be what stands between that and data loss.
- **A stopped instance is adopted or first discovered while stopped.** It has no published address; the entry must be creatable in a usable form or reported as needing a start before it can be reached.
- **Incus/Proxmox host is unreachable.** The listing fails, so there is no desired set — sync must abort without touching the registry rather than treating "unreachable" as "empty".

## Requirements *(mandatory)*

### Functional Requirements

#### Shared reconcile behavior

- **FR-001**: Sync MUST behave identically across incus, hetzner, aws, and proxmox with respect to scoping, planning, confirmation, atomicity, and reporting. Provider-specific behavior MUST be limited to how the desired host set is obtained.
- **FR-002**: Each provider MUST contribute only a *desired hosts* query: given a scope, it returns the complete set of registry entries that should exist within that scope. Providers MUST NOT clear, add, or otherwise mutate registry entries directly.
- **FR-003**: Scope MUST be explicit and MUST be one of: provider type (Hetzner), provider type plus region (AWS), or provider type plus host (Incus, Proxmox). The scope in effect MUST be stated in the sync output before any change is described.
- **FR-004**: Registry entries outside the stated scope MUST NOT be read as candidates for removal, modified, or reported in the sync summary.
- **FR-005**: Sync MUST classify every in-scope registry entry and every host the provider reports into exactly one of: **added** (eligible at the provider, not yet in the registry), **updated** (in the registry, still present at the provider, recorded details differ), **unchanged** (in the registry, still present at the provider, recorded details identical), **removed** (in the registry, confirmed absent from the provider).
- **FR-006**: The computed plan MUST be applied as a single atomic registry write. A partially applied plan MUST NOT be observable, and an interrupted or failed write MUST leave the registry in its pre-sync state.
- **FR-007**: Sync MUST report each of the four categories with a count that reflects what was actually written, and MUST name the entries in every non-empty category. A plan with no changes MUST say so explicitly.
- **FR-008**: Sync MUST NOT mutate provider-side state. It MUST NOT create, destroy, start, stop, tag, label, or reconfigure anything at the provider, including applying managed markers to instances that lack them. Applying markers remains the responsibility of the `create` and `update` commands (FR-031, FR-032).
- **FR-009**: If the desired-hosts query fails, sync MUST abort with a clear error, leave the registry unchanged, and exit with the failure code (FR-043). A failed query MUST NOT be treated as an empty result.
- **FR-039**: Correspondence between a registry entry and a provider-side host MUST be determined by the registry name within the scope. Provider-side identifiers such as an instance id or vmid are recorded details subject to update, never identity — a host destroyed and rebuilt under the same name MUST be classified as **updated**, not as a removal plus an addition.
- **FR-040**: Before classifying any entry as **removed**, sync MUST have enumerated the provider's hosts for the scope exhaustively, following pagination to exhaustion. If enumeration cannot be completed, sync MUST apply only additions and updates, suppress every removal, and warn that removals were skipped because the provider listing was incomplete.
- **FR-041**: On update, sync MUST refresh only the fields it observes from the provider — connection address, access mode, provider identifiers, and region. Any field the provider does not determine, or reports as unknown, MUST be preserved from the existing entry rather than overwritten or blanked.
- **FR-044**: The provider query MUST enumerate every host in scope, whether or not it carries the managed marker. The marker MUST NOT be used to narrow the query itself; it MUST be evaluated per host after enumeration. Only conditions that establish genuine non-existence — such as an instance being terminated — may narrow the query. A query narrowed by the marker cannot distinguish "this host was deleted" from "this host lost its marker", which would make FR-022 unenforceable.
- **FR-045**: Where a provider's enumeration covers less than the whole scope a user might reasonably assume — the default Incus project rather than all projects, or a single Proxmox node rather than a cluster — the sync output MUST name the boundary actually examined, so an entry proposed for removal can be recognised as one that merely moved outside it.
- **FR-046**: If the in-scope registry entries change between the moment the plan is computed and the moment it is written, sync MUST abandon the write, report that the registry changed underneath it, instruct the user to re-run, and exit with the failure code. It MUST NOT retry automatically and MUST NOT apply a plan the user did not see.

#### Destructive-change consent

- **FR-010**: Any plan containing one or more removals MUST list every entry it intends to remove, by name, before requesting confirmation.
- **FR-011**: Sync MUST require explicit confirmation before applying a plan containing removals. Additions, updates, and unchanged entries MUST NOT trigger a prompt.
- **FR-012**: A `--yes` flag MUST be available on every provider's sync command to bypass the confirmation prompt for scripted use. It MUST NOT suppress the report of what was removed.
- **FR-013**: Declining the confirmation MUST result in no registry change whatsoever — not the additions, not the updates, not the removals — and MUST be reported as an abort.
- **FR-014**: When removals are planned, no interactive terminal is available, and `--yes` was not supplied, sync MUST make no change, explain that `--yes` is required for non-interactive removal, and exit with the aborted code (FR-043).
- **FR-015**: A desired-hosts query returning zero hosts MUST flow through the same planning and confirmation path as any other result. The registry MUST NOT be cleared before the outcome is reported.
- **FR-042**: Every provider's sync MUST accept a `--dry-run` flag that computes and prints the complete plan, makes no registry change, requests no confirmation, and exits zero. If both `--dry-run` and `--yes` are supplied, `--dry-run` MUST take precedence and nothing is written.
- **FR-043**: Sync MUST use a consistent exit-code contract: **0** when the plan was applied or there was nothing to do (including a successful `--dry-run`); **1** when it failed (query error, write error, or a plan it refused to write); **3** when it aborted without changing anything because consent was declined or was unavailable non-interactively. Code **2** MUST NOT be used, as it is reserved for argument/usage errors by the CLI framework and by existing commands.

#### AWS correctness

- **FR-016**: `remo aws sync` MUST scope reconciliation to a single region. Only entries recorded as belonging to that region are in scope; entries for other regions MUST survive untouched.
- **FR-017**: `remo aws sync` MUST include instances in all non-terminal states — at minimum pending, running, stopping, and stopped — in the desired host set. It MUST exclude only instances that are terminated or shutting down.
- **FR-018**: A retained instance's recorded connection address MUST NOT be degraded when the provider reports no address for it (as happens while stopped). The last known address MUST be preserved. This is the AWS-specific case of the general field-ownership rule in FR-041.
- **FR-019**: Sync output MUST indicate the observed state of each instance where that state is not "running", so a user can see which registered environments are parked. This state MUST NOT be persisted in the registry — it is read from the provider and reported at sync time only, so it can never be stale.
- **FR-020**: An AWS registry entry MUST retain its recorded region across sync so that later commands targeting it by name resolve to the correct region.

#### Marker semantics and preserved behavior

- **FR-021**: The managed marker MUST govern **eligibility for addition** only — the `user.remo` config key on Incus, the `remo` tag on Proxmox, the `remo` label on Hetzner, and the `remo` tag on AWS determine which provider-side hosts sync will newly register.
- **FR-022**: An in-scope registry entry whose host still exists at the provider MUST be retained regardless of whether that host carries the managed marker. Removal MUST be driven solely by confirmed absence from the provider, never by absence of a marker.
- **FR-023**: If sync cannot determine whether an in-scope entry's host still exists at the provider, it MUST treat the entry as present and retain it.
- **FR-024**: Sync MUST identify, in its report, any retained entry whose host lacks the managed marker, and MUST state how to mark it permanently.
- **FR-025**: `--use-ip` MUST continue to work for Incus and Proxmox, storing each container's address instead of its name.
- **FR-026**: `--all` MUST continue to adopt unmarked containers on Incus and Proxmox, and MUST continue to warn that adopted entries are not remo-created. Because removal is driven by absence rather than by the marker (FR-022), adopted entries now persist across later default syncs; the existing warning that a later default sync will drop them MUST be removed, as it will no longer be true.
- **FR-027**: Incus and Proxmox sync MUST remain scoped to a single host, leaving other hosts' entries untouched.

#### Adoption parity

- **FR-028**: Hetzner and AWS sync MUST offer an explicit opt-in flag, with the same name and meaning as the Incus/Proxmox `--all`, that widens the set of addable hosts to include instances matching the provider's adoption criteria but lacking the managed marker. Because the query already enumerates unmarked hosts (FR-044), this flag changes only which hosts are eligible to be **added**; it never changes which are retained or removed.
- **FR-029**: When adoption is available but not requested, sync MUST name the instances it skipped and state both how to adopt them for this run and how to mark them permanently.
- **FR-030**: When adoption is requested, sync MUST state the criteria it used to match unmarked instances, so the user can verify what was pulled in.

#### Hetzner managed-label gap

- **FR-031**: Hetzner server creation MUST apply the `remo` managed label, so that servers remo creates are discoverable by a default sync without any adoption flag.
- **FR-032**: `remo hetzner update` MUST backfill the `remo` label onto an existing server that lacks it, mirroring the marker backfill Incus and Proxmox already perform.
- **FR-033**: Label application MUST be idempotent: creating or updating a server that already carries the label MUST report no change.
- **FR-034**: Labels MUST be applied without disturbing any other labels already present on the server.
- **FR-035**: After this feature, a default `remo hetzner sync` MUST discover every remo-created Hetzner server with no manual labeling step.

#### Consistency and documentation

- **FR-036**: Sync MUST be idempotent: running it twice in succession against an unchanged provider MUST produce no changes on the second run and MUST NOT prompt.
- **FR-037**: Sync MUST detect two provider-side hosts that resolve to the same registry name within one scope, and MUST refuse to write rather than silently letting one displace the other.
- **FR-038**: User-facing documentation MUST be updated to describe the new sync semantics — scoping, the confirmation gate, `--yes`, the report format, stopped-instance retention, marker-gates-addition-not-removal, adoption parity, and Hetzner labeling — before this feature is considered complete.

### Key Entities

- **Registry entry**: A record of one reachable environment. Identified within the registry by provider type plus name. Carries the connection address, login user, access mode, and per-provider details such as region or instance identifier.
- **Scope**: The bounded slice of the registry a single sync run is allowed to change — provider type, optionally narrowed by region (AWS) or host (Incus, Proxmox). Everything outside the scope is invisible to that run.
- **Desired host set**: A provider's answer, for one scope, at one moment. It carries two distinguishable facts per host: whether the host **exists** at the provider, and whether it is **eligible** to be newly registered. Eligibility is governed by the managed marker, optionally widened by the adoption flag; existence is independent of the marker, which is why the enumeration itself must never be narrowed by it (FR-044). The set also carries whether its enumeration was **complete**, since removals may only be derived from a complete enumeration.
- **Reconcile plan**: The computed difference between the desired host set and the in-scope registry entries, partitioned into added, updated, unchanged, and removed, keyed by registry name. Additions come from eligibility; removals come only from confirmed non-existence within a complete enumeration. A plan can be printed without being applied, and when applied is applied in full or not at all.
- **Managed marker**: The provider-side signal that an instance belongs to remo — a config key, tag, or label depending on provider. Sync reads it and never writes it; `create` and `update` write it.
- **Observed instance state**: A provider's report of whether a host is running, stopped, pending, or stopping. Reported in the sync summary and never stored in the registry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No sync operation, under any provider, query result, or failure mode, removes a registry entry without first naming it and obtaining consent — verified by a test suite covering empty results, query failures, partial results, and out-of-scope entries for all four providers.
- **SC-002**: Syncing one AWS region leaves 100% of other regions' registry entries intact.
- **SC-003**: An instance that is stopped and then synced remains usable by name — a user can start it and connect to it afterward without re-registering it or knowing its region.
- **SC-004**: A user who has run sync on one provider can predict the flags, prompts, and output structure of sync on the other three, because all four match a single documented contract.
- **SC-005**: The four providers' sync implementations share their reconcile logic, such that adding a fifth provider requires supplying only a desired-hosts query and no new registry-mutation, confirmation, or reporting code.
- **SC-006**: Every sync run performs at most one registry write, regardless of how many hosts are involved.
- **SC-007**: Running sync twice against an unchanged provider reports zero changes on the second run and prompts on neither.
- **SC-008**: Automated tests cover sync for all four providers against a real temporary registry — including the removal path, the confirmation path, and the `--yes` path — where today two providers have no sync tests at all and the destructive step is mocked out everywhere it is exercised.
- **SC-009**: A default `remo hetzner sync` discovers 100% of remo-created Hetzner servers with no manual labeling and no adoption flag, where today it discovers none.
- **SC-010**: An entry adopted via the adoption flag survives every subsequent default sync, without a prompt, for as long as its host exists at the provider.
- **SC-011**: No registry format change or migration is required by this feature; a registry written before it is readable after it, and vice versa.
- **SC-012**: A user can see exactly what sync would change, on any provider, without writing to the registry and without answering a prompt.
- **SC-013**: A script can distinguish "sync applied", "sync failed", and "sync declined to remove anything" from the exit code alone, without parsing output.
- **SC-014**: No provider listing that returns more results than fit in a single page can cause an entry to be proposed for removal.
- **SC-015**: A host that still exists but whose managed marker was removed is never proposed for removal, on any provider.
- **SC-016**: Two syncs of different scopes running concurrently both succeed; two syncs of the same scope never produce a write the user did not see.

## Assumptions

- Registry identity remains provider type plus name; this feature does not change how entries are keyed.
- "Atomic" means a reader never observes a partially written registry and a failed write leaves the prior contents intact — consistent with how the registry is already written today.
- The confirmation prompt follows the project's established convention for destructive commands, including the `--yes` / `-y` flag spelling already used by destroy, stop, and snapshot commands.
- Bare `remo aws sync` with no `--region` continues to resolve a single default region and scopes to it; syncing every region at once is out of scope for this feature.
- `--all` is retained as the adoption flag name on Incus and Proxmox and is reused on Hetzner and AWS, rather than introducing a second spelling.
- AWS adoption criteria can rely on the existing `remo-<name>` instance naming convention that remo already creates and already queries against.
- Sync remains a read-only operation with respect to the provider; users who want to permanently mark an adopted instance continue to use the provider's `update` command. Hetzner label application happens in `create` and `update`, never in `sync`.
- No registry format change is introduced. Instance state is reported, never stored, and adoption is inferred from provider presence rather than recorded — so no new fields and no migration.
- Existing registry contents are already in the current format.
- Because removal is driven by absence at the provider, an entry the user no longer wants but whose host still exists is removed through the provider's `destroy` command or by editing the registry, not by sync.
- The three-value exit-code contract (FR-043) is a deliberate departure from the codebase's current uniform exit-1 convention, and applies to sync only. Other commands are unaffected by this feature.
- "Complete enumeration" is a property the provider query reports, not something the reconcile logic infers. A provider that cannot express completeness is treated as never complete, and therefore never produces removals.
- Enumerating unmarked hosts (FR-044) costs one broader query rather than several narrow ones. For cloud providers this means listing the non-terminal instances in the scope and evaluating markers locally, which is acceptable for a CLI that already paginates.
- Incus enumeration covers the default project only, and Proxmox enumeration covers the addressed node only. Widening either — `--all-projects`, or cluster-wide discovery — is out of scope for this feature; the requirement is that the boundary be visible, not that it be removed.

## Clarifications

### Session 2026-07-25

- **Q: Hetzner's `remo` label is queried by sync but never applied by provisioning. Is closing that gap in scope?**
  **A: In scope — apply and backfill.** Server creation applies the label, and `remo hetzner update` backfills it onto existing servers, mirroring the Incus/Proxmox marker-backfill pattern. Recorded as FR-031 through FR-035. This extends the feature to the Hetzner provisioning path, not just the CLI/registry layer.

- **Q: Stopped instances must be "retained and marked" — where does the observed state live?**
  **A: Display-only at sync time.** State is read from the provider and shown in the sync report; nothing about state is written to the registry. No registry format change, no migration, and the value can never go stale after an out-of-band start or stop. Recorded as FR-019 and SC-011.

- **Q: Entries adopted via `--all` lack the managed marker, so a later default sync would now prompt to remove them on every run. How should that resolve?**
  **A: Markers gate addition, not removal.** An entry is proposed for removal only when its host is confirmed absent at the provider; an unmarked host that still exists is retained and reported as such. The marker decides what sync pulls in; presence decides what it drops. This needs no schema change, eliminates the repeat prompts, and makes `--all` adoptions durable as a side effect — so the existing "a later default sync will drop those unmarked one(s) again" warning becomes false and must be removed. Recorded as FR-021 through FR-024, FR-026, and SC-010.

- **Q: How is a registry entry matched to a provider-side host when deciding present / absent / updated?**
  **A: By registry name within the scope.** Provider-side identifiers (AWS instance id, Proxmox vmid) are recorded details subject to update, never identity. A second identity notion would create cases where the two disagree, and would make a host rebuilt under the same name look like a deletion. Recorded as FR-039.

- **Q: Provider listings are paginated. What should happen when enumeration is incomplete?**
  **A: Exhaustive enumeration required; incomplete listings suppress removals.** Sync paginates to exhaustion; if it cannot complete, it applies additions and updates, removes nothing, and warns. Under the old clear-then-repopulate a truncated page silently lost entries — under reconcile it would silently propose deleting them, so this generalizes FR-023's "undetermined → retain" to the whole listing. Recorded as FR-040 and SC-014.

- **Q: On an updated entry, which recorded fields may sync overwrite?**
  **A: Only provider-observed fields.** Connection address, access mode, provider identifiers, and region are refreshed; anything the provider does not determine or reports as unknown is preserved. FR-018 (do not clobber a stopped instance's address) is the AWS-specific case of this rule. Recorded as FR-041.

- **Q: Is there a way to preview a plan without committing to a confirmation prompt?**
  **A: Yes — `--dry-run` on every provider's sync.** Prints the plan, writes nothing, prompts for nothing, exits zero; takes precedence over `--yes` if both are given. The plan is already computed and rendered, so exposing it read-only is nearly free, and it closes a real hole: a pure-addition plan otherwise applies with no preview at all, and a non-interactive session cannot decline to see one. Recorded as FR-042 and SC-012.

- **Q: What exit-code contract should sync follow?**
  **A: 0 applied or no-op · 1 failed · 3 aborted without change.** Aborted covers both a declined confirmation and removals blocked non-interactively without `--yes`. The codebase currently exits 1 for everything, but this feature's purpose is scriptability, and a script must be able to distinguish a broken provider query from a deliberate refusal. Code 2 is skipped because the CLI framework and `remo shell` already use it for usage errors, so reusing it would make a mistyped flag indistinguishable from a deliberate refusal. Recorded as FR-043 and SC-013.

### Session 2026-07-25 (post-planning)

Three gaps surfaced while designing the implementation. All three were latent contradictions rather than open choices, so each is recorded with the reasoning that forced the answer.

- **Q: May the provider query filter on the managed marker?**
  **A: No.** The query must enumerate every host in scope and evaluate the marker afterwards; only conditions establishing genuine non-existence (a terminated instance) may narrow it. This was a contradiction, not a preference: FR-022 requires retaining an existing-but-unmarked host, but a marker-filtered query cannot see one, so it would report the host as absent and propose deleting it — the exact failure FR-022 exists to prevent. Incus and Proxmox already enumerate everything and evaluate the marker locally; this makes AWS and Hetzner match. It also simplifies FR-028: the adoption flag now changes only what may be *added*. Recorded as FR-044, FR-028, SC-015.

- **Q: What happens to a host that leaves the enumerated boundary — moved to another Incus project or Proxmox node?**
  **A: Keep today's boundary, but name it.** Incus enumeration stays default-project-only and Proxmox stays node-local; widening either is out of scope. Such a host looks absent and will be proposed for removal, so the scope line must state the boundary actually examined, letting a user recognise a "removal" that is really a relocation. The confirmation gate is what prevents the loss. Recorded as FR-045.

- **Q: How should a concurrent modification of the registry be handled between planning and writing?**
  **A: Fail loudly, never retry.** If the in-scope entries change after the plan is shown, the write is abandoned with an instruction to re-run, exiting with the failure code. Automatic retry would either apply a plan the user never saw or re-prompt confusingly. Only same-scope changes invalidate a plan, so concurrent syncs of different scopes do not obstruct each other. Recorded as FR-046 and SC-016.
