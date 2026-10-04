# Feature Specification: `remo resume` — Put Each Terminal Tab Back Where It Was

**Feature Branch**: `028-tab-resume`

**Created**: 2026-10-04

**Status**: Draft

**Input**: User description: "`remo resume` — per-terminal-tab session resume after a dropped SSH connection. The user runs `remo shell` in several laptop terminal tabs, each attached (via the host-side project-menu) to a different devcontainer project's zellij session. They close the laptop; every ssh connection drops and each tab is back at the local prompt. In each tab the user wants to type one command and be back exactly where that tab was. Agreed design: (1) client-side, `remo shell` records tab id → host, the tab id coming from the terminal's own per-tab environment variable; (2) host-side, the client forwards the tab id over ssh, the project menu and project launcher record which project that tab last attached to, and `remo-host` exposes a lookup so `remo resume` reattaches with zero prompts; (3) any missing piece degrades to plain `remo shell` behaviour — the host menu already marks live sessions — and no client-side session picker is built. Must work for every provider type and added `ssh` hosts; tab ids are untrusted input on the host."

## Context

The common way to use remo is `remo shell` with no project argument: the
client opens a plain SSH login, the host's `.bashrc` starts `project-menu`,
and the user picks a project there. The menu runs `zellij attach --create
<project>`, so every project's terminal lives in a zellij session named after
the project directory, and a devcontainer project's shell lives inside that
session.

When the laptop sleeps, the SSH connection dies. On the host only the zellij
*client* dies with it; the session keeps running detached, with the user's
work intact. `remo shell -p <project>` already reattaches to such a session
(fixed for every entry path in 4.4.2, #240). So everything needed to resume
already exists on the host — **except the knowledge of which project each tab
was in**:

1. **The client never learns the project.** Without `-p`, the choice happens
   entirely inside the host-side menu, invisible to the workstation.
2. **The client remembers nothing.** remo persists no per-session state on
   the workstation — not the last host, not the last project.
3. **The host remembers nothing per tab.** `remo-host sessions list` reports
   which sessions are live, but not who attached to them or from where.

A user with four tabs open therefore reconnects each one by hand: run
`remo shell`, pick the host (if several), find the right ⚡ project in the
menu, and hope they remember which tab held which project.

Terminal emulators give each tab a stable identifier in the environment
(iTerm2 `ITERM_SESSION_ID`, macOS Terminal `TERM_SESSION_ID`, Windows Terminal
`WT_SESSION`, kitty `KITTY_WINDOW_ID`, WezTerm `WEZTERM_PANE`, tmux
`TMUX_PANE`), and that identifier survives the laptop sleeping because the
tab itself stays open. That identifier is the key this feature is built on.

## Clarifications

### Session 2026-10-04

Run unattended (speckit-next): each answer is the recommended option, accepted
on the user's behalf.

- Q: Should plain `remo shell` auto-resume when the tab has a live record? → A: No. `remo shell` keeps today's behaviour exactly (it only *records*); resuming is always the explicit `remo resume` command, so nobody is surprised by being dropped into an old session.
- Q: How is a tmux pane identified, given `%N` pane ids repeat across tmux servers? → A: The tmux identity is the socket path (first field of `$TMUX`) plus `TMUX_PANE`; the server pid is excluded so a pane id stays the same key for the life of that tmux server's socket.
- Q: When the workstation record has a project (from `-p`) and the host also has a record for the same tab key, which wins? → A: The host record wins whenever the host supports the lookup and has an entry — it alone sees in-session menu switches. The workstation's project is used only when the host does not support the lookup or has no entry, and its liveness is then checked with the existing `remo-host sessions list`, which every configured host already supports.
- Q: How long may the resume lookup take before falling back? → A: 5 seconds for the lookup round-trip; on timeout, resume proceeds as for a host without the lookup (FR-010's workstation-project path, else the host menu).
- Q: Does `remo resume --forget` / `--forget-all` also clear host-side records? → A: No — workstation only. Host records hold no raw identifiers (only salted digests a host cannot reverse) and expire by pruning; without the workstation record and secret they can never be resumed into.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Each tab returns to its exact session with one command (Priority: P1)

The user has several terminal tabs, each in a different project's session on
a configured host (any provider, or an added `ssh` host). They close the
laptop, reopen it, and every tab is at the local prompt. In each tab they run
`remo resume` and land back inside that tab's own project session — the same
zellij session, the same devcontainer shell, scrollback and running processes
intact — without answering any prompt.

**Why this priority**: This is the whole point of the feature; it removes the
per-tab manual reconnect the user does every time the laptop sleeps.

**Independent Test**: On a configured host, open two tabs, pick project A in
one and project B in the other via `remo shell` and the host menu, kill both
SSH connections, run `remo resume` in each tab, and confirm tab 1 is in A's
live session and tab 2 in B's, with no prompt shown.

**Acceptance Scenarios**:

1. **Given** a tab that ran `remo shell`, chose project A in the host menu,
   and lost its connection, **When** the user runs `remo resume` in that tab,
   **Then** the tab is attached to A's existing live session with no host or
   project prompt.
2. **Given** two tabs attached to different projects on the same host,
   **When** each runs `remo resume`, **Then** each returns to its own project,
   never the other tab's.
3. **Given** a tab that ran `remo shell -p A`, **When** it runs `remo
   resume` after a disconnect, **Then** it returns to A's session.
4. **Given** a tab that switched projects during its session (left A for the
   menu, then chose B), **When** it resumes, **Then** it returns to B, the
   most recent attach.
5. **Given** tabs attached to projects on two different hosts, **When** each
   resumes, **Then** each reconnects to its own host and project.

---

### User Story 2 - Never worse than `remo shell` (Priority: P1)

Whenever resume cannot put the tab back exactly — the terminal exposes no tab
identifier, the tab has no record, the host has not been upgraded, the host
is unreachable, or the recorded session is no longer live — `remo resume`
behaves like `remo shell` from the furthest point it *can* reach, and says in
one line why it could not go further. It never creates a new session the user
did not ask for, and never attaches a tab to someone else's project.

**Why this priority**: A resume command that sometimes drops the user in the
wrong place, or fails where `remo shell` would have worked, is worse than no
command. Users must be able to type `remo resume` reflexively.

**Independent Test**: Exercise each missing piece in turn (unset the tab
variable; fresh tab; host without the upgrade; recorded session killed) and
confirm each ends in the same place `remo shell` would have, with an
explanatory line.

**Acceptance Scenarios**:

1. **Given** a terminal that exposes no tab identifier, **When** the user
   runs `remo resume`, **Then** it behaves exactly as `remo shell` (including
   the host picker when several hosts exist) after one line saying the
   terminal gave no tab identity.
2. **Given** a tab with no record, **When** the user runs `remo resume`,
   **Then** it behaves as `remo shell`, saying this tab has nothing to resume.
3. **Given** a tab whose recorded project's session has since exited or been
   deleted, **When** the user resumes, **Then** the tab lands in that host's
   project menu (not a freshly created session), with a line naming the
   project that is no longer running.
4. **Given** a tab whose recorded host has been removed from the registry,
   **When** the user resumes, **Then** it behaves as `remo shell`, saying the
   recorded host is no longer registered.
5. **Given** a host that has not been upgraded to support per-tab records,
   **When** a tab that last used the host menu resumes, **Then** it
   reconnects to the recorded host and shows that host's menu, and the line
   says the host needs `remo configure` / `remo <provider> upgrade` for exact
   resume.
6. **Given** a host that has not been upgraded, **When** a tab that last ran
   `remo shell -p A` resumes and A's session is live, **Then** it reattaches
   to A (the workstation remembered the project).
7. **Given** the host lookup does not answer within 5 seconds, **When** a tab
   resumes, **Then** it proceeds as for a host without the lookup.

---

### User Story 3 - Right host even before the host is upgraded (Priority: P2)

Immediately after upgrading remo on the workstation — before any host has
been reconfigured — `remo resume` already takes each tab back to the host it
was connected to, skipping the host picker. The user still picks the project
in the host menu, where live sessions are marked.

**Why this priority**: It delivers value with zero host changes and is the
mechanism User Story 2's fallback relies on, but it saves only the host prompt,
which single-host users never see.

**Independent Test**: With a workstation upgraded and a host left on the
previous release, connect a tab with `remo shell` (several hosts registered),
drop the connection, run `remo resume`, and confirm it goes straight to that
host's menu without the host picker.

**Acceptance Scenarios**:

1. **Given** several registered hosts and a tab that last ran `remo shell`
   against host H, **When** it resumes, **Then** it connects to H without
   showing the host picker.
2. **Given** an explicit host name, **When** the user runs `remo resume H`,
   **Then** it resumes this tab's record only if the record is for H, and
   otherwise behaves as `remo shell H`.

---

### User Story 4 - Documented and discoverable (Priority: P3)

`remo resume --help`, the README and the shell docs explain what is
remembered, where, which terminals are recognised, what needs a host upgrade,
and how to clear the records.

**Why this priority**: Required by the constitution (Principle VIII) and
needed for users to trust a command that remembers things, but it does not
change behaviour.

**Independent Test**: Read the help and docs and confirm each of the items
above is answered and matches behaviour.

**Acceptance Scenarios**:

1. **Given** the released feature, **When** a user reads `remo resume
   --help`, **Then** it names the recognised terminals, the fallback
   behaviour, and the host-upgrade requirement for exact resume.

### Edge Cases

- **Two laptops, same tab number.** kitty's `KITTY_WINDOW_ID=1` exists on
  every machine running kitty. Records on a shared host must never let one
  workstation's tab resume into another workstation's project.
- **Nested multiplexers.** A tab running local tmux has both a terminal tab id
  and a `TMUX_PANE`; the innermost (the tmux pane) is the one the user sees as
  "this tab" and must win.
- **The same project in two tabs.** Both tabs record the same project; both
  resume to it. That is correct, not a conflict.
- **Tab identifiers reused after a terminal restart.** A new tab may receive
  an identifier an old, closed tab once had. Resume may then offer the old
  tab's project; this must at worst land the user in a live session of a
  project they used from this workstation, never an error.
- **Hostile or malformed tab values on the host.** The host accepts a tab key
  from the network; anything not matching the expected shape is ignored and
  never used to build a file path.
- **Web console and SSM connector attaches.** These reach `project-launch`
  without any tab key; they must behave exactly as today and record nothing.
- **Inside a devcontainer or an existing zellij session.** `remo resume` run
  from inside a remote session is not meaningful; the workstation-side tab id
  is what matters and nothing on the host changes this.
- **Records growing without bound.** Old records on the workstation and on
  each host are pruned so neither grows forever.
- **The user deliberately exited to the shell.** If the user left the session
  on purpose, a later resume reattaching to it is acceptable; a session they
  killed is covered by the "no longer live" fallback.
- **Concurrent resumes.** Several tabs resuming at the same moment must not
  corrupt each other's records.

## Requirements *(mandatory)*

### Functional Requirements

**Tab identity (workstation)**

- **FR-001**: The client MUST derive a tab identity from the first set
  variable in this precedence: `TMUX_PANE`, `WEZTERM_PANE`, `KITTY_WINDOW_ID`,
  `ITERM_SESSION_ID`, `TERM_SESSION_ID`, `WT_SESSION`. The variable's *name*
  is part of the identity, so equal values from different terminals never
  collide. For tmux the identity is the socket path (the first
  comma-separated field of `$TMUX`) plus `TMUX_PANE`, so `%N` pane ids from
  different tmux servers never collide; the server pid is excluded.
- **FR-002**: The tab key that leaves the workstation MUST be a one-way digest
  of the tab identity combined with a random per-workstation secret generated
  once and stored locally; raw terminal identifiers MUST NOT be sent to any
  host. Two workstations therefore never produce the same key for "tab 1".
- **FR-003**: When no recognised variable is set, the client MUST treat the
  tab as having no identity: nothing is recorded, nothing is forwarded, and
  `remo resume` follows FR-012.

**Recording**

- **FR-004**: Every interactive connection opened by `remo shell` (with or
  without `-p`) from a tab with an identity MUST record, on the workstation,
  that tab's host and the time; a `-p` launch MUST also record the project.
- **FR-005**: The client MUST forward the tab key to the host on every
  `remo shell` connection, through a channel that a host without this feature
  silently ignores (no error, no changed behaviour).
- **FR-006**: Host configuration (`remo configure` and every provider's
  `upgrade`/`create` path, through the shared configure play) MUST make the
  host accept the forwarded tab key.
- **FR-007**: The host-side project menu and project launcher MUST record,
  per tab key, the project most recently attached and the time, each time a
  project session is attached from a connection carrying a valid tab key.
  Attaches without a key (web console, SSM connector, older clients) MUST
  record nothing and behave exactly as today.
- **FR-008**: The host MUST accept a tab key only if it matches the digest's
  exact shape; any other value MUST be ignored, and a key MUST never be used
  to form a path outside the record store.
- **FR-009**: The host MUST expose a lookup, through the existing versioned
  `remo-host` protocol and advertised in its capabilities, that returns for
  a tab key the recorded project, when it was recorded, and whether that
  project's session is currently live. Clients detect support by calling the
  lookup directly — a host without it rejects the unknown operation — so no
  separate capability round-trip is needed (SC-004); such hosts are treated
  as "not upgraded".

**Resuming**

- **FR-010**: `remo resume [NAME]` MUST, for a tab with a workstation record
  whose host is still registered (and equals NAME when given), reconnect to
  that host and choose the project to resume in this order: (a) if the host
  supports the lookup and has an entry for the tab key, that entry's
  project (the host alone sees in-session menu switches); (b) otherwise the
  workstation record's project, if it has one (from a `-p` launch), with its
  liveness taken from the existing `remo-host sessions list`. If the chosen
  project's session is live, attach to it through the same path as
  `remo shell -p <project>`, with no prompt.
- **FR-011**: If the recorded project's session is not live, `remo resume`
  MUST open the host's project menu instead (plain `remo shell` to that
  host), never create a new session for that project.
- **FR-012a**: `remo shell` MUST NOT change behaviour based on tab records:
  it records (FR-004/FR-005) but never resumes on its own. Resuming is only
  ever the explicit `remo resume` command.
- **FR-012**: In every other case — no tab identity, no workstation record,
  recorded host no longer registered, NAME not matching the record — `remo
  resume` MUST behave exactly as `remo shell [NAME]` would.
- **FR-013**: Every fallback MUST print exactly one line saying why resume
  went no further (no identity / nothing recorded / host gone / host needs
  upgrade / session no longer running), naming the remediation where one
  exists. The host-needs-upgrade line MUST name the exact command for that
  host's type (`remo configure NAME` or `remo <type> upgrade NAME`), derived
  from the descriptor/registry, not a type literal in `core/`.
- **FR-014**: `remo resume` MUST run the same pre-connect checks `remo shell`
  runs (version check and upgrade offer, SSH options, timezone forwarding),
  and accept the same connection options that apply to an interactive attach
  (e.g. port forwarding). A stopped instance the provider auto-starts on
  connect is started *before* the lookup, so the lookup reaches it at its
  current address; the start is not repeated at connect time.
- **FR-015**: The lookup round-trip MUST be bounded at 5 seconds; on
  timeout resume proceeds as for a host without the lookup (FR-010 (b), else
  the host menu). A lookup failure of any kind (timeout, unreachable,
  malformed reply) MUST degrade per FR-010 (b)/FR-011/FR-012, never abort with a traceback; a host
  that is wholly unreachable fails exactly as `remo shell` would.

**Housekeeping and safety**

- **FR-016**: Workstation records and host records MUST be pruned so they do
  not grow without bound: entries older than 30 days are discarded on write,
  and each store keeps at most 500 entries (oldest dropped first).
- **FR-017**: Workstation and host record writes MUST be atomic and safe
  under concurrent writers from several tabs.
- **FR-018**: The user MUST be able to clear workstation records:
  `remo resume --forget` clears this tab's record, `--forget-all` clears
  every record and regenerates the workstation secret, so every key a host
  may still hold becomes unreachable. Neither contacts any host; host records
  expire by pruning (FR-016).
- **FR-019**: Resume MUST work identically for every registered provider type
  and for added `ssh` hosts, with no `host.type` branching in `core/`
  (Constitution II).
- **FR-020**: Host-side changes MUST reach existing hosts only through
  `remo configure` / `remo <provider> upgrade`, and a second configure run
  MUST be a no-op for this feature (Constitution VII).

### Key Entities

- **Tab identity**: the (variable name, value) pair read from the terminal
  environment on the workstation. Never leaves the workstation.
- **Workstation secret**: a random value generated once per workstation user,
  stored locally, used to derive tab keys.
- **Tab key**: the digest of tab identity + workstation secret; the only
  tab-related value sent to hosts.
- **Workstation tab record**: tab key → host name, optional project (from
  `-p`), recorded-at time. Lives on the workstation.
- **Host tab record**: tab key → project, recorded-at time. Lives on each
  host, owned by the workspace user.
- **Resume lookup result**: project (or none), recorded-at, session live
  (yes/no), returned by the host's versioned protocol.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a laptop sleep with N tabs attached to N different
  projects on upgraded hosts, the user restores all N with exactly N commands
  and zero prompts, and 100% of tabs land in their own project's pre-existing
  session (verified by scrollback / running process presence).
- **SC-002**: In 100% of fallback cases, `remo resume` ends in the same place
  `remo shell` would have, plus one explanatory line; it never errors where
  `remo shell` would have succeeded.
- **SC-003**: No tab ever resumes into a project it did not last attach to
  from the same workstation (zero cross-tab or cross-workstation resumes
  across the test matrix, including the same-value-different-workstation
  case).
- **SC-004**: Resuming a tab to its session takes no more than one extra
  round-trip to the host compared with `remo shell -p <project>`.
- **SC-005**: Users who never run `remo resume` see no change: `remo shell`,
  the web console and the SSM connector behave identically, and a host
  without the upgrade shows no new warnings.
- **SC-006**: A resume that cannot complete the host lookup reaches its
  fallback within 5 seconds of the connection being established.

## Assumptions

- **Workstation storage**: records live under remo's existing per-user config
  home (`REMO_HOME`, default `~/.config/remo/`), next to the registry, so the
  existing `REMO_HOME` override and the web service's writable-volume model
  apply unchanged. The plan may refine the file layout.
- **Digest shape**: a hex digest (e.g. SHA-256, truncated to a fixed length
  no shorter than 32 hex characters) is sufficient as both identity and host
  input validation.
- **Forwarding channel**: SSH environment forwarding of a single remo-named
  variable (paired with the host accepting it) is the channel that a host
  without the feature silently ignores — the same pattern already used for
  the timezone variable.
- **Session liveness** is judged exactly as the host menu and `remo-host
  sessions list` judge it today (live = present and not EXITED), in the same
  pinned session namespace (#240).
- **Retention values** (30 days, 500 entries) are defaults chosen for a
  personal-workstation workload; they are not user-configurable in this
  feature.
- **Out of scope**: a client-side session picker (the host menu is the
  fallback, by explicit decision); opening one terminal tab per session
  automatically; resuming web-console or SSM-connector attaches; resurrecting
  EXITED sessions (a separate, pre-existing design question).
- **Terminal coverage** beyond the six listed variables is out of scope; an
  unrecognised terminal simply falls back (FR-003, FR-012).
