# Feature Specification: Versioned Structured Host Registry (Registry v2)

**Feature Branch**: `015-registry-v2`

**Created**: 2026-07-25

**Status**: Draft

**Input**: User description: "Replace the colon-delimited flat-file host registry (~/.config/remo/known_hosts) with a versioned structured registry file (e.g. registry.json with {\"version\": 2, \"hosts\": [...]}) that eliminates per-type field overloading. Today the KnownHost fields are polymorphic by provider type: instance_id holds an Incus host SSH user, a Proxmox VMID, an EC2 instance id, or an SSH port depending on type, and region holds an AWS region, a Proxmox node user, or an identity-file path. The v2 format must give each provider type explicitly named, typed fields so no field is ever overloaded, and must make the \"SSM access\" rule an explicit stored attribute instead of the implicit \"instance_id set + access_mode empty\" convention currently re-derived in models/host.py, core/web_adopt.py, and web/api/setup.py. Requirements: (1) transparent one-time migration from the legacy colon format on first read, preserving all existing entries including ssh-type \"added\" hosts; (2) a single registry accessor module used by ALL consumers — CLI, providers, and the web service — with an explicit readonly mode that never mkdirs, never raises SystemExit, and parses tolerantly, so the three independent parser implementations (core/known_hosts.py, web/discovery.py, web/api/setup.py) collapse into one; (3) input validation on write (reject or escape values that would corrupt the format — the legacy format silently corrupts on any value containing a colon, e.g. IPv6 literals); (4) advisory file locking around read-modify-write sequences so concurrent writers (two syncs, or CLI sync racing an adopted web service's registry PUT) cannot drop each other's entries; (5) the adopt/push mirror payload and the web service's registry PUT must speak the v2 format, with a compatibility story for a workstation and web service on different versions. The KnownHost dataclass survives as the in-memory model; only serialization and parsing move. The file must remain human-readable and diffable (no database)."

## Clarifications

### Session 2026-07-25

- Q: Which component performs the legacy→v2 migration? → A: Only the CLI migrates; the web service reads both formats in place tolerantly and never migrates, regardless of write access.
- Q: What happens to the legacy file after migration? → A: It is renamed in place to a backup name in the same directory (exact name is a design-phase decision), so "both files present" signals an anomaly rather than the permanent normal state.
- Q: How long is the bounded wait for the registry lock? → A: 5 seconds by default, then fail with a clear "registry busy" message.
- Q: What happens to the web-push delta cache after migration? → A: It is reset; the first push after upgrade treats all instances as changed and re-verifies/re-authorizes them (idempotent, one-time, mentioned in the migration notice).
- Q: What registry scale must remain performant? → A: Up to 200 entries with no perceptible added latency (under 100 ms registry overhead per command).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Existing User Upgrades Seamlessly (Priority: P1)

An operator with an existing registry containing a mix of environments — Incus containers, Proxmox containers, AWS instances (SSM-accessed), Hetzner servers, and manually added SSH hosts — upgrades remo. The first time they run any remo command, the registry is migrated to the new versioned format automatically. Every environment they had registered is still present, every command (shell, cp, list, sync, snapshot, web push) behaves exactly as before, and they never see a migration prompt or perform a manual step.

**Why this priority**: Migration safety is the make-or-break requirement. If a single existing entry is lost or altered during upgrade, users lose access to running infrastructure they depend on. Nothing else in this feature matters if upgrade is not lossless and invisible.

**Independent Test**: Populate a legacy-format registry with at least one entry of every environment type (including all optional-field combinations: with/without instance id, access mode, region, and ssh-type hosts with ports and identity paths). Upgrade, run a read command, and verify the new-format file contains an equivalent entry for every original line and that connecting to each host still works.

**Acceptance Scenarios**:

1. **Given** a legacy registry with entries of all environment types, **When** the user runs any remo command that reads the registry, **Then** the registry is converted to the versioned format with every entry preserved, and the command's output is identical to what it would have shown before migration.
2. **Given** a legacy registry containing an ssh-type "added" host with a custom port and identity file, **When** migration runs, **Then** the migrated entry stores the port and identity file under explicitly named attributes and connections to that host still use them.
3. **Given** a legacy registry containing unparseable garbage lines alongside valid lines, **When** migration runs, **Then** valid entries are migrated, the original file is preserved as a backup, and the user is informed that unrecognized lines were set aside rather than silently discarded.
4. **Given** a system that has already migrated, **When** any subsequent command runs, **Then** no migration occurs again and no legacy file is re-created.

---

### User Story 2 - Registry Values Can No Longer Corrupt the File (Priority: P2)

An operator registers a host whose address is an IPv6 literal, or adds an SSH host whose identity file path contains unusual characters. The registry stores and returns these values faithfully. Attempts to store a value that cannot be represented are rejected at write time with a clear message — never silently written in a way that corrupts the file or truncates the value.

**Why this priority**: The legacy format silently corrupts on any value containing a colon. This is a live data-loss bug class; the new format's core promise is that any legitimate value round-trips exactly.

**Independent Test**: Register hosts with IPv6 addresses, paths containing spaces and special characters, and boundary-length names; confirm each value reads back byte-identical and that a deliberately invalid value (e.g., containing a newline or empty required field) is rejected with an actionable error before anything is written.

**Acceptance Scenarios**:

1. **Given** a host whose address is an IPv6 literal, **When** it is saved and read back, **Then** the address is intact and connections use it correctly.
2. **Given** an attempt to save an entry with an invalid or unrepresentable value, **When** the write is attempted, **Then** it is rejected with a message naming the field and the problem, and the registry file is unchanged.
3. **Given** any environment type, **When** its entry is inspected, **Then** every attribute lives under a name that states what it is (e.g., a VMID is stored as a VMID, an SSH port as a port), and whether the host is reached via direct SSH or a brokered channel (SSM) is an explicit stored attribute, not inferred from which other fields happen to be empty.

---

### User Story 3 - One Registry Reader for CLI and Web Service (Priority: P3)

The web service (running in a container, possibly with the registry mounted read-only) and the CLI both read the registry through the same accessor. The web service's read path never attempts to create directories or files, never terminates the process on a bad entry, and tolerates individually malformed entries by skipping them while surfacing the rest — identical tolerant behavior to the CLI's read path, because it is the same code.

**Why this priority**: Today three independent parser implementations exist and can drift apart. Collapsing them removes a whole category of "CLI sees the host but the web service doesn't" bugs, and is a prerequisite for the format change to be safe (a format change with three parsers means three migration bugs).

**Independent Test**: Point the web service at a registry file on a read-only volume in both legacy and new formats; verify discovery lists the same hosts the CLI lists, no write is ever attempted against the read-only volume, and a corrupted single entry degrades to "that entry skipped" rather than a service failure.

**Acceptance Scenarios**:

1. **Given** a registry on a read-only volume in the legacy format, **When** the web service reads it, **Then** all entries are visible to discovery without any migration write being attempted, and the file is left untouched.
2. **Given** a registry with one malformed entry among valid ones, **When** either the CLI or the web service reads it, **Then** both surface the same set of valid entries and neither crashes or exits.
3. **Given** the codebase after this feature, **When** the registry parsing logic is located, **Then** it exists in exactly one module, and the previously independent read implementations delegate to it.

---

### User Story 4 - Concurrent Writers Don't Lose Entries (Priority: P4)

An operator runs `remo aws sync` while a `remo incus sync` is still in flight in another terminal — or the adopted web service applies a pushed registry update at the same moment the CLI modifies the registry. When all operations complete, the registry contains the union of what each writer intended: no writer's entries have been silently dropped by another writer's read-modify-write cycle.

**Why this priority**: Lost-update races exist today but are low-frequency for a single operator. They become more likely as the web service also writes the registry (adoption pushes) and as sync operations grow. This must be fixed before higher-level features (reconcile-based sync, drift tracking) build on the registry.

**Independent Test**: Launch multiple concurrent registry mutations targeting different environment types in a loop; verify the end state always contains every expected entry and the file is never left in a partially written or unparseable state.

**Acceptance Scenarios**:

1. **Given** two concurrent operations each adding/updating entries for different environment types, **When** both complete, **Then** the registry contains both writers' results.
2. **Given** a writer that holds the registry lock, **When** a second writer arrives, **Then** the second writer waits briefly and proceeds, or fails with a clear "registry is busy" message after a bounded wait — it never proceeds on stale data.
3. **Given** a process killed mid-write, **When** the next reader opens the registry, **Then** it sees the complete previous state (never a torn/partial file).

---

### User Story 5 - Workstation and Web Service on Different Versions (Priority: P5)

An operator upgrades their workstation CLI before upgrading their remo-web container (or vice versa), then runs a registry push. The push either succeeds correctly or fails fast with a message that names the version mismatch and tells the operator which side to upgrade. At no point does a mismatch silently corrupt the service's mirrored registry or strand the service in a broken state.

**Why this priority**: Version skew between workstation and service is guaranteed to happen in the field (they are upgraded independently), but it is an upgrade-window scenario rather than a daily-use path.

**Independent Test**: Run a push from a new-format workstation against an old-format service and the reverse; verify each combination either interoperates or produces a single clear remediation message, and the service remains healthy afterwards.

**Acceptance Scenarios**:

1. **Given** an upgraded workstation and an older web service, **When** the workstation pushes the registry, **Then** the outcome is either a successful, correct mirror or an explicit version-mismatch error naming the remediation ("upgrade the web service") — never a partial or corrupted mirror.
2. **Given** an upgraded web service and an older workstation, **When** the older workstation pushes a legacy-format payload, **Then** the service accepts it and stores it correctly in the new format.
3. **Given** a version-mismatch rejection, **When** the operator checks the service, **Then** the service still serves its previous registry state and reports healthy.

---

### Edge Cases

- Both a legacy file and a new-format file exist side by side (e.g., interrupted migration, or a rollback then re-upgrade): the system must define a single winner deterministically, tell the user which was used, and never merge them silently.
- The new-format file declares a version newer than the running remo understands (user downgraded the CLI): reads must fail with a clear "this registry was written by a newer remo" message rather than misparsing; the file must not be modified.
- The registry directory is on a read-only volume and still contains only a legacy file: readonly consumers must parse it in place indefinitely — migration must not be a precondition for reading.
- An entry has an environment type the running version doesn't recognize (written by a newer version or a future provider): readers must preserve it on rewrite and skip it in listings rather than dropping or crashing.
- Empty registry file vs. missing registry file: both must be handled as "no hosts registered" without error, and neither state may cause a spurious migration or backup.
- The migration backup already exists from a previous migration attempt: a re-migration must not overwrite the older backup silently.
- Lock acquisition on filesystems that do not support advisory locking (some network mounts): the system must degrade to current (unlocked) behavior with a one-time warning rather than refusing to operate.
- A legacy entry whose overloaded fields are ambiguous or contradictory (e.g., an entry that doesn't match any known per-type shape): migration must preserve it in the backup and report it, never guess destructively.

## Requirements *(mandatory)*

### Functional Requirements

**Format**

- **FR-001**: The registry MUST be stored as a single human-readable, diffable text file carrying an explicit format version, replacing the colon-delimited flat file as the canonical store.
- **FR-002**: Each registry entry MUST store every attribute under an explicitly named field whose meaning does not depend on the entry's environment type; no field may be overloaded to mean different things for different types.
- **FR-003**: Each environment type (Incus, Proxmox, AWS, Hetzner, manually added SSH) MUST have its own defined set of named attributes covering everything the legacy format encoded for that type (including: Incus host SSH user; Proxmox VMID and node user; AWS instance id and region; SSH port and identity-file path for added hosts).
- **FR-004**: The access method (direct SSH vs. brokered SSM channel) MUST be an explicit stored attribute of each entry, replacing the implicit "instance id present + access mode empty" convention everywhere it is currently re-derived.
- **FR-005**: The stored format MUST support any legitimate field value, including values containing colons (IPv6 literals), spaces, and other characters that corrupt the legacy format; values MUST round-trip byte-identically.
- **FR-006**: The file MUST remain reviewable and diffable in version-control and backup tooling; a database or binary store is out of scope.

**Migration**

- **FR-007**: On first read by the CLI, a legacy-format registry MUST be migrated to the new format automatically, with zero user action and zero behavior change to the invoking command beyond an informational notice (which also mentions the one-time web-push re-verification, see FR-026). The CLI is the only component that performs migration.
- **FR-008**: Migration MUST preserve 100% of parseable legacy entries across all environment types and optional-field combinations, mapping each overloaded legacy field to its explicit named attribute.
- **FR-009**: Migration MUST preserve the original legacy file by renaming it in place to a backup name in the same directory, and MUST report (not silently discard) any lines it could not interpret; a pre-existing backup from an earlier attempt MUST NOT be silently overwritten.
- **FR-010**: Migration MUST be idempotent and crash-safe: an interrupted migration leaves the legacy file authoritative and intact, and the next read completes the migration; a completed migration is never re-run.
- **FR-011**: The web service and all other non-CLI consumers MUST be able to read both the legacy and new formats in place without writing anything and MUST never migrate, regardless of whether their volume is writable; migration MUST NOT be a precondition for reading.

**Single accessor**

- **FR-012**: Exactly one module MUST own parsing, serialization, validation, and locking for the registry; the CLI, provider logic, and the web service MUST all consume the registry through it, eliminating the three existing independent parser implementations.
- **FR-013**: The accessor MUST offer an explicit read-only mode that never creates directories or files, never terminates the calling process, and reports problems as returned errors/exceptions the caller chooses how to handle.
- **FR-014**: Reads MUST be tolerant at entry granularity: an individually malformed entry is skipped and surfaced as a warning/diagnostic, while remaining entries are returned; rewrites MUST preserve entries of unrecognized environment types rather than dropping them.
- **FR-015**: The in-memory host model (the KnownHost dataclass and its consumers) MUST retain its role; only serialization, parsing, and storage semantics change. Existing call sites that read or mutate hosts continue to work against the same in-memory shape or type-safe equivalents.

**Validation on write**

- **FR-016**: Every write MUST validate entries before anything touches disk: required fields present and non-empty, values representable in the format, and per-type attributes consistent with the entry's type; a failed validation rejects the write with a message naming the field and problem, leaving the file unchanged.

**Concurrency**

- **FR-017**: All read-modify-write sequences MUST be serialized via advisory locking so concurrent writers (two syncs; CLI mutation racing a web-service registry update) cannot drop each other's entries; a writer either acquires the lock within a bounded wait (5 seconds by default) or fails with a clear "registry busy" message — it never writes based on stale reads.
- **FR-018**: Every write MUST remain atomic from a reader's perspective: readers see either the complete old state or the complete new state, never a torn file — including when a writer is killed mid-operation.
- **FR-019**: On filesystems where advisory locking is unavailable, the system MUST degrade gracefully to unlocked operation with a one-time warning rather than refusing to work.

**Workstation ↔ web service compatibility**

- **FR-020**: The adopt/push mirror payload and the web service's registry-update endpoint MUST carry the registry format version and exchange entries in the explicit per-type representation (no overloaded fields on the wire).
- **FR-021**: A version mismatch between workstation and service MUST fail fast, before any mutation, with a message identifying which side is older and what to upgrade; the service's existing mirror MUST remain intact and served after a rejected push.
- **FR-022**: An upgraded web service MUST accept pushes from a not-yet-upgraded workstation (previous payload version) and store them correctly in the new format, so the service can be upgraded first without breaking the operator's push workflow.
- **FR-023**: When the registry file declares a format version newer than the running software understands, reads MUST fail with an explicit "written by a newer version" error and the file MUST NOT be modified.
- **FR-026**: Migration MUST reset the web-push change-detection cache: the first push after upgrade treats every instance as changed and re-verifies/re-authorizes it. This re-verification MUST be idempotent and require no manual cleanup, and the migration notice MUST mention that the next push will re-verify all instances.

**Precedence & observability**

- **FR-024**: When both a legacy file and a new-format file are present, the system MUST deterministically select one (new format wins), inform the user which was used and why, and never merge the two silently.
- **FR-025**: Migration, backup creation, skipped-entry warnings, and lock-wait events MUST be reported to the user in plain language; none of these may pass silently.

### Key Entities

- **Registry**: The single versioned document listing every environment remo manages from this workstation (or mirrored to a web service). Carries an explicit format version and a collection of host entries. Human-readable and diffable.
- **Host Entry**: One managed environment. Common attributes: environment type, display name, address, login user, access method (direct / brokered). Type-specific attributes live in a named per-type group (e.g., VMID and node user for Proxmox; instance id and region for AWS; port and identity file for added SSH hosts). No attribute's meaning varies by type.
- **Legacy Registry**: The colon-delimited flat file being replaced. Exists only as a migration source and preserved backup after upgrade.
- **Migration Backup**: The legacy file, renamed in place to a backup name in the same directory at migration time, content untouched — including any lines migration could not interpret.
- **Mirror Payload**: The versioned representation of the registry (plus per-host trust material) that the workstation pushes to an adopted web service; speaks the same explicit per-type representation as the file format.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of parseable legacy registry entries — across all five environment types and all optional-field combinations — survive migration with equivalent connection behavior, verified by an automated migration test matrix.
- **SC-002**: An existing user upgrading remo performs zero manual migration steps: the first command they run after upgrade works, and every subsequent command's output matches pre-upgrade behavior for the same registry.
- **SC-003**: Registry parsing/serialization logic exists in exactly one place: a codebase search finds no independent registry parser outside the single accessor module (down from three today).
- **SC-004**: Hosts with previously-corrupting values (IPv6 addresses, colon-containing paths) can be registered, listed, connected to, and pushed to a web service with values intact end to end.
- **SC-005**: A concurrency stress test (repeated concurrent mutations from multiple writers) completes with zero lost entries and zero unparseable file states across all iterations.
- **SC-006**: Every workstation/service version-skew combination (new→old, old→new) in the push flow ends in either a correct mirror or a single actionable error message; zero combinations end in silent divergence or a broken service.
- **SC-007**: The registry file remains reviewable: a person can read a diff of a registry change and state which host and which attribute changed, without tooling.
- **SC-008**: Registries of up to 200 entries add no perceptible latency: registry read/parse/write overhead stays under 100 ms per command invocation.

## Assumptions

- The registry remains a workstation-owned local file; multi-workstation shared registries, remote registries, and databases are out of scope. Advisory locking targets same-machine concurrency only.
- The new-format file lives in the same configuration directory as the legacy file under a new file name; the legacy file is renamed in place to a backup name at migration time. Exact file and backup naming are design-phase decisions.
- Migration happens lazily on the CLI's first registry read, not via a separate migration command; the web service — whether on a read-only mount or an adopted writable volume — reads legacy files in place indefinitely and never migrates.
- Downgrade support is not provided: after migration, an older remo version will not find the legacy file at its expected path. The renamed backup allows manual restoration, and this is documented; automated back-migration is out of scope.
- The web service's registry-update endpoint already carries a payload version field; this feature increments it. "Upgrade the service first, workstation second" is the supported skew direction (the service accepts old payloads; the workstation does not down-convert for old services beyond failing with clear remediation).
- The scope of "consumers" is the existing codebase's three read paths and all current write paths; changing sync semantics, adopt/push workflow behavior, or provider dispatch is explicitly out of scope (covered by separate roadmap features).
- Entry-level tolerant parsing (skip bad entries, keep good ones) matches today's read behavior and remains the desired policy; strict all-or-nothing validation applies only to writes.
