# Feature Specification: Discovery Resilience — Tolerate Intermittent Instance Connectivity

**Feature Branch**: `024-discovery-resilience`

**Created**: 2026-09-19

**Status**: Draft

**Input**: User description: "Make the web console tolerant of intermittent instance connectivity. When a remo web instance is under heavy load, a discovery cycle times out and the console clears the workspace, tears down live terminals, and shows the host as a red error block with zero sessions — while a page refresh restores everything, proving nothing was actually wrong with the instance."

## Context

The web console's discovery service polls every registered instance on a fixed
interval (capabilities probe + session listing over SSH). Today one failed
cycle is treated as authoritative: the cached snapshot for the instance is
overwritten unconditionally, so an `ok` snapshot holding N session targets is
replaced by a `timeout` snapshot holding zero targets. Everything downstream
amplifies that single blink:

- The service's target index only includes targets from instances whose
  status is `ok`, so every session target for the host vanishes from the
  sessions listing — and with it, the authorization basis for opening or
  reattaching terminals on that host.
- The console's workspace resolves open panes against the live target list
  and silently drops unresolved panes, so open terminal cards unmount and
  their live WebSocket connections are torn down. With none left, the
  workspace falls to the "Select a session" empty state.
- The navigation rail flips the host's group into its error presentation:
  rows disappear and a prominent red error block replaces them.

A page refresh immediately restores everything, because the instance was
reachable all along — discovery, not the instance, had the bad moment. The
frontend polling layer already demonstrates the right instinct one level up:
when a *poll of the discovery API* fails, it deliberately keeps the last-known
instances. The gap is that a *successful poll reporting a failed discovery* is
indistinguishable from "this host genuinely has no sessions."

A contributing defect makes the blink far more likely under load: the
per-instance discovery routine gives each of its two sequential remote calls
the full per-call timeout, while an outer guard bounds *both together* at that
same single value — so a slow first call starves the second, and the instance
is declared timed out well before either individual call would have failed.

This feature makes a transient discovery failure a *degraded* state rather
than a *destroyed* one: the service retains last-known-good data through a
bounded grace window, the console presents staleness as an advisory badge
rather than an error, and open terminals stop depending on discovery liveness
at all — the terminal's own connection state is the truth about whether it is
alive.

Deterministic failures are explicitly out of the retention path: a host that
fails authentication, lacks the remo-host tooling, speaks an incompatible
protocol, or returns malformed data is genuinely misconfigured, and hiding
that behind stale data would delay the fix. Only transient, retryable
failures (timeout, unreachable) are graced.

## Clarifications

### Session 2026-09-19

- Q: How is the grace budget measured — elapsed time or failure count? → A:
  Elapsed time: a retryable failure hardens into the real error state only
  when the time since the last successful discovery exceeds the configured
  budget. The consecutive-failure count is advisory (served for display),
  never the trigger — a count-based reading would silently rescale with the
  poll interval.
- Q: What do the advisory fields show on the post-grace hard-error snapshot?
  → A: `stale` is false — staleness marks only the retained-ok state. The
  last-success timestamp, consecutive-failure count, and error detail stay
  populated on the error snapshot for observability.
- Q: Are the console's sticky pane-target records persisted with the
  workspace layout? → A: No — in-memory only. A page reload runs fresh
  discovery and resolves panes through the existing path; persisting target
  snapshots would resurrect dead state across sessions.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Open terminals survive a discovery blink (Priority: P1)

A user has several terminals open to a busy instance (many devcontainers
building at once). A discovery cycle times out against that instance. The
user's terminals stay mounted, connected, and interactive — nothing flickers,
disconnects, or unmounts. The rail shows the host slightly dimmed with a
"not responding · retrying" indication; the next successful cycle clears it.

**Why this priority**: Terminal teardown is the data-loss-adjacent failure —
a torn WebSocket ends the user's interactive attachment mid-keystroke. It is
the single behavior users described as the bug.

**Independent Test**: With terminals open against a fake instance, force one
discovery cycle to time out and assert the sessions listing still contains
the host's targets, terminal open/reattach is still authorized, and the
console keeps the panes mounted.

**Acceptance Scenarios**:

1. **Given** an instance with a prior successful discovery snapshot holding
   N session targets, **When** the next discovery cycle for it fails with a
   retryable status (timeout or unreachable), **Then** the served instance
   state keeps status `ok`, retains all N targets and its capability record,
   and is marked stale with the failure attached as advisory detail.
2. **Given** that stale-but-ok state, **When** a client opens or reattaches
   a terminal to one of the retained targets, **Then** the operation is
   authorized and proceeds exactly as it would against a fresh snapshot.
3. **Given** open terminal panes on the affected host, **When** the console
   receives the stale snapshot, **Then** no pane unmounts and no terminal
   WebSocket is closed by the console.
4. **Given** a stale instance, **When** a later discovery cycle succeeds,
   **Then** the stale marking clears, the failure counter resets, and fresh
   targets replace the retained ones.

---

### User Story 2 - Staleness reads as a badge, not an outage (Priority: P2)

The same user glances at the navigation rail during the blip. The host's
group does not turn into a red error block with vanished rows; it stays in
place, dimmed, with a compact "not responding · retrying" chip. The host is
never dropped from the rail.

**Why this priority**: The red error block is the visible half of the false
alarm — it tells the user their host is down when it is not, prompting
disruptive manual refreshes.

**Independent Test**: Drive the rail model with a stale-marked instance and
assert the group renders its rows with the stale presentation, not the error
presentation; drive it with a genuinely errored instance and assert the error
presentation is unchanged.

**Acceptance Scenarios**:

1. **Given** an instance marked stale (status still `ok`), **When** the rail
   renders it, **Then** its session rows remain listed, the group is visually
   dimmed, and a compact staleness indicator is shown instead of the error
   block.
2. **Given** an instance whose grace window has been exhausted or whose
   failure is non-retryable, **When** the rail renders it, **Then** today's
   error presentation appears unchanged.
3. **Given** a stale instance that recovers, **When** the next snapshot
   arrives, **Then** the staleness indicator disappears without any layout
   change to the group.

---

### User Story 3 - Panes never depend on discovery liveness (Priority: P2)

Even past the grace window — the host has now been unreachable long enough
that discovery legitimately reports it as errored with no targets — the
user's already-open terminal panes stay mounted. Each card's own connection
state decides its presentation: a still-connected terminal stays fully
interactive; a dead one shows its dimmed/disconnected overlay. Discovery
coming back later does not remount or reset anything.

**Why this priority**: The grace window bounds how long *new* attachments are
authorized against stale data, but an *existing* attachment has its own live
connection whose health is directly observable — tearing it down on
discovery's say-so is wrong at any point, not just during the grace window.

**Independent Test**: Open panes, then serve a snapshot where the host's
targets are gone entirely; assert the panes remain mounted, resolved against
last-known target data, with presentation driven by each terminal's own
connection state.

**Acceptance Scenarios**:

1. **Given** open panes whose targets disappear from the served sessions
   listing (for any reason), **When** the workspace re-renders, **Then**
   the panes stay mounted, resolved against the last-known target data the
   console retains for them.
2. **Given** a mounted pane whose underlying terminal connection has died,
   **When** the card renders, **Then** the card's own connection state
   drives a dimmed/disconnected presentation with its existing reconnect
   affordances — independent of what discovery says about the host.
3. **Given** a pane the user explicitly closes, **When** it is removed,
   **Then** its retained target data is dropped with it (stickiness never
   resurrects closed panes).

---

### User Story 4 - Real failures still surface honestly (Priority: P3)

A host is genuinely down (or misconfigured). After the grace window of
consecutive retryable failures — or immediately, for deterministic failures
like authentication errors or missing host tooling — the console shows
today's true error state: the host errored in the rail, its targets gone
from the sessions listing, new attachments refused.

**Why this priority**: Resilience must not become blindness. The feature's
value depends on the error path remaining trustworthy — but this story
mostly *preserves* existing behavior rather than adding new behavior.

**Independent Test**: Simulate consecutive retryable failures past the grace
budget and assert the hard-error snapshot (cleared targets, error status) is
served; simulate each non-retryable status and assert it surfaces on the
first failure with no grace.

**Acceptance Scenarios**:

1. **Given** an instance failing retryably for longer than the configured
   grace budget, **When** the budget is exhausted, **Then** the served state
   becomes the failure status with cleared targets, exactly as today.
2. **Given** an instance whose discovery fails with a non-retryable status
   (authentication failure, missing host tooling, incompatible protocol,
   malformed response), **When** the cycle completes, **Then** that status
   is served immediately — no retention, no grace.
3. **Given** an instance with no prior successful snapshot (first discovery
   after service start), **When** its first cycle fails retryably, **Then**
   the failure is served as-is — there is nothing to retain.
4. **Given** an operator who wants a different tolerance, **When** they set
   the grace-budget configuration setting, **Then** the window honors it,
   and the default is approximately two minutes.

---

### Edge Cases

- **Service restart during a blip**: retention state is in-memory; after a
  restart there is no prior snapshot, so a failing instance surfaces its
  failure immediately (User Story 4, scenario 3). Acceptable — restart is
  an operator action, not a background blink.
- **Flapping instance** (alternating ok/timeout): each success resets the
  failure counter and clears staleness; each failure re-enters the grace
  window. The user sees the badge flicker, never the error block, and
  terminals are untouched.
- **Grace budget set to zero**: retention disabled; every retryable failure
  surfaces immediately (today's behavior). This is the escape hatch for
  operators who prefer strict reporting.
- **Stale targets that no longer exist on the host**: a user may open a
  terminal to a target that died during the blip. The attachment attempt
  fails at the terminal layer with its existing error handling — the same
  outcome as a target dying between two successful discovery cycles today.
- **Manual refresh during the grace window**: an explicit refresh request
  runs a fresh cycle; its result flows through the same retention rules
  (a retryable failure still yields the stale-ok state).
- **Mixed fleet**: staleness is strictly per-instance; one host's blip must
  not affect any other host's presentation or targets.
- **Clock concerns**: the grace budget is measured from consecutive-failure
  history tracked by the service, not from client clocks; the advisory
  last-success timestamp is informational only.
- **Both remote calls slow but individually under budget**: the timeout fix
  gives the combined operation a total budget of roughly the sum of its
  parts plus slack, so two individually-healthy calls no longer produce a
  spurious instance timeout.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: When a discovery cycle for an instance fails with a retryable
  status (timeout, unreachable) and a prior successful snapshot exists, the
  service MUST serve a state that keeps status `ok` and retains the prior
  snapshot's capability record and full target list.
- **FR-002**: The retained (stale) state MUST carry advisory fields
  distinguishing it from a fresh one: a staleness flag, the timestamp of the
  last successful discovery, a count of consecutive retryable failures, and
  the most recent failure's error detail. These fields MUST be present in
  the instance shape served to the console (fresh instances serve the
  non-stale defaults).
- **FR-003**: Because retained targets keep status `ok`, they MUST remain in
  the service's target index, so terminal open and reattach authorization
  continues to work against them throughout the grace window.
- **FR-004**: The service MUST bound retention by a configurable grace
  budget (environment setting `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S`,
  default ~120 seconds), measured as elapsed time since the last successful
  discovery: a retryable failure processed after that elapsed time exceeds
  the budget hardens into the real failure status with cleared targets,
  exactly as today. The consecutive-failure count is advisory only, never
  the trigger. A budget of zero MUST disable retention. The hard-error
  snapshot serves `stale` as false while keeping the last-success
  timestamp, failure count, and error detail populated.
- **FR-005**: Non-retryable statuses (authentication failure, missing host
  tooling, incompatible protocol, malformed response) MUST surface
  immediately with no retention, regardless of prior snapshots or budget.
- **FR-006**: A successful discovery cycle MUST fully replace the retained
  state: staleness cleared, failure counter reset, last-success timestamp
  updated, fresh targets served.
- **FR-007**: The per-instance discovery operation's outer time bound MUST
  accommodate both sequential remote calls (approximately twice the
  per-call timeout plus slack), so a slow first call cannot starve the
  second into a spurious instance timeout.
- **FR-008**: The console's navigation rail MUST render a stale instance as
  a dimmed group with its rows intact and a compact "not responding ·
  retrying" indicator — never the error presentation, and the host MUST
  never be dropped from the rail while stale. Genuine error states (grace
  exhausted or non-retryable) MUST keep today's error presentation.
- **FR-009**: The console's workspace MUST resolve open panes against a
  sticky last-known-target record so that a pane is never unmounted because
  its target disappeared from the served listing — during the grace window
  or after it. The sticky record is in-memory only (never persisted with
  the workspace layout); a page reload resolves panes through the existing
  path. Explicitly closing a pane MUST drop its sticky record.
- **FR-010**: A mounted terminal card's presentation (interactive vs
  dimmed/disconnected) MUST be driven by the terminal's own connection
  state, not by discovery status.
- **FR-011**: The instance shape change MUST flow through the generated
  contract pipeline: the service schema artifacts and the console's
  generated types MUST be regenerated together, and the schema-drift gates
  MUST pass (Constitution Principle IV).
- **FR-012**: Retention MUST be tracked and applied strictly per instance;
  one instance's failures MUST NOT affect another's served state.

### Key Entities

- **Discovery snapshot (per instance)**: the served record of an instance's
  reachability, capability, and session targets — extended with advisory
  staleness fields (stale flag, last-success timestamp, consecutive-failure
  count, last error detail).
- **Retention state (per instance, service-internal)**: the last successful
  snapshot plus the time of the last success, which decides whether the
  grace window is still open (the consecutive-failure count is advisory
  display data, never the trigger).
- **Sticky pane target record (console-internal)**: the last-known target
  data for each open pane, living with the pane's lifecycle rather than the
  discovery feed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single failed discovery cycle against a previously-healthy
  instance causes zero terminal disconnections and zero workspace pane
  unmounts (previously: all of the host's panes unmounted).
- **SC-002**: During a discovery blip shorter than the grace budget, the
  host's session targets remain listed and new terminal attachments to them
  succeed at the same rate as against a fresh snapshot.
- **SC-003**: Users no longer need a page refresh to recover from a
  transient discovery failure; recovery is automatic on the next successful
  cycle with no user action.
- **SC-004**: A genuinely-failed host surfaces as an error within one grace
  budget (default ~2 minutes) for retryable failures, and on the first
  cycle for deterministic failures — no regression in honest error
  reporting.
- **SC-005**: Under load where one remote call is slow but both individually
  complete within their per-call budget, the instance is reported healthy
  (previously: spuriously timed out).

## Assumptions

- Retention state may be in-memory only; surviving a service restart is out
  of scope (a restart forgets history and the first cycle after it is
  authoritative).
- The existing per-call timeout setting remains the per-call bound; the fix
  is to the combined outer bound, not to the individual call budgets.
- "Retryable" is exactly the set {timeout, unreachable}; all other failure
  statuses are deterministic. New statuses added later default to
  non-retryable unless classified otherwise.
- The advisory staleness fields are additive to the instance shape; no
  existing field changes meaning or type, so existing consumers keep
  working unmodified.
- The grace budget is service-wide (one setting for all instances), not
  per-instance; per-instance tuning is out of scope.
- The console's existing poll-failure tolerance (keeping last-known
  instances when the discovery API itself is unreachable) is correct and
  untouched; this feature addresses the served-content gap, not transport
  failures.
- Terminal-layer behavior on attaching to a dead target (error surfaced in
  the card) is already acceptable and unchanged.
