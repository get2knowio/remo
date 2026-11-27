# Feature Specification: Reach a Local Host over SSH Too

**Feature Branch**: `030-local-host-ssh`

**Created**: 2026-11-27

**Status**: Draft

**Input**: User description: "Make a machine set up with `remo local setup` reachable from another workstation as an ordinary SSH host, with the same experience either way: the same project menu, the same projects (its project index), and the same Zellij sessions, so work started at the desk can be picked up over SSH and vice versa." (Source: Notion spec prompt "Reach a local host over SSH too — the same projects at the desk or from another machine", grounded in an audit of `main` at cb5d865, 2026-11-27. Follows spec 029; prerequisite for moves started from a third machine, #277.)

## Context

Spec 029 made the machine remo runs on a Remo host for someone sitting at it.
The user needs three scenarios to work with one set of projects:

1. `remo shell` to a remote server **A** over SSH — as today.
2. Sitting at a machine **B** set up with `remo local setup` — delivered by 029.
3. `remo shell` to **B** from another machine **C**, when the user is away from B.

Scenario 3 half-works already: once B's SSH server is on and C has registered
B with `remo add`, going straight to a project (`remo shell hostb -p NAME`),
running a command in it (`--exec`), starting it in the background
(`--detach`), the version check and `remo resume`'s lookup all reach B's host
scripts, which 029 installed. Four gaps remain:

1. **No menu.** A bare `remo shell hostb` is a plain login. On remote hosts the
   menu starts from a login-shell hook that remo writes into the remote
   account's `.bashrc`; local setup deliberately never edits rc files, and a
   Mac user's login shell is zsh anyway.
2. **Possibly two sets of sessions.** On macOS the terminal-session manager
   (Zellij) keeps its sessions in a directory derived from the login
   environment. A desktop login and an SSH login may derive different
   directories, in which case a session started at the desk is invisible over
   SSH and "attach to project X" silently starts a second, separate session.
   This has not been verified on a real Mac; the feature must hold either way.
3. **No per-tab resume from C.** `remo resume` identifies the terminal tab by
   passing a key through SSH, which B's SSH server accepts only if configured
   to. Remote hosts get that during configuration; local setup never touches
   the SSH server, because doing so needs administrator rights.
4. **Wrong advice for upgrades.** From C, B looks like any SSH host, so remo's
   upgrade offer says `remo configure hostb`, and `remo configure hostb` stops
   on macOS with a safe but unhelpful "only apt-based hosts are supported".

This feature closes all four, so B is the same host whichever way the user
reaches it.

## Clarifications

### Session 2026-11-27

Run unattended (speckit-next): each answer is the recommended option, accepted
on the user's behalf.

- Q: How does remo decide a host supports the explicit menu start, and may that cost an extra connection? → A: From the pre-connect check remo already makes (the version-marker read), extended to also report whether the host's scripts can start the menu — no additional SSH round trip. A missing answer, a failed check or a timeout means "not supported" and today's plain login.
- Q: Where does the pinned macOS session directory live? → A: Under the user's own state directory in their home folder (beside remo's other host state), not under a temporary directory, so it is identical for desktop and SSH logins and survives logout; it is created 0700 on first use and refused if it exists with the wrong owner or wider permissions.
- Q: What happens to sessions started before the pin? → A: They keep running under the old location and are not listed by the menu; the upgrade (local setup) prints, once, how many such sessions it found and the exact command to attach to them, and docs/local.md carries the same note. No attempt is made to move live sessions.
- Q: What is the command surface for managing a host's index from another workstation? → A: A new `remo projects` group — `remo projects list HOST`, `remo projects add HOST PATH [--name NAME]`, `remo projects remove HOST NAME` — usable against any host, including the local one; `remo local add/remove/list` stay as the local spellings. PATH is a path on that host.
- Q: What are the resume opt-in's name and platform scope? → A: `remo local setup --allow-ssh-resume` / `--no-allow-ssh-resume`, supported on macOS and Linux wherever the system SSH server's main configuration includes a drop-in directory; elsewhere (or non-interactive, or declined) setup succeeds and prints the exact line to add by hand.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The same project menu from another machine (Priority: P1)

The developer is away from their Mac (B) and on their laptop (C), where B is
registered as an SSH host. They run `remo shell hostb` and get B's project
menu — the same projects they see at the desk — rather than a bare prompt.
Choosing a project lands them in its session inside its devcontainer.

**Why this priority**: The menu is the front door of remo. Without it,
scenario 3 requires remembering project names and typing `-p` every time.

**Independent Test**: On C, with B (set up by `remo local setup`, SSH server
on) registered via `remo add`, run `remo shell hostb` and confirm the project
menu opens listing B's index; run the same against a host whose scripts
predate this feature and confirm it still gets a plain login.

**Acceptance Scenarios**:

1. **Given** B's host scripts support it, **When** the user runs `remo shell hostb` from C, **Then** B's project menu opens, listing exactly the projects B's index lists at the desk.
2. **Given** a remote Linux host whose scripts support it, **When** the user runs a bare `remo shell` to it, **Then** the menu opens directly, and the existing login-shell hook does not start a second menu.
3. **Given** a host whose scripts predate this feature, **When** the user runs a bare `remo shell` to it, **Then** the connection is exactly what it is today (a plain login, where any existing hook behaves as before).
4. **Given** the user quits the menu over SSH, **When** the menu exits, **Then** they get the same outcome they get when quitting the menu today on that host (no hung connection, no error).
5. **Given** any other kind of connection (`-p`, `--exec`, `--detach`, tunnels), **When** it is made to any host, **Then** what remo sends over SSH is byte-for-byte unchanged.

---

### User Story 2 - One set of sessions, whichever way you reach the machine (Priority: P1)

The developer starts project X at the desk, walks away, and from C opens B's
menu: X shows as running, and choosing it attaches to that very session —
same panes, same scrollback. They detach, return to the desk, and
`remo shell local -p X` takes them back into the same session again.

**Why this priority**: Without a single session namespace, scenario 3 silently
splits the user's work into two copies of every project session — the most
confusing failure this feature can have.

**Independent Test**: On B, start project X locally; from C (over SSH) list
B's sessions and confirm X is live; attach from C and confirm it is the same
session (e.g. a marker typed at the desk is visible); detach and re-attach
locally.

**Acceptance Scenarios**:

1. **Given** a session for project X started at the desk, **When** B's sessions are listed over SSH from C, **Then** X is reported as running.
2. **Given** that session, **When** the user attaches to X from C (menu or `-p X`), **Then** they join the existing session and no second session for X is created.
3. **Given** a session started over SSH from C, **When** the user later opens X at the desk, **Then** they join that same session.
4. **Given** every way B's scripts reach sessions (menu, direct launch, background launch, session list, lookup, attach), **When** each is used locally or over SSH, **Then** all of them see the same set of sessions.
5. **Given** a Linux host, **When** any of the above runs, **Then** where sessions are kept is exactly as today.
6. **Given** sessions that were started before this feature was installed on B, **When** the user upgrades B, **Then** those sessions keep running and stay reachable until they end, or remo tells the user plainly how to reach them.

---

### User Story 3 - B knows it is a local host, and says so (Priority: P2)

From C, the developer is offered an upgrade for B, or tries
`remo configure hostb`. Instead of being pointed at a configuration path that
cannot work on a Mac, remo tells them B is managed by `remo local setup` and
that is the command to run on B.

**Why this priority**: The current behaviour is safe (nothing is changed) but
leaves the user stuck; this is about giving correct advice.

**Independent Test**: From C, run `remo configure hostb` against a B set up by
local setup and confirm it refuses before changing anything, naming
`remo local setup` on B; make B's scripts older than C's remo and confirm
`remo shell hostb`'s upgrade offer names `remo local setup` (run on B).

**Acceptance Scenarios**:

1. **Given** B was set up by `remo local setup`, **When** the user runs `remo configure hostb` from C, **Then** it refuses before any change is attempted, with a message saying to run `remo local setup` on that machine.
2. **Given** B's scripts are older than C's remo, **When** `remo shell hostb` or `remo resume` offers an upgrade, **Then** the offer names `remo local setup` run on B, and does not offer to run anything from C.
3. **Given** B's host capabilities are queried, **When** the answer is returned, **Then** it states that B is a locally managed host, and every existing field is unchanged.
4. **Given** an ordinary remote host added with `remo add`, **When** any of the above runs, **Then** behaviour and wording are exactly as today.
5. **Given** a B whose host-side record is missing or unreadable (e.g. set up by an earlier remo), **When** it is reached over SSH, **Then** remo behaves as it does today for an SSH host, without errors.

---

### User Story 4 - Opt in to per-tab resume from other machines (Priority: P2)

The developer wants `remo resume` in a terminal tab on C to put that tab back
into the project it last had open on B. They run local setup on B with an
explicit opt-in; remo explains that this is the one step that changes the
system's SSH configuration and needs administrator rights, and asks before
doing it. They can undo it the same way.

**Why this priority**: Resume across machines already degrades gracefully (to
the last project remembered on C, or the menu); this makes it exact for users
who want it.

**Independent Test**: Run local setup with the opt-in, confirm the prompt,
confirm the SSH server accepts the tab key afterwards, re-run and confirm
nothing changes, run with the opt-out and confirm the change is removed — all
exercised in tests without real administrator rights.

**Acceptance Scenarios**:

1. **Given** the user runs local setup with the opt-in, **When** the change is not yet in place, **Then** remo explains what it will change and why administrator rights are needed, and asks before using them.
2. **Given** the user declines at that prompt, **When** setup finishes, **Then** nothing in the system's SSH configuration has changed and the rest of setup has completed normally.
3. **Given** the opt-in change is already in place, **When** local setup runs again with the opt-in, **Then** nothing is changed and no administrator rights are requested.
4. **Given** the change is in place, **When** the user runs local setup with the opt-out, **Then** the change is removed (after the same explanation and confirmation) and the SSH server stops accepting the tab key.
5. **Given** local setup runs without either flag, **When** it completes, **Then** the SSH configuration is never read for changes, written or touched, and no administrator rights are requested — exactly as in spec 029.
6. **Given** the opt-in is not in place, **When** `remo resume` runs on C against B, **Then** it degrades exactly as it does today against a host that does not accept the tab key.

---

### User Story 5 - Manage B's projects from either side (Priority: P3)

A project the developer adds at the desk shows up when they reach B from C,
and vice versa. From C they can also list, add and remove B's projects without
first logging in to B.

**Why this priority**: The shared index already makes projects visible from
both sides; remote management is a convenience on top.

**Independent Test**: Add a project at the desk, list B's projects from C and
see it; add one from C, see it in the desk menu; remove one from C and confirm
its files on B are untouched.

**Acceptance Scenarios**:

1. **Given** a project added at the desk with `remo local add`, **When** the user lists or opens B's menu from C, **Then** the project is there.
2. **Given** the user adds an existing checkout on B from C, **When** the add succeeds, **Then** the desk menu lists it too, under the same name.
3. **Given** the user removes a project from C, **When** the removal succeeds, **Then** it is gone from both sides and its files are untouched.
4. **Given** any host (remote Linux or local-managed), **When** these commands are used against it, **Then** they enforce the same naming, collision and missing-path rules as `remo local add/remove/list` do today.
5. **Given** a host whose scripts lack the project-index commands, **When** the user tries to manage its projects from C, **Then** remo refuses with a message naming the upgrade command for that host.

### Edge Cases

- B's SSH server is off: `remo shell hostb` fails as any unreachable SSH host does today; the docs say how to turn it on.
- B is reached over SSH while nobody is logged in at the desk, and after the desk user logs out and back in: sessions that the session manager itself kept alive are still listed and attachable.
- The capability check used to decide whether to start the menu fails or times out: remo falls back to today's plain login rather than failing the connection.
- A remote Linux host has the new scripts and the old `.bashrc` hook: exactly one menu starts.
- The session directory remo pins on macOS is missing, has the wrong owner, or is world-readable: remo recreates or refuses to use it with a clear message, never silently sharing sessions with another user.
- An SSH login on the Mac has a minimal search path that lacks Homebrew's directories: the menu, direct launches and `--exec` still find Zellij, fzf, the devcontainer runtime and the container engine.
- The macOS temporary directory differs between a desktop login and an SSH login (or is unset over SSH): sessions are still shared.
- The opt-in is requested on Linux, or the system's SSH server is not the one remo knows how to configure: remo explains what it would add and how to add it by hand, and the rest of setup succeeds.
- The opt-in is requested in a non-interactive run: remo does not use administrator rights without being able to ask; it reports how to complete the step.
- B is registered on C under a name that differs from B's own local host name: nothing depends on the two names matching.
- The same machine is registered on C twice (as `local` on B itself is never pushed, so only via `remo add`): each entry behaves independently.

## Requirements *(mandatory)*

### Functional Requirements

**Menu without login-shell hooks**

- **FR-001**: A bare `remo shell` to an SSH-reached host whose host scripts advertise support MUST start the project menu explicitly as the remote command, rather than relying on a login-shell hook.
- **FR-002**: Support MUST be detected from the pre-connect check remo already makes (no additional SSH round trip), extended to report whether the host's scripts can start the menu; a host that predates this feature, or whose check fails or times out, MUST get exactly today's connection.
- **FR-003**: Starting the menu explicitly MUST NOT cause a second menu to start through the existing `.bashrc` hook on remote hosts; that hook stays in place for older clients.
- **FR-004**: Every SSH command other than the bare-menu case MUST remain byte-identical to today, pinned by tests before and after the change.

**One session namespace**

- **FR-005**: On macOS, every host-script path that creates, lists, looks up or attaches sessions MUST use one stable, per-user session directory, so local and SSH access see the same sessions.
- **FR-006**: That directory MUST live under the user's own state directory in their home folder (not a temporary directory), MUST be private to the user (mode 0700, owned by the user) and MUST remain valid across the user logging out and back in for as long as the session manager's own sessions survive; remo MUST refuse to use it, with a clear message, if it is not private.
- **FR-007**: On Linux the session directory behaviour MUST be exactly as today.
- **FR-008**: Sessions created before the pin MUST keep running (they are not moved and not listed by the menu); local setup MUST report, once, how many it found and the exact command to attach to them, and the docs MUST carry the same note.

- **FR-008a**: On a locally managed host, host scripts reached over SSH MUST find the same tools (session manager, menu picker, devcontainer runtime, container engine) as they do at the desk, even though an SSH login's search path usually lacks the package manager's directories; this MUST NOT require editing the user's shell rc files.

**Host-side record of a locally managed host**

- **FR-009**: Local setup MUST record, on the host, that the machine is managed by `remo local setup` and which remo version installed its scripts; re-running setup MUST keep that record current and be a no-op when nothing changed.
- **FR-010**: The host's capabilities report MUST state the host kind as an additive field; the protocol version and every existing field MUST be unchanged, and hosts without the record MUST report the remote kind (or omit the field) so older and remote hosts read as today.
- **FR-011**: `remo configure` against an SSH-reached host that reports itself as locally managed MUST refuse before running any configuration step, with a message telling the user to run `remo local setup` on that machine.
- **FR-012**: The upgrade offer in `remo shell` and `remo resume` for such a host MUST name `remo local setup` (to be run on that machine) and MUST NOT offer to run a configuration from the connecting workstation.
- **FR-013**: When the host-side record cannot be read, remo MUST behave as for an ordinary SSH host, without error.

**Opt-in resume across machines**

- **FR-014**: Local setup MUST offer an explicit opt-in (`--allow-ssh-resume`) that makes the machine's SSH server accept the tab key `remo resume` sends, and an explicit opt-out (`--no-allow-ssh-resume`) that removes it; both are supported on macOS and Linux wherever the system SSH server's main configuration includes a drop-in directory.
- **FR-015**: The opt-in and opt-out are the only steps in local setup that use administrator rights; each MUST explain what it changes and why, and ask before using them. Without either flag, local setup MUST NOT read for change, write or restart anything in the SSH server's configuration.
- **FR-016**: The opt-in MUST be idempotent (a second run changes nothing and requests no rights), the opt-out MUST fully reverse it, and both MUST validate the SSH server configuration before reloading it, leaving it unchanged on failure.
- **FR-017**: When the opt-in cannot be applied (unsupported platform or SSH server, non-interactive run, declined prompt), the rest of setup MUST still succeed and remo MUST print how to apply the change by hand.
- **FR-018**: Without the opt-in, `remo resume` against the host MUST degrade exactly as today.

**Same index from both sides**

- **FR-019**: Projects added or removed at the desk and over SSH MUST share one index; nothing in this feature introduces a second copy.
- **FR-020**: remo MUST offer `remo projects list HOST`, `remo projects add HOST PATH [--name NAME]` and `remo projects remove HOST NAME` to list, add (an existing checkout on that host, in place) and remove projects on any host, local or SSH-reached, with the same validation, collision and missing-path behaviour as `remo local add/remove/list`; removal never touches files.
- **FR-021**: Against a host whose scripts lack the project-index commands, those commands MUST refuse with the host's upgrade command.

**Docs and gates**

- **FR-022**: `docs/local.md` MUST gain a "Reach this machine from another one" section: turning on Remote Login, registering with `remo add`, the resume opt-in, what to expect, and the pre-existing-sessions note.
- **FR-023**: Tests MUST cover: the bare-menu command on both sides of the support gate and on check failure; byte-identity of every other SSH command; the session-directory pin on macOS and its absence on Linux (including the not-private refusal); the configure refusal and upgrade wording for a locally managed host; the opt-in add / re-run no-op / remove / decline / non-interactive paths without real administrator rights; and remote project-index management including its refusal path. The macOS CI job MUST run the new host-script tests.

### Key Entities

- **Local-management record**: a host-side marker that the machine is managed by `remo local setup`, with the remo version that installed its scripts. Read by the host's capabilities report.
- **Host kind**: an additive capability value — locally managed vs remote — that the connecting workstation uses to choose advice and refusals.
- **Session directory**: the one per-user location where the session manager keeps sessions on macOS; private, stable across logins.
- **Resume opt-in**: a single, reversible SSH-server configuration change that accepts the tab key.
- **Project index**: unchanged from spec 029; now also managed remotely.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From a second machine, a bare `remo shell` to a locally managed Mac opens its project menu in one step, with the same project list as at the desk (0 differences).
- **SC-002**: A session started at the desk is attached, not duplicated, from the second machine in 100% of attempts, and vice versa.
- **SC-003**: 0 changes to any SSH command remo sends, other than the bare-menu case, verified by tests across every host type.
- **SC-004**: Every configure or upgrade path against a locally managed host reached over SSH names `remo local setup`; 0 paths suggest `remo configure` for it.
- **SC-005**: Local setup without the opt-in requests administrator rights 0 times; with the opt-in it requests them at most once per run, and 0 times on a re-run.
- **SC-006**: Existing remote Linux hosts show no behaviour change other than the menu now starting directly, verified by the existing suite plus the new pins.

## Assumptions

- The user turns on the Mac's SSH server (Remote Login) themselves; remo documents but never automates it.
- B is registered on C with the existing `remo add` (the `ssh` pseudo-type); no registry schema change and no new host type are needed — what B is comes from the host itself.
- The connecting workstation runs a remo that includes this feature; an older client simply keeps today's behaviour.
- "Locally managed" is determined host-side, so it is correct regardless of what C's registry says.
- The SSH-server change for resume is made with the same kind of configuration drop-in remote hosts already use.
- Out of scope: moving projects between hosts (#277), the web console for local hosts (#276), and configuring Remote Login.
