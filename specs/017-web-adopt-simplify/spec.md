# Feature Specification: Simplify Web Adoption & Close the Lifecycle

**Feature Branch**: `017-web-adopt-simplify`

**Created**: 2026-07-26

**Status**: Draft

**Input**: User description: "Simplify the CLI-to-web adoption surface by collapsing `remo web adopt` and `remo web push` into a single push operation where first-push-with-a-pairing-code IS adoption — one mental model, one code path, one doc section — while keeping the existing trust model intact. Add drift visibility and lifecycle closure: `remo web status`, an out-of-date nudge after registry-mutating commands, best-effort revocation, a `--force` flag, and multi-workstation flap detection. Also fix the mode-detection wart that makes bare-metal adopted operation unreachable."

## Clarifications

### Session 2026-07-26

- Q: Should the `remo web adopt` command be retained after the collapse into a single push? → A: Retain it for one release as a deprecated alias that delegates to the single push code path, then remove it.
- Q: How should multi-workstation flap detection behave in a non-interactive (`--yes`) push? → A: Print the warning and proceed; interactive pushes instead prompt to confirm or abort before overwriting.
- Q: Which mechanism resolves the mode-detection wart (bare-metal adopted vs. mount-configured)? → A: Both — an explicit environment-variable override plus a narrowed heuristic, where a non-writable state directory remains the authoritative signal for mount-configured.
- Q: When a workstation has pushed to more than one deployment, how does `remo web status` choose its target? → A: Implicit when exactly one deployment is cached; an explicit selector (deployment id / URL) is required when more than one is cached, and the selected deployment is shown in the output.
- Q: What shape is the deployment's mirror-identity marker used for flap detection? → A: A monotonic generation counter plus a best-effort last-push descriptor (timestamp and a workstation label); no secret or instance content is exposed.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One command to connect and re-sync a web deployment (Priority: P1)

An operator running a remo web deployment wants a single, memorable action to make the deployment mirror their local registry — whether it's the very first time (adoption) or the hundredth (re-sync). Today they must remember that the *first* time is `remo web adopt` and *every time after* is `remo web push`, two commands that behave almost identically and are documented as separate flows. The operator wants one command: paste a pairing code, and it does the right thing automatically.

**Why this priority**: This is the headline simplification and the core of the request ("one mental model, one code path, one doc section"). It removes a persistent point of confusion and is the foundation the other stories build on. It is independently valuable even if nothing else in this feature ships.

**Independent Test**: From a workstation with a populated registry, run the single push command against a fresh (never-adopted) deployment using a pairing code — the deployment becomes fully configured and mirrors the registry. Run the same command again against the now-adopted deployment — it re-syncs, skipping unchanged instances. Both paths produce the same on-screen summary format and both succeed with exit code 0.

**Acceptance Scenarios**:

1. **Given** a running deployment that has never been adopted and a valid pairing code, **When** the operator runs the unified push command, **Then** the deployment's service identity is authorized on each reachable direct-access instance, the full registry mirror plus verified host keys is applied, a service-side verification pass runs, and the deployment transitions to adopted — with no separate "adopt" step required.
2. **Given** a deployment already adopted from this workstation, **When** the operator runs the same push command with a fresh pairing code, **Then** instances unchanged since the last push are reported as `unchanged` (keyscan/authorize skipped), new or changed instances get the full trust treatment, and the full mirror is re-applied so removals propagate.
3. **Given** the operator invokes the command, **When** they supply the service URL and pairing code, **Then** resolution order is identical for both first-push and re-sync (argument → environment variable → interactive prompt) and nothing durable (no URL, no code) is persisted.
4. **Given** the deployment is mount-configured (read-only operator-provided registry/identity), **When** the operator runs the push command, **Then** it fails fast with a clear "adoption does not apply to a mount-configured deployment" message and changes nothing.
5. **Given** the existing trust model, **When** any push runs, **Then** the service-scoped identity, workstation-verified-host-keys-only rule, the single idempotent `remo-web@<deployment>` authorized_keys marker, the pairing-code-gated setup surface, and the never-copy-personal-keys guarantee all remain in force, unchanged.

---

### User Story 2 - See what has drifted since the last push, without contacting anything (Priority: P2)

An operator who has created, destroyed, or otherwise changed instances since their last push wants to know, at a glance and fully offline, how their web deployment's view differs from reality — which instances are new, which changed, which were removed — before deciding whether to re-sync.

**Why this priority**: Drift is invisible today; operators only discover staleness when a terminal fails to open in the browser. A read-only status command turns an implicit, surprising failure into an explicit, checkable state. It depends on the push cache that Story 1 already maintains, so it is naturally second.

**Independent Test**: Adopt/push a deployment, then add one instance, change one, and remove one in the local registry. Run the status command with no network available — it reports exactly one new, one changed, and one removed instance and contacts nothing.

**Acceptance Scenarios**:

1. **Given** a workstation that has previously pushed to a deployment, **When** the operator runs the status command, **Then** it compares the current local registry against the recorded push cache and reports each instance as `new`, `changed`, `removed`, or `in sync`, without making any network or SSH connection.
2. **Given** a registry-mutating command completes (creating, destroying, or syncing an instance, or adding/removing a registered SSH host), **When** a push cache exists on the workstation, **Then** a single-line notice tells the operator the web deployment is now out of date and how to re-sync.
3. **Given** no push cache exists (this workstation has never pushed), **When** a registry-mutating command completes, **Then** no out-of-date notice is printed (there is nothing to be out of date with respect to).
4. **Given** the local registry exactly matches the last push, **When** the operator runs the status command, **Then** it reports the deployment as in sync with a clear "nothing to push" outcome.

---

### User Story 3 - Removing an instance revokes the service's access to it (Priority: P2)

When an operator removes an instance and re-syncs, they expect the web service to lose access to that instance — not just to stop listing it. Today the service's `remo-web@` authorized_keys entry is left behind on the removed instance and the operator is told to delete it by hand.

**Why this priority**: Leaving authorized keys on decommissioned-from-the-mirror instances is a lingering-access hygiene gap. Closing it is the "lifecycle closure" half of the request. It is best-effort (the instance may be gone), so it is valuable but not a correctness blocker for Story 1.

**Independent Test**: Adopt a deployment covering two reachable instances, remove one from the registry, then push. The push attempts to delete the `remo-web@` line from the removed instance over the operator's own SSH access and reports success; inspecting that instance's authorized_keys confirms the line is gone. Repeat with the instance unreachable and confirm the push reports that revocation could not be performed, without failing the overall push.

**Acceptance Scenarios**:

1. **Given** a push that removes one or more instances from the mirror, **When** the push applies the new mirror, **Then** for each removed direct-access instance it attempts, over the operator's existing SSH access, to remove the `remo-web@` authorized_keys line, and reports per-instance whether revocation succeeded.
2. **Given** a removed instance is unreachable or the operator no longer has SSH access to it, **When** revocation is attempted, **Then** the push clearly reports that revocation could not be performed for that instance (with guidance to remove the line manually) and the overall push still completes successfully.
3. **Given** revocation runs, **When** it edits authorized_keys, **Then** it removes only lines carrying the `remo-web@` marker and leaves all other authorized keys untouched, and re-running is a no-op.
4. **Given** an instance was never adopted from this workstation (no cache entry) but is removed from the registry, **When** the push runs, **Then** revocation is still attempted best-effort against it if it is a direct-access entry the workstation can reach, and reported the same way.

---

### User Story 4 - Force a full re-authorization for instances rebuilt out of band (Priority: P3)

An operator who rebuilt an instance out-of-band (same registry entry, brand-new host keys and a wiped authorized_keys) wants to force the push to re-scan and re-authorize it, even though the registry-field fingerprint hasn't changed and the fast path would otherwise skip it as `unchanged`.

**Why this priority**: A real but occasional recovery path. The fingerprint fast path is correct for the common case; `--force` is the escape hatch for the case fingerprints structurally cannot detect. Useful, but narrower than Stories 1–3.

**Independent Test**: Adopt a deployment, then out-of-band reset one instance's host keys and remove its authorized_keys without changing its registry entry. A normal push reports it `unchanged` (stale). A push with `--force` re-runs keyscan and re-authorizes the service key for every instance regardless of fingerprint match.

**Acceptance Scenarios**:

1. **Given** an adopted deployment, **When** the operator runs the push command with the force flag, **Then** the fingerprint-based "unchanged" skip is bypassed for every instance: host keys are re-scanned and the service key re-authorized, exactly as on a first push.
2. **Given** the force flag is not supplied, **When** the operator pushes, **Then** the existing fingerprint fast path is preserved (unchanged instances are skipped) — force changes nothing about the default behavior.
3. **Given** a forced push, **When** an instance is now unreachable, **Then** it is reported with the same per-instance skip/flag outcomes as any other push (force does not turn a per-instance failure into a hard failure).

---

### User Story 5 - Warn before overwriting another workstation's mirror (Priority: P3)

Two operators (or one operator on two machines) each push to the same deployment. When workstation B pushes over a mirror last written by workstation A, B wants to be told it is about to overwrite someone else's push, so an accidental cross-workstation clobber (e.g. B has a narrower registry) doesn't silently wipe A's instances.

**Why this priority**: Protects against a real multi-workstation data-loss foot-gun, but only affects deployments touched from more than one workstation — a smaller population than the single-workstation majority the other stories serve.

**Independent Test**: Push from workstation A. From workstation B (which has never pushed to this deployment, so no local record of A's push), push to the same deployment — B detects that the live mirror was last written by a different push than B last knows about and warns before proceeding.

**Acceptance Scenarios**:

1. **Given** a deployment last pushed by workstation A, **When** workstation B pushes, **Then** B detects — from a mirror-identity marker the deployment reports — that the current mirror was written by a push B did not make, and surfaces a clear warning naming when/where the last push came from (to the extent that information is available) before applying its own mirror.
2. **Given** the same workstation pushes twice in a row with no intervening push from elsewhere, **When** the second push runs, **Then** no flap warning appears (the marker matches what this workstation last recorded).
3. **Given** a flap is detected in an interactive session, **When** the operator is warned, **Then** they can confirm or abort before the overwrite; in a non-interactive (`--yes`) run the warning is printed and the push proceeds (Clarifications Q2).
4. **Given** a first-ever push to a fresh deployment, **When** no prior mirror marker exists, **Then** no flap warning appears.

---

### User Story 6 - Run an adopted web service on bare metal (Priority: P2)

An operator wants to run `remo web serve` directly on a workstation (not in a Docker read-only-mount deployment) and adopt it. Today this is impossible: the service classifies any deployment where a personal SSH key (`~/.ssh/id_*`) is readable as `mount_configured`, and workstations always have personal keys — so the setup surface refuses adoption and the deployment is permanently stuck in the wrong mode.

**Why this priority**: This is a correctness wart that makes a legitimate, documented deployment shape (bare-metal serve) unreachable. It is independent of the push simplification but shares the same subsystem, and it unblocks the bare-metal path that Stories 1–5 would otherwise be unusable on. Prioritized above the P3 conveniences.

**Independent Test**: On a workstation that has a personal `~/.ssh/id_ed25519` and a writable REMO_HOME, start the service and query its mode; it must be adoptable (unconfigured → adopted after a push), not forced to `mount_configured`. Separately, a Docker deployment with a read-only-mounted registry and identity must still classify as `mount_configured`.

**Acceptance Scenarios**:

1. **Given** a bare-metal `remo web serve` on a workstation with a writable state directory, a service-scoped identity (or the ability to create one), and a personal `~/.ssh/id_*` present, **When** the operator adopts/pushes to it, **Then** the deployment is treated as adoptable and reaches adopted mode — the mere presence of a personal key no longer forces `mount_configured`.
2. **Given** a Docker deployment configured via read-only mounts (non-writable state directory, operator-mounted registry and identity), **When** its mode is detected, **Then** it still classifies as `mount_configured` and the read-only-mount story is unchanged.
3. **Given** the operator needs to be explicit about intent, **When** they set the documented mode override, **Then** the service honors it deterministically regardless of ambient heuristics (without weakening the read-only-mount protection).

---

### Edge Cases

- **Empty local registry**: Pushing an empty registry would wipe the deployment's instance list; the command must refuse unless the operator explicitly opts in (preserving today's `--allow-empty` guard), whether it is a first push or a re-sync.
- **Expired/rotated pairing code**: A dormant setup surface (code expired, rotated by a page reopen, or wrong URL) must produce the same clear "reopen the page for a fresh code" guidance regardless of whether this is a first push or re-sync.
- **Payload-version skew**: If the deployment cannot accept this workstation's registry payload version, the push must abort before any instance processing or mutation.
- **Status against multiple deployments**: A workstation that has pushed to more than one deployment must have an unambiguous way to select which deployment `remo web status` compares against.
- **Status with no cache**: Running status before ever pushing must report a clear "no prior push recorded" state rather than an error or an empty diff.
- **Revocation vs. re-adopt race**: If an instance removed from the mirror is simultaneously being re-added elsewhere, best-effort revocation must not corrupt authorized_keys (marker-scoped, atomic, idempotent).
- **Force + removed instances**: A forced push that also removes instances must both re-authorize survivors and attempt revocation on the removed ones.
- **Nudge after a no-op mutation**: A registry-mutating command that ends up changing nothing should not falsely claim the deployment is now out of date. (Guidance in Assumptions: the nudge is acceptable to print whenever a mutating command runs and a cache exists; a false positive is low-cost and status is authoritative.)
- **Flap detection with a mount-configured deployment**: Flap markers do not apply where the mirror is operator-provided; the mount-configured refusal takes precedence.

## Requirements *(mandatory)*

### Functional Requirements

#### Unified push (US1)

- **FR-001**: The system MUST provide a single push command that adopts a not-yet-adopted deployment on first use and re-syncs an already-adopted deployment on subsequent use, with no separate adopt command required in the operator's workflow.
- **FR-002**: The unified push MUST auto-detect whether the deployment is already adopted and behave correctly for both cases without the operator declaring which case applies.
- **FR-003**: The adopt and push behaviors MUST be implemented as one code path (no duplicated first-push vs. re-sync orchestration), and the operator-facing documentation MUST describe a single flow.
- **FR-004**: The unified push MUST preserve the existing trust model in full: service-scoped identity only, host keys included only when workstation-verified, a single idempotent `remo-web@<deployment>` authorized_keys marker, a pairing-code-gated setup surface, and never copying personal SSH keys to any instance or to the service.
- **FR-005**: URL and pairing-code resolution (argument → environment → prompt) and the "nothing durable is persisted" guarantee MUST be identical on every push.
- **FR-006**: The unified push MUST retain all existing hard-failure behaviors: mount-configured refusal, empty-registry guard (with explicit opt-in), payload-version skew abort before any mutation, and dormant-setup-surface guidance.
- **FR-007**: Per-instance skips and flags (unreachable, no-trust, host-key mismatch / potential MITM) MUST remain non-fatal and reported in a summary, with overall completion mapping to success.
- **FR-008**: The `remo web adopt` command MUST be retained for one release as a deprecated alias that delegates to the same single push code path and clearly signals its deprecation in favor of the unified push, then removed (Clarifications Q1).

#### Drift visibility (US2)

- **FR-009**: The system MUST provide a status command that compares the current local registry against the recorded push cache and reports, per instance, whether it is new, changed, removed, or in sync.
- **FR-010**: The status command MUST NOT make any network or SSH connection — it operates purely on local state.
- **FR-011**: The status command MUST report a clear, non-error outcome when no prior push has been recorded, and a clear "in sync / nothing to push" outcome when the registry matches the last push.
- **FR-012**: The status command MUST report against the single cached deployment implicitly when exactly one is recorded, and MUST require an explicit selector (deployment id / URL) when more than one is recorded; in all cases the reported-against deployment MUST be visible in its output (Clarifications Q4).
- **FR-013**: After any registry-mutating operation completes — instance create, instance destroy, sync (including the shared reconcile/`run_sync` path), and registered-SSH-host add and remove — the system MUST, when a push cache exists, print a single-line notice that the web deployment is now out of date and how to re-sync.
- **FR-014**: The out-of-date notice MUST be suppressed when no push cache exists.

#### Revocation (US3)

- **FR-015**: When a push removes one or more instances from the mirror, the system MUST attempt, best-effort over the operator's existing SSH access, to remove the `remo-web@` authorized_keys line from each removed direct-access instance.
- **FR-016**: Revocation MUST remove only lines carrying the `remo-web@` marker, leave all other authorized keys intact, and be idempotent (re-running against an already-revoked instance is a no-op).
- **FR-017**: When revocation cannot be performed (instance unreachable, no SSH access, remote error), the system MUST report that clearly per instance with remediation guidance, and MUST NOT fail the overall push.
- **FR-018**: Revocation outcomes (revoked / could-not-revoke) MUST be surfaced in the push summary alongside per-instance adoption outcomes.

#### Force re-authorization (US4)

- **FR-019**: The push command MUST accept a force option that bypasses the fingerprint-based "unchanged" skip, re-running host-key scan and service-key re-authorization for every instance.
- **FR-020**: Without the force option, the existing fingerprint fast path MUST be preserved unchanged.
- **FR-021**: A forced push MUST keep per-instance failures non-fatal (force affects which instances are processed, not how failures are classified).

#### Multi-workstation flap detection (US5)

- **FR-022**: The deployment MUST expose, via its setup status surface, a mirror-identity marker consisting of a monotonic generation counter plus a best-effort last-push descriptor (timestamp and workstation label), letting a pushing workstation detect that the current mirror was last written by a push it did not make (Clarifications Q5).
- **FR-023**: The mirror-identity marker MUST be updated by the deployment on each successful mirror apply.
- **FR-024**: On push, when the workstation detects that the live mirror was written by a different push than it last recorded, the system MUST surface a clear warning — naming when/where the last push came from to the extent available — before overwriting.
- **FR-025**: No flap warning MUST appear for a first-ever push to a fresh deployment, nor for consecutive pushes from the same workstation with no intervening external push.
- **FR-026**: In an interactive session the operator MUST be prompted to confirm or abort before the overwrite; a non-interactive (`--yes`) push MUST print the warning and proceed (Clarifications Q2).
- **FR-027**: The mirror-identity marker MUST NOT expose secrets and MUST NOT reveal instance contents to an unauthenticated caller (it is served only over the already-pairing-gated setup surface).

#### Mode-detection fix (US6)

- **FR-028**: The system MUST make bare-metal adopted operation reachable: a deployment with a writable state directory and a service-scoped identity MUST be adoptable even when a personal SSH key is readable on the same host.
- **FR-029**: The system MUST continue to classify a Docker read-only-mount deployment (non-writable state directory, operator-mounted registry and identity) as mount-configured — the read-only-mount protection MUST NOT be weakened.
- **FR-030**: The system MUST provide a deterministic explicit environment-variable override to select adopted vs. mount-configured mode AND, in its absence, a narrowed heuristic in which a non-writable state directory remains the authoritative mount-configured signal while a service identity on a writable state directory yields adopted; both MUST be documented (Clarifications Q3).

#### Cross-cutting

- **FR-031**: All new and changed operator-facing behavior (unified push, status, nudge, revocation, force, flap detection, mode override) MUST be reflected in the documentation, consolidated into the single adoption doc section rather than duplicated across an adopt section and a push section.

### Key Entities *(include if feature involves data)*

- **Web deployment**: A running remo web service instance, identified by its service-scoped deployment id, that mirrors an operator's registry. Has a mode (unconfigured / adopted / mount-configured / broken) and, after this feature, a mirror-identity marker.
- **Push cache**: The non-secret, workstation-local record of the last successful push per deployment id — per-instance fingerprints and verified host-key lines. Read by status (offline diff) and by push (fast-path skip). Holds no URL and no pairing code.
- **Mirror-identity marker**: A deployment-reported value (generation counter and/or last-push descriptor: who/when) that distinguishes one workstation's push from another's, enabling flap detection.
- **Instance drift state**: The per-instance comparison outcome between the local registry and the push cache — new, changed, removed, or in sync.
- **Revocation outcome**: The per-removed-instance result of the best-effort authorized_keys cleanup — revoked, or could-not-revoke with a reason.
- **Configuration state**: The deployment's derived mode, now determined so that a bare personal SSH key no longer forces mount-configured when a service identity and writable state directory are present.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can connect a brand-new deployment and later re-sync it using the same single command, with zero need to know or choose between "adopt" and "push" — verified by both flows succeeding through one command.
- **SC-002**: The operator-facing documentation describes web adoption in one consolidated section (no separate adopt and push sections), and the first-push and re-sync flows share one implementation path.
- **SC-003**: After changing the registry (add, remove one instance, and one modification) since the last push, `remo web status` reports exactly those differences (one new, one removed, one changed) in under 2 seconds and with no network access.
- **SC-004**: Every registry-mutating command (create, destroy, sync, SSH-host add, SSH-host remove) prints the out-of-date notice when a push cache exists, and prints nothing extra when none exists — verified across all five commands.
- **SC-005**: Removing a reachable instance and re-syncing leaves no `remo-web@` line in that instance's authorized_keys, with all other authorized keys intact; when the instance is unreachable the push still completes and the operator is told revocation could not be performed.
- **SC-006**: A push with the force option re-scans and re-authorizes every instance including ones a normal push would report `unchanged`, recovering an out-of-band-rebuilt instance in a single command.
- **SC-007**: A push from a second workstation over another workstation's mirror produces a visible warning before overwriting; two consecutive pushes from the same workstation produce none.
- **SC-008**: A bare-metal `remo web serve` on a workstation that has personal SSH keys can be adopted and reach adopted mode, while a Docker read-only-mount deployment continues to be detected as mount-configured — both verified in the same test matrix.
- **SC-009**: The full existing trust model is preserved: no personal key is ever copied, host keys are included only when workstation-verified, and the single `remo-web@<deployment>` marker remains the only service-authorization line on any instance — verified by inspecting instances after a push.

## Assumptions

- **`remo web adopt` fate (decided — Clarifications Q1)**: `remo web push` becomes the single documented command. `remo web adopt` is retained for one release as a thin, clearly-deprecated alias that delegates to the same push code path (preserving muscle memory and existing docs/links), then removed.
- **Flap-detection posture (decided — Clarifications Q2)**: Interactive pushes prompt for confirmation before overwriting another workstation's mirror; non-interactive pushes (`--yes`) print the warning and proceed, so automation is not deadlocked.
- **Mirror-identity marker shape (decided — Clarifications Q5)**: The deployment reports a monotonically increasing generation counter plus a best-effort last-push descriptor (timestamp and a workstation label). The workstation records the counter it last wrote in its push cache; a mismatch triggers the flap warning. No secret or instance content is exposed.
- **Mode-detection fix mechanism (decided — Clarifications Q3)**: An explicit, documented environment-variable override deterministically forces adopted vs. mount-configured mode. In the absence of an override, the heuristic is narrowed so that a non-writable state directory remains the authoritative signal for mount-configured, and the presence of a service-scoped identity on a writable state directory yields adopted even when a personal `~/.ssh/id_*` is readable. The mere presence of a personal key no longer forces mount-configured. This keeps the Docker read-only-mount story (non-writable mount) fully intact.
- **Status deployment selection (decided — Clarifications Q4)**: When the push cache records exactly one deployment, status reports against it implicitly. When it records more than one, the operator selects the target (by deployment id / URL), and the reported-against deployment is shown in the output.
- **Nudge precision (default: cache-existence gated, not diff-gated)**: The out-of-date notice is printed whenever a registry-mutating command runs and a push cache exists, without recomputing an exact diff inline. A rare false-positive nudge after a no-op mutation is acceptable because the offline status command is the authoritative, cheap follow-up.
- **Revocation transport**: Revocation reuses the operator's existing (ambient) SSH access to the instance — the same access adoption uses to authorize the key — never the service identity, and applies the same marker-scoped, atomic, idempotent authorized_keys edit used to install the key.
- **Scope boundary**: This feature builds on the already-landed registry accessor (registry v2) and the shared reconcile engine; it does not change the registry file format, the pairing-code security model, the payload-version handshake, or the browser terminal-brokering behavior.
- **Existing safeguards carry over**: `--allow-empty`, `--via` tunneling, `--yes`, payload-version skew abort, and the SSM-instances-excluded-from-host-key-and-key-push rule remain in force for the unified push.
