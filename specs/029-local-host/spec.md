# Feature Specification: Local Machine as a Remo Host

**Feature Branch**: `029-local-host`

**Created**: 2026-11-26

**Status**: Draft

**Input**: User description: "Let a user install the remo CLI on their own machine (macOS first, Linux workstations too) and use it to reach projects and devcontainers on that same machine, with the same commands and the same experience as a remote Remo host: `remo shell` opens the project menu, choosing a project attaches its Zellij session inside its devcontainer, and `remo shell -p NAME`, `--exec`, `--detach` and `remo resume` all behave as they do against a remote host. No VM, no SSH server, no second user account. Today the only way to use Remo's project menu and devcontainer sessions on a Mac is to run a Linux VM on the Mac, configure it as a Remo host, and `remo shell` into it over SSH; the goal is to drop the VM. Projects on a laptop live in several folders, so a host keeps a per-host project index (name → path, existing checkouts added in place) instead of scanning one projects directory. A later feature adds project migration between hosts; this spec keeps that possible but does not build it." (Source: Notion spec prompt "Local machine as a Remo host", grounded in an audit of `main` at 6a7d86a.)

## Context

Remo's whole session experience lives on the *host*: a set of host scripts
(`remo-host`, `project-menu`, `project-launch`, `devshell`) lists the user's
projects, starts each project's devcontainer, and keeps one terminal session
per project alive across disconnects. The workstation's `remo` CLI only
reaches those scripts, and today it can reach them only one way — over SSH,
into a dedicated Linux account that remo itself configured.

That leaves a Mac user who wants the same experience for projects on the Mac
itself with one option: run a Linux VM on the Mac, configure the VM as a
Remo host, keep the projects inside the VM, and `remo shell` into it. The VM
exists only so remo has something to SSH into. Four things stand between
remo and the machine it is installed on:

1. **No transport except SSH.** Opening the menu, launching a project, the
   version check, and `remo resume`'s lookup all build an `ssh` command.
2. **Host setup assumes a dedicated Linux server** — it creates a separate
   user, mounts its home directory, installs system packages and rewrites
   SSH configuration. None of that is acceptable on someone's own laptop.
3. **The host scripts assume Linux and bash 4**, and the two hooks that
   start the menu and the devcontainer live in the remote account's
   `.bashrc`, which a Mac user's zsh never reads.
4. **A project is "a directory directly under one projects folder"**, named
   by that directory. On a laptop, checkouts already live wherever the user
   keeps them, names can collide, and not every folder is a project.

This feature makes the machine remo runs on a first-class Remo host, so the
VM can go.

## Clarifications

### Session 2026-11-26

Run unattended (speckit-next): each answer is the recommended option, accepted
on the user's behalf.

- Q: Should the host scripts be rewritten to run on macOS's stock bash 3.2, or require a newer bash? → A: Require bash ≥ 4 as a detected, required prerequisite (Homebrew's bash on macOS). On the local host remo runs the scripts with the bash it detected rather than relying on the scripts' shebang; on remote hosts behaviour is unchanged. Porting ~1,900 lines off associative arrays and `mapfile` would risk every remote host for no user benefit, and Homebrew is already assumed for Zellij and fzf.
- Q: Which prerequisites are required and which optional? → A: Required: bash ≥ 4, git, Zellij, fzf (the menu refuses to run without it), a devcontainer runtime, and an installed container engine. Optional: the GitHub CLI (cloning falls back without it). A container engine that is installed but not running is a warning at setup and a clear, actionable error when a project is opened.
- Q: What is the command surface and the default local host name? → A: A `remo local` command group with `setup`, `check`, `add`, `remove` and `list`; the local host's default name is `local` (so `remo shell local -p NAME` works out of the box), overridable at setup.
- Q: Does the local host scan a projects folder by default, and where do clones go? → A: No projects folder by default, so nothing is scanned implicitly; if the user sets one at setup, its direct subfolders count as projects exactly as on a remote host. Clones made on the local host go to the projects folder if set, otherwise to `~/projects` (created on demand), and are always recorded in the index.
- Q: How does the local host interact with `remo web push` / `remo web sync`? → A: It never leaves the machine: local host entries are excluded from every push payload and sync merge, and a local entry received from a deployment is ignored. A deployed console cannot reach another machine's local host, and another workstation would misread the entry as itself.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Work on this machine's projects with `remo shell` (Priority: P1)

A developer installs remo on their Mac, runs a one-time local setup, adds
the project checkouts they already have (wherever they live), and from then
on `remo shell` opens the same project menu they know from their remote
hosts — listing the Mac's projects. Choosing a project starts its
devcontainer and drops them into that project's terminal session, exactly as
on a remote host. No VM is running and no SSH server is enabled.

**Why this priority**: This is the whole point — it is what lets the user
delete the VM. Without it nothing else in this feature has value.

**Independent Test**: On a Mac with a container engine, a devcontainer
runtime and Zellij installed: install remo, run local setup, add two
checkouts from two different folders, run `remo shell`, open one project,
and confirm the terminal is inside that project's devcontainer — with the
Mac's SSH server off throughout.

**Acceptance Scenarios**:

1. **Given** remo is installed and local setup has completed, **When** the user runs `remo shell` and picks the local host (or it is the only host), **Then** the project menu opens listing every project in the local project index.
2. **Given** a project in the menu, **When** the user selects it, **Then** its devcontainer starts (or is reused) and the user lands in that project's terminal session inside the devcontainer.
3. **Given** the project's session is open, **When** the user detaches and later runs `remo shell` again and selects the same project, **Then** they rejoin the same, still-running session with their work intact.
4. **Given** the Mac's SSH server is disabled, **When** the user does any of the above, **Then** every step succeeds.
5. **Given** the registry also contains remote hosts, **When** the user runs `remo shell` with no name, **Then** the picker lists the local host alongside the remote ones, and choosing a remote host behaves exactly as before this feature.

---

### User Story 2 - Add existing checkouts in place, from any folder (Priority: P1)

The developer's projects already live in `~/code/a`, `~/work/b` and so on.
They register each one with remo without moving or copying anything. Each
project gets a name (by default its folder's name) that is the label in the
menu, the name of its terminal session, and what they type after
`remo shell -p`. They can unregister a project without its files being
touched, and remo tells them clearly when a registered checkout has gone
missing.

**Why this priority**: Without it, User Story 1 only works if the user moves
every checkout into one folder — the exact rearrangement they want to avoid.
It ships together with Story 1 as the MVP.

**Independent Test**: Add `~/code/a` and `~/work/b`; confirm both appear in
the menu under their folder names; try adding a second folder also named
`a` and confirm it is refused with a hint to choose another name; remove
`b` and confirm its folder is untouched; move `a`'s folder away and confirm
the menu shows it as missing with how to fix it.

**Acceptance Scenarios**:

1. **Given** a checkout at any path, **When** the user adds it, **Then** it is registered in place under its folder's name and nothing on disk is moved or copied.
2. **Given** the user supplies a different name when adding, **When** the add succeeds, **Then** that name is used everywhere the project is shown or addressed.
3. **Given** a registered project named `a`, **When** the user adds another checkout that would also be named `a`, **Then** the add is refused with a message naming the existing project and suggesting an explicit name.
4. **Given** a name that is not a valid project name, **When** the user adds with it, **Then** the add is refused with the same validation message remo already uses for project names.
5. **Given** a registered project, **When** the user removes it, **Then** it disappears from the menu and its folder and files are untouched.
6. **Given** a registered project whose folder no longer exists, **When** the menu or any listing is shown, **Then** the project appears marked as missing with the command to fix it (re-add at the new path, or remove), and is never silently dropped.
7. **Given** a remote host configured before this feature, **When** it is upgraded, **Then** every project directory under its projects folder still appears exactly as before, with no step required from the user.

---

### User Story 3 - The rest of `remo shell` and `remo resume` work locally (Priority: P2)

Everything the developer does against a remote host works against the local
host: `remo shell local -p NAME` goes straight to a project,
`--exec "git status"` runs a command inside its devcontainer, `--detach`
starts it in the background, and after closing a terminal tab,
`remo resume` in that tab puts it back in the project it was in. Commands
that have no meaning on the machine itself — copying files to it, port
tunnels to it, the remote configure step — say so plainly and point at the
right thing to do.

**Why this priority**: Story 1 gives the menu; this story gives the
shortcuts and the per-tab resume that make daily use match the remote
experience. It is valuable but can follow the MVP.

**Independent Test**: Against the local host: `remo shell local -p a`
attaches `a`; `--exec "git status"` prints `a`'s status from inside its
devcontainer; `--detach` returns immediately with `a`'s session running;
in a terminal tab attached to `a`, close the session, run `remo resume`,
and land back in `a`. Then run `remo cp`, `remo shell local -L 8080:8080`
and `remo configure local` and confirm each refuses with an actionable
message.

**Acceptance Scenarios**:

1. **Given** the local host, **When** the user runs `remo shell <local> -p NAME`, **Then** they attach NAME's session directly, as on a remote host.
2. **Given** `-p NAME --exec CMD`, **When** run against the local host, **Then** CMD runs inside NAME's devcontainer and its output and exit status reach the user.
3. **Given** `-p NAME --detach`, **When** run against the local host, **Then** remo returns once NAME's session is running in the background.
4. **Given** a terminal tab last attached to project NAME on the local host, **When** the user runs `remo resume` in that tab, **Then** they are back in NAME's session with no prompt — the same outcome as on a remote host.
5. **Given** the local host, **When** the user runs `remo cp`, uses `-L` tunnels, or runs the remote configure command for it, **Then** remo refuses with a message saying why the command does not apply to the local machine and what to use instead, and exits non-zero.

---

### User Story 4 - Local setup is safe to run on your own computer (Priority: P2)

The developer runs local setup on their everyday machine. It installs only
remo's own scripts into their home directory, as them — no administrator
password, no new accounts, no changes to their shell configuration, no
system packages. Before and during setup it checks what remo needs (a
container engine, a devcontainer runtime, Zellij, fzf, git, a suitable
shell) and, for anything missing, prints the exact command to install it on
their platform. Running setup again changes nothing; running it after
upgrading remo brings the scripts up to date, and `remo shell` offers that
update the same way it offers to upgrade a remote host.

**Why this priority**: Users will not run something on their own laptop that
might rewrite their dotfiles or ask for a sudo password. Trust in setup is a
precondition for adoption, but the detection details can be refined after
the MVP.

**Independent Test**: Snapshot the user's shell rc files and system
directories; run local setup on a machine missing fzf; confirm the only
files written are remo's own under the user's home, that no privilege
escalation was attempted, that the rc files are byte-identical, and that
fzf is reported with its install command. Run setup a second time and
confirm nothing changed.

**Acceptance Scenarios**:

1. **Given** a machine with all prerequisites, **When** the user runs local setup, **Then** it completes as the invoking user, writes only remo's own files under the user's home, and registers the local host.
2. **Given** local setup has already run, **When** it runs again with no remo upgrade in between, **Then** it reports nothing to do and changes no file.
3. **Given** a required prerequisite is missing (e.g. no container engine), **When** setup runs, **Then** it fails listing each missing requirement with the platform's install command, and registers nothing.
4. **Given** only an optional prerequisite is missing, **When** setup runs, **Then** it completes and warns, naming the tool and its install command.
5. **Given** the user later upgrades remo, **When** they next run `remo shell` against the local host, **Then** remo notices the installed scripts are older than itself and offers to update them, as it does for remote hosts.
6. **Given** any run of local setup, **Then** no administrator privilege is requested, no user account is created, and no shell startup file is modified.
7. **Given** a separate check command, **When** the user runs it at any time, **Then** it reports each prerequisite as present or missing (with install command) and the state of the local host, without changing anything.

---

### Edge Cases

- **Local setup run twice with different names, or on two accounts.** At most one local-host entry per machine and user: a second setup with a different name renames or refuses clearly; it never creates a second local host.
- **Name collision between a local project and a remote project.** Names are unique per host only; the same name on two hosts is normal (the user addresses the host first).
- **A projects folder is configured and contains a folder with the same name as an explicitly added project.** The explicit entry wins; the clash is reported in listings so the user can rename one, and neither is silently hidden.
- **A registered path is a symlink, or two names point at the same path.** Paths are stored as given and resolved when used; adding a second name for an already-registered path is refused with the existing name.
- **The project index file is missing, empty or malformed.** Missing or empty means "no explicitly added projects" (projects-folder entries still appear); malformed is reported with the file's location and nothing is overwritten until the user fixes or removes it.
- **The container engine is installed but not running.** Setup and check report it as present but not running, with how to start it; opening a project reports the same instead of a raw engine error.
- **The local host entry exists but the scripts were never installed or were deleted.** `remo shell` to it says local setup has not completed and names the command, instead of failing with "command not found".
- **A remote-host registry entry written by this version is read by an older remo, or the local entry is read by an older remo.** The older remo preserves the entry unchanged and warns it does not know the type (existing behaviour for unknown types).
- **The user's login shell is zsh, fish or anything else.** Nothing about local use depends on which shell the user logs in with.
- **macOS's stock bash 3.2 is first on the path.** remo still runs the host scripts with the bash ≥ 4 it detected; if none is installed, setup lists it as a missing required prerequisite with its install command.
- **A web deployment is in use.** `remo web push`/`sync` neither send the local host nor accept one from the deployment; remote hosts sync exactly as before.
- **The machine is Linux, not macOS.** The same commands work on a Linux workstation, with install hints for its package manager.
- **Windows / WSL.** Unchanged: the existing platform gate still applies.
- **`--exec` command fails inside the devcontainer.** Its non-zero exit status is returned to the user, as on a remote host.

## Requirements *(mandatory)*

### Functional Requirements

**Local host**

- **FR-001**: Users MUST be able to register the machine remo runs on as a host with a one-time `remo local setup`, choosing its name (default `local`) and optionally a projects folder. The `remo local` command group MUST provide `setup`, `check`, `add`, `remove` and `list`.
- **FR-002**: The local host MUST appear wherever registered hosts appear — the `remo shell` picker, `remo shell NAME`, `remo resume NAME`, and any existing host listing or host-name completion — and MUST NOT offer provider lifecycle commands (create, destroy, resize, snapshot) that do not apply to it. `remo remove NAME` MUST deregister it (leaving its scripts and project index in place).
- **FR-003**: There MUST be at most one local host entry per machine and user; repeating setup MUST update that entry rather than create another.
- **FR-004**: The local host entry MUST be stored in the existing registry format, and an older remo reading it MUST preserve it unchanged (existing unknown-type behaviour).
- **FR-004a**: The local host entry MUST never leave the machine: it MUST be excluded from every `remo web push` payload and `remo web sync` merge, and a local host entry received from a deployment MUST be ignored.

**Reaching the host without SSH**

- **FR-005**: remo MUST reach the local host's scripts directly, as processes of the user's own session with the user's real terminal, without SSH and without any SSH server running.
- **FR-006**: Every operation that reaches a host — opening the project menu, `-p` launch, `--exec`, `--detach`, the remote-version check and upgrade offer, and `remo resume`'s lookup and per-tab recording — MUST go through one host-access abstraction that has an SSH implementation and a local implementation.
- **FR-007**: For every existing host type, the commands remo runs over SSH MUST be byte-identical to those before this feature.
- **FR-008**: The per-tab identifier used by `remo resume` MUST reach the local host's scripts, so per-tab resume behaves locally exactly as it does remotely.
- **FR-009**: `remo cp`, `-L` port tunnels and the remote configure command MUST, when aimed at the local host, refuse with a message that says why they do not apply and what to use instead, and exit non-zero.
- **FR-010**: The host-access abstraction MUST be expressed so that a future non-SSH host type could supply its own implementation; this feature MUST NOT build any implementation other than SSH and local.

**Local setup**

- **FR-011**: Local setup MUST install or update only remo's own files under the user's home directory: the host scripts, the version marker and remo's per-tab state directory.
- **FR-012**: Local setup MUST run entirely as the invoking user, MUST NOT request administrator privileges, MUST NOT create or modify user accounts, and MUST NOT modify the user's shell startup files.
- **FR-013**: Local setup MUST be idempotent: a second run with no remo upgrade in between MUST change nothing and say so.
- **FR-014**: The host scripts installed locally MUST come from the same source as those installed on remote hosts, so the two cannot drift apart.
- **FR-015**: After a remo upgrade, `remo shell` to the local host MUST detect that the installed scripts are older than remo and offer to update them, consistent with the existing upgrade offer for remote hosts.

**Prerequisites**

- **FR-016**: remo MUST NOT install system software on the local machine. `remo local setup` and the read-only `remo local check` MUST detect each prerequisite: a container engine (and whether it is running), a devcontainer runtime (by the same precedence remo already uses to choose one), Zellij, fzf, git, bash ≥ 4, and the GitHub CLI.
- **FR-017**: For each missing prerequisite, remo MUST print the exact install command for the current platform (Homebrew on macOS; the distribution's package manager on Linux).
- **FR-018**: Required prerequisites are bash ≥ 4, git, Zellij, fzf, a devcontainer runtime and an installed container engine; the GitHub CLI is optional. Setup MUST fail, registering nothing, when a required prerequisite is missing, and MUST complete with a warning when only an optional one is missing or the container engine is installed but not running. Opening a project while the engine is not running MUST fail with a message saying how to start it. The split MUST be documented.

**Host scripts on macOS**

- **FR-019**: The host scripts MUST run correctly on macOS as well as Linux, with no change in behaviour on existing remote hosts. They MAY require bash ≥ 4; on the local host remo MUST run them with the bash ≥ 4 it detected, never with an older system bash that happens to be first on the path.
- **FR-020**: No part of starting the project menu or starting a project's devcontainer inside its session MAY depend on the user's login shell or its startup files; remote hosts MUST keep working, and if their mechanism changes, an existing remote host MUST move to the new one on its next upgrade/configure without user action.
- **FR-021**: The host's machine-readable protocol MUST stay at its current version with an unchanged contract. Host statistics on macOS MUST report real values where available and report the rest as unavailable through the existing contract; they MUST never fail.

**Project index**

- **FR-022**: Each host MUST keep a project index mapping project name → absolute path, owned by the host scripts and stored in the user's state directory. Every consumer that lists projects or resolves a project name to a path MUST use it.
- **FR-023**: Users MUST be able to add an existing checkout to the index in place, from the workstation CLI for the local host, without anything being moved or copied; the name defaults to the folder name and may be given explicitly.
- **FR-024**: A project name MUST pass remo's existing project-name validation and MUST be unique per host; an add that would collide MUST be refused naming the existing project and suggesting an explicit name.
- **FR-025**: Users MUST be able to remove a project from the index without its files being touched; deleting a project (existing behaviour) MUST also drop its index record.
- **FR-026**: Cloning a project on a host (existing behaviour) MUST record it in the index. On the local host a clone goes to the projects folder if one is set, otherwise to `~/projects` (created on demand).
- **FR-027**: Every direct subfolder of the host's projects folder MUST remain a project implicitly, so existing remote hosts list exactly the same projects with no migration step. The local host has no projects folder unless the user sets one, so by default nothing is scanned there and only explicitly added or cloned projects appear.
- **FR-028**: An index record whose path no longer exists MUST be shown as missing, with the command to fix it, and MUST never be silently dropped.
- **FR-029**: The project name MUST be the project's identity everywhere: menu label, terminal session name, per-tab resume record and the `-p` argument.

**Portability (for future migration)**

- **FR-030**: Host-specific facts about a project (its absolute path, any host identity) MUST live only in that host's index, never inside the project's own files, so a future feature can move a project to another host by recreating it there under the same name.

**Documentation and verification**

- **FR-031**: The project MUST document local use: installing, local setup, adding existing checkouts, prerequisites per platform, how local differs from a remote host, and how to retire a VM that only existed to run `remo shell`.
- **FR-032**: The host scripts' macOS behaviour MUST be verified automatically on a macOS environment, in addition to the existing Linux verification.

### Key Entities

- **Local host**: A registry entry representing the machine remo runs on. Has a name (default `local`) and an optional projects folder. No lifecycle; at most one per machine and user. Reached directly, never over SSH; never pushed or synced to a web deployment.
- **Host access**: The way remo runs a host's scripts — SSH (today, all existing host types) or local (this feature). Every host-reaching operation goes through it.
- **Project index**: Per-host list of project records (name → absolute path), kept by the host scripts in the user's state directory. Explicit records plus implicit ones for each subfolder of the projects folder.
- **Project**: A named checkout with its devcontainer configuration. Its name is its identity on that host; its path is a host-local fact held only in the index.
- **Prerequisite**: An external tool the local host needs, with a required/optional flag, a present/missing (and running/not-running, for the container engine) state, and a per-platform install command.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with the prerequisites installed goes from installing remo to working inside a project's devcontainer on their own Mac in under 5 minutes, with no VM and no SSH server.
- **SC-002**: 100% of the day-to-day session commands (`remo shell` menu, `-p`, `--exec`, `--detach`, `remo resume`) produce the same user-visible outcome against the local host as against a remote host.
- **SC-003**: Checkouts in at least two different folders can be registered and opened without moving any file.
- **SC-004**: Running local setup leaves the user's shell startup files byte-identical and never prompts for an administrator password.
- **SC-005**: A second run of local setup changes zero files.
- **SC-006**: Every existing host type behaves identically before and after this feature: the same commands are sent over SSH and an upgraded remote host lists the same projects.
- **SC-007**: Every missing prerequisite is reported with a copy-pasteable install command for the user's platform.
- **SC-008**: A user who previously ran a VM only to use `remo shell` can retire it following the documentation alone.

## Assumptions

- The user installs and runs their container engine (Docker Desktop, OrbStack, Colima, or native Docker on Linux), devcontainer runtime and Zellij themselves; remo detects and guides, never installs them.
- macOS is the primary target and Linux workstations are supported with the same mechanism; Windows remains unsupported (the existing platform gate applies).
- The local host is used by the person logged in; remo does not create or switch accounts, and the "remo user" on a local host is simply the current user.
- The web console (`remo web`) showing or attaching local projects is out of scope; its protocol is unchanged, so remote hosts' consoles are unaffected.
- Moving projects between hosts is a later feature; this spec only keeps project identity portable (FR-030).
- `remo cp` and port tunnels have no local meaning (the files and ports are already on this machine) and are refused rather than emulated.
- The local host is named `local` by default and has no projects folder by default (Clarifications, 2026-11-26).
- Issue #223 (a public connection seam for non-SSH providers) is answered here for the local case only; it stays open for non-SSH providers in general.
- No new runtime dependency is added to remo itself; anything the host scripts need (such as a newer shell) is a detected prerequisite.
