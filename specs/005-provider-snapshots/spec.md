# Feature Specification: Provider Snapshots

**Feature Branch**: `005-provider-snapshots`
**Created**: 2026-05-24
**Status**: Draft
**Input**: User description: "Add snapshot support across all four providers (incus, proxmox, aws, hetzner) with create/list/restore/delete commands; in-place rollback semantics where the provider allows; warn-on-destroy when snapshots exist."

## Clarifications

### Session 2026-05-24

- Q: Sync vs. async create on cloud providers (AWS, Hetzner) — does the create command block until the snapshot is usable? → A: Return immediately after the provider accepts the request; `list` shows in-progress snapshots with a status column.
- Q: Snapshot identity across destroy + re-create of an instance with the same `remo` name — do the old snapshots associate with the new instance? → A: No. Snapshots are scoped by provider-side identity (root volume ID on AWS, source server ID on Hetzner). After destroy, surviving snapshots become orphans invisible to remo; users manage them via the provider console.
- Q: What happens when `restore` or `delete` targets a snapshot whose creation is still pending? → A: Fail fast with a clear "snapshot X is still pending; check `list` for status" message, exit non-zero. No polling, no waiting. User retries after `list` shows the snapshot as available.
- Q: Should `list` show an estimated monthly cost for snapshots on AWS/Hetzner? → A: No. The cost column is dropped entirely. For accurate billing the user consults the provider's billing console. `remo` does not attempt cost estimation.
- Q: On AWS, what size should the restored EBS volume be when the current volume has been grown since the snapshot? → A: Match the current volume size (≥ snapshot's recorded size). The filesystem stays at the snapshot's recorded size until the user manually runs `resize2fs` to claim the extra capacity. Preserves disk capacity; avoids data loss.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Save state before a risky change (Priority: P1)

A developer is about to apply a system update, run an experimental script, or change configuration that they're not confident will work. They want to capture the current state of their remote instance so they can revert if it breaks.

**Why this priority**: This is the primary "insurance policy" use case that motivates snapshots. Without it, a broken upgrade means rebuilding from scratch and losing local state (uncommitted work, installed tools, custom config). It must work for all four providers because every developer's environment is hosted on one of them.

**Independent Test**: Pick any one provider, create a snapshot of an existing instance, mutate something inside the instance (touch a file, change a setting), restore the snapshot, then verify the mutation is gone. Delivers value as soon as a single provider works end-to-end.

**Acceptance Scenarios**:

1. **Given** a registered instance on any provider with no existing snapshots, **When** the user runs the snapshot create command without specifying a name, **Then** a snapshot is created with an auto-generated name based on the current timestamp, and a success message confirms creation.
2. **Given** a registered instance, **When** the user runs the snapshot create command with an explicit name and description, **Then** the snapshot is stored with that name and description and is retrievable via the list command.
3. **Given** an instance with a previously created snapshot, **When** the user has made changes since the snapshot, then runs the snapshot restore command and confirms the prompt, **Then** the instance is rolled back to the snapshot state, the instance's identifier and reachable address remain the same, and the user can reconnect to it as before.
4. **Given** an instance with a previously created snapshot, **When** the user runs restore but answers "no" to the confirm prompt, **Then** no changes are made to the instance.

---

### User Story 2 - See what snapshots exist (Priority: P2)

A developer wants to know which snapshots they currently have for an instance (or for all instances on a provider) and when each was taken. This drives both rollback decisions and cleanup.

**Why this priority**: Without visibility, snapshots silently accumulate. For local providers (Incus, Proxmox) this only consumes disk; for cloud providers (AWS, Hetzner) it costs real money monthly. Users need a quick way to see what exists so they can decide what to keep (and look up exact charges in the provider's billing console).

**Independent Test**: Create two snapshots of an instance, list them, confirm both appear with the expected columns.

**Acceptance Scenarios**:

1. **Given** an instance with two snapshots, **When** the user runs the snapshot list command targeted at that instance, **Then** a table is shown with one row per snapshot, including instance name, snapshot name, creation timestamp, size, and description.
2. **Given** multiple instances on the same provider each with snapshots, **When** the user runs the snapshot list command without specifying an instance, **Then** snapshots for all instances of that provider are shown grouped or sorted consistently.
3. **Given** an instance with no snapshots, **When** the user runs the list command, **Then** the user is shown a clear message indicating no snapshots exist (not a blank table).

---

### User Story 3 - Clean up an unwanted snapshot (Priority: P2)

A developer no longer needs a particular snapshot — they've verified the change worked, they're done debugging, or they want to free disk/cloud-storage costs.

**Why this priority**: Tightly paired with listing. Without delete, snapshots only grow. Especially important on AWS/Hetzner where the storage bill is recurring.

**Independent Test**: Create a snapshot, delete it, then list and confirm it no longer appears.

**Acceptance Scenarios**:

1. **Given** an instance with a snapshot, **When** the user runs the snapshot delete command and confirms the prompt, **Then** the snapshot is removed from the provider and no longer appears in subsequent list output.
2. **Given** an instance with a snapshot, **When** the user runs delete but declines the prompt, **Then** the snapshot remains.
3. **Given** the user passes the bypass-confirmation flag, **When** the delete command runs, **Then** no prompt appears and the snapshot is removed immediately.

---

### User Story 4 - Avoid orphaning paid snapshots when destroying an instance (Priority: P2)

A developer destroys an instance they no longer need. On cloud providers, snapshots they took of that instance persist after destruction and continue to incur monthly storage costs. They want to be reminded so they don't accidentally pay for forgotten data.

**Why this priority**: This is a money-leakage guard. Lower than P1 because it's preventive, not core functionality — but still high-value, especially on paid providers.

**Independent Test**: Create an instance, take a snapshot of it, run destroy, verify the destroy command surfaces the existence of the snapshot and offers cleanup.

**Acceptance Scenarios**:

1. **Given** an instance with one or more existing snapshots, **When** the user runs the destroy command, **Then** before destroying the instance the command lists the existing snapshots and asks whether to delete them too.
2. **Given** the user accepts the cleanup prompt, **When** destroy proceeds, **Then** all listed snapshots are deleted alongside the instance.
3. **Given** the user declines the cleanup prompt, **When** destroy proceeds, **Then** the instance is destroyed but the snapshots remain (and the user is informed they will continue to incur storage costs on paid providers).
4. **Given** an instance with no snapshots, **When** the user runs destroy, **Then** behavior is unchanged from today — no extra prompt is shown.

---

### Edge Cases

- **Provider doesn't support snapshots on the configured storage**: On Proxmox, only some storage backends (ZFS, LVM-thin, Btrfs) support snapshots; `dir` storage does not. When the user attempts to create a snapshot on an unsupported backend, the system must fail fast with a message naming the backend and suggesting alternatives, rather than producing an opaque provider error.
- **Snapshot name already exists**: When the user supplies a name that matches an existing snapshot for the same instance, the command must refuse to overwrite, return an error naming the conflict, and exit non-zero.
- **Asynchronous create on cloud providers**: For AWS and Hetzner, snapshot creation can take several minutes. The create command returns immediately after the provider accepts the request and prints a clear "in progress" hint; the user checks status later via `list`, which shows pending snapshots with their status. Restore and delete operations targeting a still-pending snapshot must fail fast with a clear message naming the pending status and exit non-zero — no polling, no waiting. The user re-runs after `list` shows the snapshot as available.
- **Restore of a snapshot that no longer matches the current volume size or configuration**: For AWS, if the EBS volume has been grown since the snapshot was taken, the restore creates the new volume at the current (larger) size — preserving capacity. The filesystem on the restored volume remains at the snapshot's recorded size until the user runs `resize2fs` manually; the restore command prints a hint pointing this out. Silent data loss is unacceptable; the snapshot's recorded size is never used to shrink the volume.
- **Restore while the instance is running**: For local providers (Incus, Proxmox), the instance may need to be stopped first. For cloud providers (AWS volume swap, Hetzner rebuild), the instance will definitely be stopped during restore. The confirm prompt must make this downtime explicit.
- **Listing snapshots when the provider/account is unreachable**: The list command must surface the underlying error rather than silently returning an empty list.
- **Restore failure mid-flight**: On AWS, the in-place volume swap is multi-step (stop → detach → create-volume → attach → start). If a step fails partway, the instance state could be inconsistent (no root volume attached). The command must either roll back to a known-good state or print explicit recovery instructions; silent corruption is unacceptable.
- **Snapshot name with characters not allowed by the provider**: Provider-side restrictions vary. The command must validate names client-side and reject obviously incompatible inputs with a clear message before attempting the provider call.
- **Orphaned snapshots after destroy**: If the user declines the destroy-time cleanup prompt, surviving snapshots become orphans — invisible to `remo` (they're scoped to a provider-side identity that no longer has a matching instance). The destroy command MUST surface this consequence in its warning so the user understands they can only manage the orphans via the provider console going forward.

## Requirements *(mandatory)*

### Functional Requirements

**Create**

- **FR-001**: Users MUST be able to create a snapshot of any registered instance on any of the four providers via a per-provider snapshot create command.
- **FR-002**: The snapshot create command MUST accept an optional explicit name; when omitted, the system MUST generate a name from the current timestamp in a consistent format.
- **FR-003**: The snapshot create command MUST accept an optional description that is persisted with the snapshot and shown by the list command.
- **FR-004**: When creating a snapshot on a provider where the operation is asynchronous (AWS, Hetzner), the command MUST return as soon as the provider accepts the request (without waiting for the snapshot to reach a completed state) and MUST inform the user that completion will take several minutes and that progress is visible via the `list` command.
- **FR-005**: When creating a snapshot on a Proxmox container whose storage backend does not support snapshots (e.g., `dir` storage), the command MUST detect this and exit with a clear error naming the storage backend and suggesting supported alternatives, without attempting the snapshot operation.
- **FR-006**: When the user supplies a snapshot name that already exists for the same instance, the command MUST refuse to create and exit with an error identifying the conflict.

**List**

- **FR-007**: Users MUST be able to list snapshots for a specified instance, or for all known instances of a given provider when no instance is specified.
- **FR-008**: The list output MUST include, at minimum: instance name, snapshot name, creation timestamp, snapshot size, and description. For providers where snapshot creation is asynchronous (AWS, Hetzner), the output MUST also include a status column whose value indicates whether the snapshot is `pending`, `available`, or `failed` (or the closest provider-side equivalent).
- **FR-009**: The list output MUST NOT attempt to estimate or display monthly storage cost. Cost information is the user's responsibility to look up in the provider's billing console; `remo` does not approximate or fetch billing data.
- **FR-010**: When no snapshots exist for the requested scope, the list command MUST display a clear message indicating this, not a blank table.
- **FR-011**: When the provider or account is unreachable, the list command MUST surface the underlying error rather than returning empty.

**Restore**

- **FR-012**: Users MUST be able to restore a named snapshot to its original instance via a per-provider snapshot restore command.
- **FR-013**: Restore MUST be in-place: the instance retains its registered name, its provider-side identifier, and its reachable network address. The user MUST be able to reconnect using the same `remo shell` invocation as before the restore.
- **FR-014**: Restore MUST prompt the user for confirmation by default; a bypass flag MUST be available to skip the prompt for scripted use.
- **FR-015**: When restore involves downtime (AWS volume swap, Hetzner server rebuild, or Proxmox/Incus stop-rollback-start), the confirm prompt MUST state this explicitly so the user understands the implication before agreeing.
- **FR-016**: If a restore operation fails partway through a multi-step sequence (specifically, the AWS volume-swap flow), the system MUST either restore the instance to its pre-restore state or print explicit recovery instructions naming the resources that need manual intervention.

**Delete**

- **FR-017**: Users MUST be able to delete a named snapshot of an instance.
- **FR-018**: Delete MUST prompt for confirmation by default; a bypass flag MUST be available.
- **FR-019**: Delete MUST remove the snapshot from the provider such that it no longer appears in subsequent list output and (for paid providers) no longer incurs storage cost.

**Destroy integration**

- **FR-020**: When a user runs the destroy command on an instance that has one or more existing snapshots, the system MUST list those snapshots and prompt the user whether to delete them as part of destroy, before any destructive action runs.
- **FR-021**: If the user accepts snapshot cleanup at destroy time, the system MUST delete all listed snapshots in addition to the instance.
- **FR-022**: If the user declines snapshot cleanup, the system MUST proceed with destroying only the instance and MUST warn the user that the orphaned snapshots will continue to incur storage cost on paid providers.
- **FR-023**: When an instance has no snapshots, the destroy command MUST behave exactly as it does today — no additional prompt.

**Cross-cutting**

- **FR-024**: All snapshot subcommands (create, list, restore, delete) MUST exit non-zero on any provider-side failure, surfacing the underlying error to the user.
- **FR-025**: Snapshot names supplied by the user MUST be validated client-side against any obvious provider restrictions before the provider call is attempted.
- **FR-026**: On providers where snapshot identity is **not intrinsically scoped to a remo-managed instance** (AWS and Hetzner, where snapshots live in a global account-wide namespace), snapshots created by `remo` MUST be identifiable as `remo`-managed via tags or labels so that the `list` command can distinguish them from snapshots created via other tools (provider console, manual CLI). On Incus and Proxmox, snapshot identity is intrinsic to the container — since the container itself is remo-managed, all of its snapshots are treated as remo's responsibility and no separate marker is required.
- **FR-027**: Snapshots MUST be scoped to the parent instance by the provider's stable identity of the underlying storage or source resource (root volume ID on AWS, source server ID on Hetzner; container identity is intrinsic on Incus/Proxmox), not by the user-facing instance name. When an instance is destroyed and a new one with the same `remo` name is later created, snapshots from the prior incarnation MUST NOT appear in `list`, MUST NOT be eligible for `restore` against the new instance, and MUST NOT be picked up by the destroy-time cleanup prompt for the new instance.
- **FR-028**: When `restore` or `delete` targets a snapshot whose current status is pending (or any non-available state), the command MUST exit non-zero with a clear error naming the snapshot, its current status, and a pointer to `list` for status visibility. The command MUST NOT poll or block waiting for the snapshot to become available.
- **FR-029**: On AWS, when `restore` is invoked against a snapshot whose recorded volume size is smaller than the current root volume, the restore MUST create the new volume at the current (larger) size, attach it as root, and emit a hint to the user that the filesystem occupies only the snapshot's recorded size and may be grown with `resize2fs` (or equivalent) inside the instance. The restore MUST NOT shrink the volume back to the snapshot's recorded size.
- **FR-030**: As a safety net on the AWS in-place volume swap, after a successful restore the system MUST retain the pre-restore root volume (no deletion) and tag it with `remo-restore-orphan=<ISO-8601-timestamp>`. The restore success message MUST identify this volume by ID and tell the user how to delete it manually (`aws ec2 delete-volume --volume-id <id>`) once they've confirmed the restored instance is healthy. This is intentionally not auto-cleaned: if the restored volume turns out corrupt, the orphan is the only recovery path.

### Out of Scope

- **Stateful snapshots on Proxmox** (preserving RAM state via `--vmstate`). Only non-stateful (filesystem-only) snapshots are supported.
- **Top-level cross-provider listing** (a `remo snapshot list` with no provider). Each provider has its own subcommand.
- **Auto-prune policies** such as "keep the last N snapshots" or "delete snapshots older than X days".
- **AWS "create new instance from snapshot"** restore mode. AWS restore is in-place volume swap only; spinning up a new instance from a snapshot is a separate workflow not covered here.
- **Snapshot of arbitrary volumes** other than the instance's root storage. Only the primary disk of each instance is captured.

### Key Entities

- **Snapshot**: A point-in-time capture of an instance's primary storage, identified by a user-facing name and a provider-side identifier. Attributes: parent instance, name, creation timestamp, size in bytes, optional description, provider-specific backend identifier, status (for providers with asynchronous creation).
- **Instance** (existing): The remote environment that a snapshot belongs to. Already modelled as a known host. The snapshot feature reads instance identity (provider, name, provider-side identifier such as VMID or volume ID) from existing registry data.

### Assumptions

- The instance's primary storage (the root volume on AWS/Hetzner; the container rootfs on Incus/Proxmox) is the appropriate scope for a snapshot. Users who need finer-grained capture (specific files, attached volumes) are out of scope and should fall back to provider-native tooling.
- Snapshot names supplied by users are short, ASCII, and free of provider-disallowed characters. The system validates and rejects obvious violations but does not attempt to sanitize or transform names.
- The destroy-time snapshot-cleanup prompt is shown for all four providers for consistency, even though orphan-cost is only meaningful for AWS and Hetzner. On local providers it still helps users keep their storage tidy.
- "Reconnect with the same `remo shell` invocation" assumes the user's local SSH known_hosts already trusts the host key; if the provider's restore changes the host key, the user may need to re-accept it once. This is consistent with existing remo behavior.
- Delete of a snapshot that doesn't exist exits non-zero with a "snapshot not found" error rather than silently succeeding. This is an intentional departure from strict idempotency (Constitution Principle III): we prefer a loud error so that a typo in the snapshot name doesn't masquerade as success and leave the user thinking they cleaned up something they didn't. Re-running `delete` on an already-deleted snapshot reaches the same end state, but reports it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After taking a snapshot and making destructive changes inside the instance, a user can restore from that snapshot and find the instance back in its pre-change state — verified by checking that test files created after the snapshot are gone after restore — on 100% of attempts across all four providers.
- **SC-002**: After restoring an instance, the user can reconnect via the same `remo shell <name>` invocation they used before the restore, without editing any local registry — on 100% of attempts.
- **SC-003**: On all four providers, `list` shows a snapshot within 5 seconds of its creation being acknowledged by the provider (with appropriate status — `pending` for AWS/Hetzner, `available` for Incus/Proxmox).
- **SC-004**: When attempting to create a snapshot on an unsupported Proxmox storage backend, the user receives an explanatory error within 5 seconds, before any provider mutation is attempted — on 100% of attempts.
- **SC-005**: When a user destroys an instance with existing snapshots, they are always (100% of the time) shown those snapshots and given the choice to clean them up before any destructive action runs.
- **SC-006**: Time from `remo <provider> snapshot create <instance>` invocation to a usable snapshot is under 30 seconds for local providers (Incus, Proxmox) on instances with a typical dev-tools rootfs; cloud providers (AWS, Hetzner) display the "in progress" hint within 5 seconds even if final completion takes longer.
- **SC-007**: A user new to the snapshot feature can create, list, restore, and delete a snapshot using only `--help` output and no external documentation, completing the round-trip in under 5 minutes.
