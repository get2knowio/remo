# Feature Specification: Make deacon the Default Devcontainer Runtime — Close the Parity Gaps, Then Flip

**Feature Branch**: `026-deacon-default-runtime`

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "Refresh the deacon pin, resolve the projects.rebuild guard, answer the nested-overlayfs question, then flip devcontainer_runtime's default to deacon. The runtime seam already exists and works; this is about closing the remaining parity gaps and flipping a default without a silent failure on any host shape." (Spec Prompts queue, "Make deacon the default devcontainer runtime", drafted 2026-09-20 against `ansible/` at `680dbf7`; deacon issues #688, #117, #371, #265.)

## Context

remo provisions every host with a devcontainer runtime and bakes the choice
into the launcher scripts (`devshell`, `project-launch`, `remo-host`). Two
runtimes exist behind one seam: the Node-based reference CLI
(`@devcontainers/cli`) and **deacon**, a single-binary Rust reimplementation
that needs no Node.js. A host can already be provisioned with deacon by
opting in; every provider shares the selection because all configure
playbooks include the same task file. What is missing is the confidence to
make deacon the default, and three findings from the audit shape that:

1. **The pin is stale.** remo pins `0.2.0-rc.11` (2026-07-05); deacon has
   shipped v0.2.0, v0.3.0 and **v0.4.0** (2026-08-16).
2. **The rebuild guard is stale.** Two places in `remo-host` assert deacon
   lacks `--remove-existing-container` and therefore hide the console's
   Rebuild action on deacon hosts. Verified 2026-09-29 against the v0.4.0
   binary: `deacon up` accepts `--remove-existing-container`,
   `--build-no-cache`, `--workspace-folder` and `--trust-workspace-persist`;
   `deacon exec --workspace-folder` exists. The three tracked deacon issues
   (#688, #117, #371) are all closed. The guard is wrong at the new pin.
3. **The nested-overlayfs fix covers only the reference CLI.** On hosts
   whose kernel refuses nested overlayfs mounts (OrbStack machines), remo
   installs a `native`-snapshotter buildx builder and a `devcontainer` shim
   that sets the build environment per project. deacon gets the builder but
   no shim. Verified in deacon's v0.4.0 source: image builds run
   `docker buildx build` (which honours a `BUILDX_BUILDER` environment
   variable but nobody sets it for deacon), Compose builds run
   `docker compose build`, and `--buildkit auto` follows `DOCKER_BUILDKIT`;
   deacon's updateUID stage is a `docker exec`, not a plain `docker build`,
   so that half of the reference CLI's problem does not apply. Nothing has
   been run on a real affected host. Flipping the default there would
   reintroduce the silent build failure #160 existed to kill.

A fourth question the prompt raises is what happens to hosts that already
exist: the derived variables are late-bound, so a re-run of configure or
upgrade would silently move a reference-CLI host to deacon, and deacon
(#265, closed) deliberately isolates its Docker state from the reference
CLI's, so an already-running project would not be adopted.

## Clarifications

### Session 2026-09-29

- Q: Is `--remove-existing-container` supported at the new pin? → A: Yes,
  verified by running the v0.4.0 macOS binary's `up --help` (and
  `--build-no-cache`, `--trust-workspace-persist`, `--workspace-folder`;
  `exec --workspace-folder`). Both stale guards are removed; the
  capability-advertisement mechanism stays.
- Q: Which of the three nested-overlayfs outcomes applies? → A: Outcome 3,
  **conditional flip**: on hosts detected as `docker_nested_overlayfs`, the
  automatic choice stays the reference CLI (which has the shim); elsewhere
  it is deacon. Neither outcome 1 nor 2 can be claimed without a real
  OrbStack host; the candidate environment for a deacon shim
  (`BUILDX_BUILDER=remo-native`; `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` for
  Compose projects) is recorded in the docs and a tracked issue, marked not
  verified.
- Q: How does the default flip without changing a host that already exists?
  → A: A new runtime value **`auto`** becomes the default on both the CLI
  and the Ansible side. `auto` resolves on the host, once, to a concrete
  runtime and records it in a marker file in the workspace account's home;
  later configure/upgrade runs keep the recorded runtime. A host with no
  marker but the reference CLI already installed is treated as a
  reference-CLI host (a legacy host never flips silently). An explicit
  `--devcontainer-runtime deacon|devcontainer` always wins and rewrites the
  marker.
- Q: Are mixed-runtime hosts supported? → A: Not supported; refused by
  design rather than undefined. Configure installs and wires exactly one
  runtime. Switching explicitly is allowed and documented: stop running
  projects first, then switch, then rebuild each project fresh on first
  launch (the previous runtime's containers are not adopted and are left for
  the operator to remove). The other binary is left on disk, unused.
- Q: Does the remo-host protocol version change? → A: No. `operations` is
  the honest mechanism; deacon hosts now advertise `projects.rebuild`
  because the binary supports it, and a host that cannot still omits it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A new host gets deacon by default (Priority: P1)

An operator creates or configures a fresh host with no runtime flag. On an
ordinary host (Proxmox, Incus, Hetzner, AWS, or an added SSH host) the
launcher scripts invoke deacon at the current stable release; the reference
CLI is not installed. On an OrbStack-style host whose kernel refuses nested
overlayfs, the same command installs the reference CLI with its shim, and
the operator can see why.

**Why this priority**: it is the flip itself, and the conditional branch is
what keeps it from silently breaking builds on the one host shape that
cannot use deacon yet.

**Independent Test**: render the runtime-resolution logic for the four host
shapes (fresh ordinary host, fresh nested-overlayfs host, legacy host with
the reference CLI, host with a recorded marker) and assert the effective
runtime; render the launcher templates with `deacon` and check every call
site; the deacon role's version pin is a stable release and its
idempotency check matches it exactly.

**Acceptance Scenarios**:

1. **Given** a fresh host and no runtime flag, **When** it is configured,
   **Then** the effective runtime is deacon at the pinned stable release,
   the launcher scripts call `deacon up … --trust-workspace-persist` and
   `deacon exec`, and the marker records `deacon`.
2. **Given** a fresh host detected as refusing nested overlayfs, **When** it
   is configured with no flag, **Then** the effective runtime is the
   reference CLI, the shim is installed, the marker records `devcontainer`,
   and the run's output names the reason.
3. **Given** a configured deacon host, **When** configure runs again with
   the same inputs, **Then** the deacon role reports no change (the
   installed version equals the pin) and the marker is unchanged.
4. **Given** an explicit `--devcontainer-runtime devcontainer`, **When** a
   fresh host is configured, **Then** the reference CLI is used exactly as
   before this feature.

---

### User Story 2 - Rebuild works from the console on deacon hosts (Priority: P1)

A user of the web console clicks Rebuild on a project hosted on a deacon
host. The host advertises `projects.rebuild`, the job starts, and deacon
rebuilds the container with `--remove-existing-container` (and
`--build-no-cache` when asked).

**Why this priority**: today a deacon host loses a shipped console feature
for no reason; flipping the default would make that the common case.

**Independent Test**: render `remo-host` with deacon and assert
`capabilities --json` lists `projects.rebuild` and `projects rebuild`
proceeds past the runtime check (its remaining validation is unchanged).

**Acceptance Scenarios**:

1. **Given** `remo-host` rendered for deacon, **When** `capabilities --json`
   runs, **Then** `operations` includes `projects.rebuild`.
2. **Given** the same host, **When** `projects rebuild --project X --json`
   runs, **Then** it is not refused for the runtime; the argument
   validation and the detached job behave exactly as on a reference-CLI
   host, and the job's argv is `deacon up --workspace-folder <dir>
   --remove-existing-container [--build-no-cache]`.
3. **Given** a host provisioned with a runtime that genuinely lacks the
   flag (none today), **Then** the omission mechanism still exists and is
   tested, so a future gap degrades honestly instead of failing at call
   time.

---

### User Story 3 - Existing hosts do not change under their users (Priority: P1)

An operator runs `remo <type> upgrade` or `remo configure` on a host that
was provisioned with the reference CLI before this feature. The host keeps
the reference CLI; running projects are untouched. When the operator
explicitly switches a host's runtime, the docs tell them to stop projects
first and rebuild fresh, and the host records the new choice so the next
upgrade does not switch back.

**Why this priority**: a running project silently becoming unreachable after
an upgrade is the failure the prompt names as the one to design against.

**Independent Test**: resolution cases for "no marker + reference CLI
present" and "marker present" both keep the runtime; "explicit flag"
overrides and the marker task writes the new value; a doc test that the
switch runbook exists and names the stop-then-rebuild sequence.

**Acceptance Scenarios**:

1. **Given** a host with the reference CLI installed and no marker, **When**
   configure runs with the default, **Then** the effective runtime is the
   reference CLI and a marker recording it is written.
2. **Given** a host whose marker says `devcontainer`, **When** configure runs
   with the default, **Then** the runtime stays the reference CLI even
   though the host is not a nested-overlayfs host.
3. **Given** the same host, **When** configure runs with
   `--devcontainer-runtime deacon`, **Then** deacon is installed, the
   scripts are re-pointed, and the marker now says `deacon`.
4. **Given** a switched host, **Then** the docs state that projects must be
   stopped before switching, that the previous runtime's containers are not
   adopted, that a fresh rebuild is forced on first launch, and that both
   binaries on one host is unsupported.

---

### User Story 4 - The nested-overlayfs story is honest (Priority: P2)

An operator with an OrbStack machine reads `docs/nested-overlayfs.md` and
learns that the automatic choice keeps the reference CLI there, why, what
the candidate deacon settings are, and that they are not yet verified. An
operator who forces deacon on such a host is warned by the configure run
that Compose-based builds are unverified there.

**Why this priority**: the failure is silent and lands days later; the
documentation and the warning are what make the conditional flip safe.

**Independent Test**: the configure task file emits the warning only when
the effective runtime is deacon on a nested-overlayfs host; the doc names
the conditional rule and the tracked issue.

**Acceptance Scenarios**:

1. **Given** a nested-overlayfs host and `--devcontainer-runtime deacon`,
   **When** configure runs, **Then** deacon is installed and the run prints
   a task named for the unverified status pointing at the doc.
2. **Given** the docs, **Then** `docs/nested-overlayfs.md` states the
   conditional default, the reason, the candidate environment for a future
   deacon shim, and "not verified on a real host" with the issue link.

---

### User Story 5 - Documentation and tool tables match (Priority: P3)

`ansible/README.md`'s tool list and role table, `docs/proxmox.md`'s runtime
section, the README environment-variable table, and the CLAUDE.md/AGENTS.md
diagrams describe `auto`, the stable pin, and the conditional rule.

**Independent Test**: docs-structure gate; grep-based doc tests for the
words that must appear (`auto`, the stable version, the conditional rule).

**Acceptance Scenarios**:

1. **Given** the docs, **Then** none of them calls deacon "experimental" as
   the default's description, the tool table lists both runtimes with the
   selection rule, and the CLI help for `--devcontainer-runtime` documents
   `auto`.

---

### Edge Cases

- **Marker unreadable or holds an unknown value**: treated as absent; the
  legacy-detection rule (reference CLI present?) decides; the run prints
  what it decided.
- **Marker says deacon but the binary is missing** (someone removed it):
  the deacon role reinstalls it; that is the existing idempotency path.
- **`--skip devcontainers`**: no runtime is installed and no marker is
  written; the launcher scripts keep whatever the templates render for the
  effective runtime, exactly as today.
- **Host reinstalled from scratch with an old marker on a persistent home
  volume** (Hetzner volume reuse): the marker is honoured, which is the
  desired stickiness.
- **Explicit `auto` passed on the command line**: identical to omitting the
  flag.
- **Nested-overlayfs host explicitly forced to deacon**: allowed, warned,
  and the shim is not installed (it wraps the reference CLI only).
- **Rebuild on a host where the runtime lacks the flag**: still omitted from
  `operations`; the mechanism is preserved by a test that renders with a
  hypothetical unsupported runtime name.

## Requirements *(mandatory)*

### Functional Requirements

**Pin**

- **FR-001**: The deacon role MUST pin the current stable release (v0.4.0)
  and its idempotency check MUST compare the installed version to that pin
  exactly (a stable version with no release-candidate suffix).
- **FR-002**: The spec MUST record which verbs and flags remo invokes and how
  each was verified against the pinned binary (`up`, `exec`,
  `--workspace-folder`, `--trust-workspace-persist`,
  `--remove-existing-container`, `--build-no-cache`).

**Rebuild parity**

- **FR-003**: `remo-host` MUST advertise `projects.rebuild` and execute
  `projects rebuild` on deacon hosts; the two runtime guards MUST be removed.
- **FR-004**: The capability-omission mechanism MUST be preserved: a runtime
  that lacks `--remove-existing-container` MUST be omitted from
  `operations` and refused at call time with the existing exit code, and a
  test MUST prove it with a hypothetical runtime.
- **FR-005**: The remo-host protocol version MUST NOT change.

**Runtime resolution**

- **FR-006**: The runtime value set MUST become `auto`, `deacon`,
  `devcontainer`, with `auto` the default for the CLI flag, the environment
  variable fallback, and the Ansible variable.
- **FR-007**: `auto` MUST resolve on the host, before any runtime role runs,
  to: the marker's value if a valid marker exists; else `devcontainer` if
  the reference CLI is already installed (legacy host); else
  `devcontainer` if the host is detected as refusing nested overlayfs; else
  `deacon`.
- **FR-008**: An explicit `deacon` or `devcontainer` MUST win over the marker
  and every detection rule.
- **FR-009**: After the workspace account exists, the effective runtime MUST
  be recorded in a marker file in that account's home (owner-only write,
  world-readable), written only when its content changes.
- **FR-010**: Every consumer of the runtime (both runtime roles' selection,
  `devcontainer_cli_bin`, `devcontainer_up_extra_args`, the launcher
  templates, `remo-host`) MUST use the effective runtime, never the raw
  variable.
- **FR-011**: Both runtimes MUST remain under the `configure_devcontainers`
  toggle; `--only`/`--skip` semantics MUST be unchanged.
- **FR-012**: On a nested-overlayfs host whose effective runtime is deacon,
  configure MUST print a task whose name states that Compose builds are
  unverified there and points at `docs/nested-overlayfs.md`.

**Existing hosts**

- **FR-013**: `upgrade`/`configure` with the default MUST NOT change the
  runtime of a host that has a marker or has the reference CLI installed.
- **FR-014**: Mixed-runtime hosts MUST be documented as unsupported; the
  documented switch procedure MUST say: stop projects, switch explicitly,
  rebuild fresh on first launch, previous containers are not adopted.

**Docs and gates**

- **FR-015**: `docs/nested-overlayfs.md`, `docs/proxmox.md`,
  `ansible/README.md`, `README.md`, `CLAUDE.md`/`AGENTS.md` MUST describe
  `auto`, the pin and the conditional rule; nothing MUST claim the deacon
  path is verified on a nested-overlayfs host.
- **FR-016**: All Ansible additions MUST use `| default()` on every
  registered-variable access; the reference-CLI path MUST keep working
  exactly as today.
- **FR-017**: Deferred work (the OrbStack proof / deacon shim; the live
  manual gate) MUST be tracked as issues, never inline TODOs.

### Key Entities

- **Runtime value** (`auto` | `deacon` | `devcontainer`): the operator's
  request.
- **Effective runtime** (`deacon` | `devcontainer`): what the host actually
  gets; the only thing consumers read.
- **Runtime marker** (`~/.remo-devcontainer-runtime`): the host's recorded
  effective runtime; the source of stickiness.
- **Capability advertisement** (`operations` in `remo-host capabilities`):
  how a host says what it can do.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The rendered `remo-host` for deacon advertises
  `projects.rebuild`, and the rebuild path is exercised past the runtime
  check; the omission path still passes for a hypothetical runtime.
- **SC-002**: Resolution tests cover all four host shapes plus explicit
  overrides and an invalid marker, and pass.
- **SC-003**: The full Python suite, ruff, mypy and the docs-structure gate
  pass; every `.rc`/`.stdout` access under `ansible/` uses `| default()`.
- **SC-004**: Every launcher call site renders with deacon at the new pin
  and the flags verified in FR-002.
- **SC-005** (manual gate, tracked): provision a host with the default,
  confirm `capabilities --json` operations, launch a project, attach through
  `remo web`, run configure twice (second run: no change), and build a
  Compose-based devcontainer on a nested-overlayfs host with deacon forced;
  record the result in `docs/nested-overlayfs.md`.

## Assumptions

- deacon v0.4.0 is the stable pin; a newer release during implementation is
  a one-line bump with the same verification.
- The reference CLI's install path (`npm prefix -g`/bin/devcontainer) is the
  legacy-host signal; it is what the nested_docker role already probes.
- The marker lives in the workspace account's home because the runtime is
  per-account state (launcher scripts are installed there).
- No live host is available in this run; live verification is the tracked
  manual gate, and docs say "not verified" until it runs.
