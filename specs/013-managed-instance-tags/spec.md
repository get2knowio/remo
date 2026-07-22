# Feature Specification: Managed-Instance Tagging & Filtered Sync (Incus / Proxmox)

**Feature Branch**: `013-managed-instance-tags`
**Created**: 2026-07-22
**Status**: Draft
**Input**: User description: "Tag Incus/Proxmox containers at provision time so `sync` only pulls in remo-managed instances, matching the cloud providers (AWS `tag:remo=true`, Hetzner `label_selector=remo`). Provide an escape hatch to adopt everything on a host."

## Problem & Motivation

`remo` has four providers whose `sync` command reconciles the local known-hosts
registry with the instances that actually exist. Today they split into two
mental models:

- **Cloud providers (AWS, Hetzner)** apply a provider-native marker at create
  time (`remo=true` tag on AWS, `remo` label on Hetzner) and `sync` filters on
  it. A user's unrelated EC2 instances or Hetzner servers are never touched.
- **Hypervisor providers (Incus, Proxmox)** apply no marker. `sync` runs
  `incus list` / `pct list` over SSH to the host and registers **every**
  container on the box — including containers the user created by hand, or that
  belong to unrelated workloads (a Home Assistant LXC, a Plex container, etc.).

This asymmetry means the same verb (`sync`) means "reconcile my remo instances"
on two providers and "import literally everything on this host" on the other
two. This feature closes that gap: `remo`-created Incus/Proxmox containers are
marked at provision time, and `sync` filters on that marker by default, with an
explicit `--all` opt-out for users who deliberately want to adopt every
container on a host.

## Clarifications

### Session 2026-07-22

- Q: When a default `sync` skips unmarked containers, what should the hint include? → A: Names + count + remedies (list the skipped container names alongside the count and both remedies).
- Q: Should this feature guard lifecycle commands (`destroy`/`snapshot`/resize) against `--all`-adopted, unmarked containers? → A: Out of scope — lifecycle commands operate uniformly on any registry entry; no marker check is added.
- Q: Should the managed marker key/value be fixed or configurable? → A: Fixed built-in constant (not user-configurable), matching AWS/Hetzner marker behavior.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sync only pulls in remo-managed containers (Priority: P1)

A developer runs `remo` on a Proxmox node (or Incus host) that also hosts
unrelated LXC containers — a media server, a home-automation box, a database
they run by hand. They run `remo proxmox sync <host>` and expect their registry
to contain only the dev containers `remo` created for them, not the entire
inventory of the node.

**Why this priority**: This is the core defect being fixed and the behavior that
brings Incus/Proxmox in line with AWS/Hetzner. Without it, `sync` pollutes the
registry (and the `remo shell` picker) with containers `remo` can neither manage
nor safely connect to, and gives the two hypervisor providers a different mental
model from the two cloud providers.

**Independent Test**: On a host with one remo-created container and one
hand-created container, run `sync` and confirm only the remo-created container
is registered.

**Acceptance Scenarios**:

1. **Given** an Incus/Proxmox host with a mix of remo-created and non-remo
   containers, **When** the user runs `sync` with no flags, **Then** only the
   remo-created (marker-bearing) containers are registered, and the summary line
   reports the count of registered containers.
2. **Given** a container that `remo` created (via `create`), **When** the user
   runs `sync`, **Then** that container is always registered because `create`
   applied the managed marker.
3. **Given** a host on which every container is remo-created, **When** the user
   runs `sync`, **Then** the result is identical to today's behavior (all of
   them registered).
4. **Given** a host on which no container carries the marker, **When** the user
   runs `sync` with no flags, **Then** zero containers are registered and the
   command prints a hint naming the skipped untagged containers and how to
   include or adopt them (see US2, US3).

---

### User Story 2 - Adopt every container on a host with `--all` (Priority: P2)

A developer is standing up `remo` against an existing Proxmox node whose dev
containers were created before this feature (or by another tool), and they
deliberately want to register all of them regardless of marker. They run
`sync --all` and get today's unfiltered behavior.

**Why this priority**: Preserves the existing capability for the "this whole box
is mine, import all of it" case and provides the migration path for containers
that predate the marker. Lower than P1 because it is an explicit opt-in, not the
default.

**Independent Test**: On a host with only unmarked containers, run `sync --all`
and confirm all of them are registered.

**Acceptance Scenarios**:

1. **Given** a host with a mix of marked and unmarked containers, **When** the
   user runs `sync --all`, **Then** every container on the host is registered,
   regardless of marker.
2. **Given** `--all` is used, **When** `sync` completes, **Then** the summary
   distinguishes how many of the registered containers were unmarked, so the
   user understands they adopted containers `remo` did not create.

---

### User Story 3 - Backfill the marker onto pre-existing remo containers (Priority: P2)

A developer upgrades `remo` to a version with this feature. Their existing
remo-created containers have no marker yet, so the new default `sync` would drop
them from the registry. They want a low-friction way to bring those containers
into the managed set so future filtered syncs see them.

**Why this priority**: Without a backfill path, upgrading is a regression: the
first `sync` after upgrade silently empties the registry of real remo
containers. This story makes the upgrade non-destructive.

**Independent Test**: Take a container that `remo` created before this feature
(no marker), run the backfill path, then run a default `sync` and confirm the
container is now registered.

**Acceptance Scenarios**:

1. **Given** an existing remo container with no marker, **When** the user runs
   `remo <provider> update <name>`, **Then** the managed marker is applied
   (idempotently) as part of update, and a subsequent default `sync` registers
   the container.
2. **Given** an existing remo container with no marker, **When** the user runs
   `sync --all`, **Then** the container is registered for that run even though it
   lacks the marker (adoption without mutating the container).
3. **Given** a default `sync` skips one or more unmarked containers, **When** the
   command finishes, **Then** it prints a hint naming the skipped containers and
   their count, and the two ways to include them (`--all` for a one-time
   adoption, or `remo <provider> update <name>` to mark one permanently).

---

### Edge Cases

- **Proxmox container already carries user tags**: Proxmox guest tags are a set.
  Applying the `remo` marker MUST preserve any existing tags on the container and
  MUST NOT remove or reorder the user's own tags. Re-applying when the marker is
  already present is a no-op.
- **Incus config key collision**: The Incus marker is a `user.*` config key,
  which lives in a namespace reserved for user metadata and cannot collide with
  Incus's own keys. If the key already exists with the expected value, applying
  it again is a no-op.
- **Marker present but container is stopped**: The marker is stored in container
  configuration, not runtime state, so a stopped container is still discovered by
  a filtered `sync` (subject to whatever address-resolution limits already apply
  to stopped containers today — unchanged by this feature).
- **User manually removes the marker**: If a user strips the marker off a
  remo-created container, a default `sync` will no longer see it. This is treated
  as an intentional "unmanage this container" action; `remo` does not fight the
  user by re-adding markers during `sync` (which is read-only — see FR-010).
- **Mixed-marker host with `--all`**: When `--all` adopts unmarked containers,
  those registry entries are indistinguishable from marked ones once written
  (the registry does not record marker state). Re-running a default `sync` later
  will drop the unmarked ones again. The `--all` summary MUST make this
  round-trip behavior clear enough that the user is not surprised.
- **`create` on a container name that already exists**: `create` is already
  idempotent (re-runs configure without re-creating). It MUST ensure the marker
  is present on that pre-existing container as part of the run, so a container
  first made by `remo` before this feature becomes marked the next time `create`
  touches it.
- **Localhost Incus**: The Incus provider supports `host == "localhost"`
  (running `incus list` directly). Marker application and filtering MUST behave
  identically for localhost and remote hosts.

## Requirements *(mandatory)*

### Functional Requirements

**Marking at provision time**

- **FR-001**: When `remo` creates an Incus or Proxmox container, it MUST apply a
  provider-native managed marker to that container as part of the create flow.
  On Proxmox the marker MUST be a guest tag; on Incus the marker MUST be a
  `user.*` configuration key.
- **FR-002**: Marker application MUST be idempotent: applying it to a container
  that already carries it MUST succeed as a no-op and MUST NOT alter any other
  container configuration.
- **FR-003**: On Proxmox, applying the marker MUST preserve all pre-existing
  guest tags on the container. The marker is added to the tag set; no existing
  tag may be removed or altered.
- **FR-004**: `remo <provider> update <name>` MUST ensure the managed marker is
  present on the target container (applying it if absent), so that `update`
  doubles as the backfill path for containers created before this feature.
- **FR-005**: Marker application failure during `create`/`update` MUST be
  surfaced to the user but MUST NOT, on its own, fail the overall command if the
  container was otherwise created/configured successfully; the command MUST warn
  that the container is unmarked and will require `--all` or a re-run of `update`
  to be picked up by a default `sync`.

**Filtered sync**

- **FR-006**: By default (no `--all` flag), `remo <provider> sync` on Incus and
  Proxmox MUST register only containers that carry the managed marker. Unmarked
  containers MUST NOT be registered.
- **FR-007**: `sync` MUST accept an `--all` flag that disables marker filtering
  and registers every container discovered on the host — the pre-feature
  behavior.
- **FR-008**: When a default (filtered) `sync` skips one or more unmarked
  containers, the command MUST print an informational hint stating how many were
  skipped, **naming the skipped containers**, and how to include them: `--all`
  for a one-time adoption, or `remo <provider> update <name>` to permanently mark
  one. Naming the skipped containers makes the `update <name>` remedy directly
  actionable without a separate lookup.
- **FR-009**: When `--all` is used and one or more registered containers were
  unmarked, the `sync` summary MUST distinguish the unmarked count so the user
  understands they adopted containers `remo` did not create.
- **FR-010**: `sync` MUST remain read-only with respect to remote container
  state: it MUST NOT apply, remove, or modify markers on any container. Marker
  mutation happens only through `create` and `update`.

**Cross-cutting**

- **FR-011**: This feature MUST NOT change how AWS and Hetzner `sync` behave;
  they already filter on their native markers. The goal is parity, achieved by
  bringing Incus/Proxmox up to the cloud model, not by altering the cloud
  providers.
- **FR-012**: This feature MUST NOT change the connection path (`remo shell`,
  `remo cp`) or the registry line format for a container. The marker lives on
  the provider side (container config), not in the local registry.
- **FR-013**: Marker presence MUST be detectable by `sync` in a single,
  bounded set of host queries (no per-container extra round-trip that scales the
  sync time linearly with unrelated containers where the provider offers a bulk
  query). Where a bulk marker-aware listing is available it SHOULD be preferred.

### Out of Scope

- **Changing AWS/Hetzner marking**: their `remo` tag/label mechanism is
  unchanged.
- **A generic "adopt this one existing container into the managed set" verb**
  that both registers *and* marks in one step. Backfill is via `update` (marks)
  or `--all` (registers without marking). A dedicated adopt verb may be
  considered separately.
- **Registering an SSH-reachable container without host access** — that is the
  subject of the companion feature `014-register-ssh-host` and is explicitly not
  addressed here (this feature still requires host/node access, because marking
  and discovery both run against the hypervisor).
- **Recording marker state in the local registry.** The registry continues to
  store only what is needed to connect; marker state is authoritative on the
  provider side.
- **Auto-migration** that silently re-marks or re-registers all pre-existing
  containers on first upgrade. Migration is user-initiated (`update` or
  `--all`), with a hint to guide it.
- **Guarding lifecycle commands against `--all`-adopted containers.** `destroy`,
  `snapshot`, and resize operate on any registry entry uniformly; this feature
  adds no marker-based refusal or warning to them.

### Key Entities

- **Managed marker**: A provider-native piece of container metadata indicating
  a container was created and is managed by `remo`. On Proxmox it is a guest tag;
  on Incus it is a `user.*` config key. It is a fixed built-in constant (not
  user-configurable) and the hypervisor analog of the AWS `remo=true` tag and the
  Hetzner `remo` label.
- **Container** (existing): An Incus/Proxmox instance, already modeled as a
  `KnownHost` in the registry (name in `host/container` form, VMID in
  `instance_id` for Proxmox). This feature adds a marker on the provider side but
  does not change the registry representation.

### Assumptions

- Proxmox guest tags (`pct set <vmid> --tags …`, visible in `pct config`) are
  available on the target Proxmox versions `remo` already supports. The Proxmox
  provider already requires SSH access to the node, so applying and reading tags
  needs no new capability.
- Incus `user.*` config keys (`incus config set <c> user.remo=true`, filterable
  via `incus list user.remo=true`) are available on the Incus versions `remo`
  already supports.
- The marker is a **fixed built-in constant, not user-configurable** (matching
  how the AWS `remo=true` tag and Hetzner `remo` label are hard-coded today). The
  exact literal key/value (e.g. tag `remo`, config key `user.remo=true`) is a
  design decision for the plan; the requirement is that it is stable, namespaced
  to avoid collisions, and consistent across the two providers' conceptual
  models. No env var or config option overrides it.
- Users who deliberately run `sync --all` on a shared host understand they are
  registering containers `remo` did not create. Lifecycle commands (`destroy`,
  `snapshot`, resize) operate **uniformly on any registry entry** and do NOT
  consult the marker: this feature adds no marker check to those commands. The
  expectation that lifecycle actions stay "scoped to what `remo` created" is a
  usage convention (the user chose to adopt those entries via `--all`), not an
  enforced guard — enforcing it would require recording marker state in the
  registry, which is explicitly out of scope.
- The upgrade path is acceptable as "first default `sync` after upgrade shows a
  hint and registers nothing until the user runs `update` or `--all`" — a loud,
  reversible no-op is preferred over silently emptying the registry or silently
  re-marking every container on the box.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a host containing exactly one remo-created container and any
  number of unrelated containers, a default `sync` registers exactly one entry —
  on 100% of attempts across both Incus and Proxmox.
- **SC-002**: A container created via `remo <provider> create` is registered by
  the very next default `sync` with no additional user action — on 100% of
  attempts.
- **SC-003**: Running `sync --all` on the same host registers the same set of
  containers that the pre-feature `sync` registered — i.e. no regression for the
  "adopt everything" workflow.
- **SC-004**: After running `remo <provider> update <name>` on a pre-existing
  unmarked container, that container appears in the next default `sync` — on
  100% of attempts, demonstrating the backfill path.
- **SC-005**: Applying the marker twice (e.g. `create` then `update`, or two
  `update`s) leaves the container configuration identical apart from the single
  marker, with all pre-existing Proxmox tags intact — verified by comparing
  container config before and after.
- **SC-006**: A user who upgrades and runs a default `sync` on a host of
  pre-existing unmarked containers is never left guessing: the command's hint
  names both the `--all` and `update` remedies — on 100% of such runs.
