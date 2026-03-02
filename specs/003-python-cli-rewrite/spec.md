# Feature Specification: Python CLI Rewrite

**Feature Branch**: `003-python-cli-rewrite`
**Created**: 2026-02-28
**Status**: Draft
**Input**: User description: "Convert the client-side shell parts of the remo CLI to Python, with an appropriately structured and modularized Python project. The Ansible code should remain in Ansible, invoked via Python. The client TUI offered by fzf should be replaced by the appropriate Python alternative library. The server-side code can remain in shell for now."

## Clarifications

### Session 2026-02-28

- Q: How should the Python CLI be distributed and installed? → A: Pip-installable package with `console_scripts` entry point, distributed via git clone, installed with `pip install -e .`
- Q: What is the migration strategy from bash to Python? → A: Direct replacement — Python CLI fully replaces the bash script, no coexistence period
- Q: How should verbose/debug output be controlled? → A: Support `--verbose` / `-v` flag and `REMO_VERBOSE=1` env var, matching current behavior
- Q: What style should confirmation prompts use for destructive operations? → A: Simple text-based yes/no prompts matching current behavior, not TUI widgets
- Q: How should external API errors (AWS, Hetzner, GitHub) be handled? → A: Fail fast with clear error messages, no automatic retries

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Connect to a Remote Environment (Priority: P1)

A developer runs the remo CLI to connect to one of their registered development environments via an interactive shell session. If multiple environments are registered, an interactive picker is presented (replacing the current fzf-based picker). The connection supports optional port forwarding. On connection drop (e.g., laptop sleep), the terminal is restored to a sane state automatically.

**Why this priority**: Shell access is the most frequently used command and the core value proposition of remo. If nothing else works, this must.

**Independent Test**: Can be fully tested by registering a known host entry and running `remo shell`, verifying SSH connects with correct options, port tunneling works, and terminal resets on disconnect.

**Acceptance Scenarios**:

1. **Given** a single registered environment, **When** the user runs `remo shell`, **Then** remo connects directly via SSH without prompting for selection.
2. **Given** multiple registered environments, **When** the user runs `remo shell`, **Then** an interactive picker displays all environments with type, name, and host, and the user can search/filter and select one.
3. **Given** an active SSH session that drops unexpectedly, **When** the user returns to the local terminal, **Then** mouse tracking, alternate screen buffer, bracketed paste, and cursor state are all restored to normal.
4. **Given** the user specifies `-L 8080:3000`, **When** remo connects, **Then** local port 8080 is forwarded to remote port 3000.
5. **Given** an AWS environment with SSM access mode, **When** the user runs `remo shell`, **Then** remo uses the SSM proxy command and auto-starts the instance if it is stopped.

---

### User Story 2 - Create, Destroy, and Update Environments (Priority: P1)

A developer provisions new development environments (Incus containers, Hetzner VMs, or AWS EC2 instances) via `remo <provider> create`, tears them down with `destroy`, or updates dev tools with `update`. The CLI collects the required parameters interactively or via flags, invokes the appropriate Ansible playbook on the target, and registers/unregisters the environment in the local configuration.

**Why this priority**: Environment lifecycle management is the second core capability. Users cannot use remo without being able to create environments.

**Independent Test**: Can be tested by running `remo incus create testenv`, verifying the Ansible playbook is invoked with correct extra variables, and confirming the environment is registered in known_hosts.

**Acceptance Scenarios**:

1. **Given** the user runs `remo incus create mycontainer`, **When** all required parameters are provided, **Then** the appropriate Ansible playbook runs with correct extra variables and the container is registered locally on success.
2. **Given** the user runs `remo hetzner destroy myvm`, **When** prompted for confirmation, **Then** the teardown playbook runs and the environment is unregistered from known_hosts.
3. **Given** the user runs `remo aws create` with `--tools docker,nodejs`, **When** the playbook completes, **Then** only the specified dev tools are installed.
4. **Given** a playbook fails during execution, **When** the error occurs, **Then** the user sees a clear error message with the failing task name, and the full Ansible output is available for debugging.

---

### User Story 3 - Copy Files To/From Environments (Priority: P1)

A developer transfers files between their local machine and a remote environment using `remo cp`. The command supports colon notation (e.g., `myenv:~/project/` or `:~/file` for the default environment) and handles both upload and download directions. Transfers use rsync for efficiency.

**Why this priority**: File transfer is essential for development workflows and is used frequently alongside shell access.

**Independent Test**: Can be tested by running `remo cp localfile :~/remote/path` and verifying rsync is invoked with the correct SSH options and paths.

**Acceptance Scenarios**:

1. **Given** a local file and a remote path using colon notation, **When** the user runs `remo cp file.txt :~/project/`, **Then** the file is uploaded via rsync to the resolved environment.
2. **Given** a named environment, **When** the user runs `remo cp myenv:~/data.csv ./`, **Then** the file is downloaded from that specific environment.
3. **Given** an environment using AWS SSM access, **When** the user runs `remo cp`, **Then** rsync uses the correct SSH proxy command with properly quoted options.

---

### User Story 4 - List and Discover Environments (Priority: P2)

A developer lists their registered environments with `remo <provider> list` or refreshes the local registry by discovering running environments with `remo <provider> sync`. Sync queries the provider APIs (Incus CLI, Hetzner API, AWS EC2 API) and updates the local known_hosts registry.

**Why this priority**: Listing and discovery support the core workflows but are not blocking for basic usage if environments are manually registered.

**Independent Test**: Can be tested by running `remo aws sync` and verifying the correct API calls are made and known_hosts is updated with discovered instances.

**Acceptance Scenarios**:

1. **Given** registered environments exist, **When** the user runs `remo incus list`, **Then** all Incus containers are displayed with name, host, and user.
2. **Given** running AWS instances tagged with `remo`, **When** the user runs `remo aws sync`, **Then** all matching instances are discovered and registered in known_hosts with correct instance IDs, access modes, and regions.
3. **Given** a previously registered environment that no longer exists, **When** sync runs, **Then** the stale entry is removed from known_hosts.

---

### User Story 5 - AWS Instance Lifecycle Management (Priority: P2)

A developer manages the lifecycle of AWS instances with `remo aws stop`, `remo aws start`, `remo aws reboot`, and `remo aws info`. These commands directly call AWS APIs to control instance state without invoking Ansible.

**Why this priority**: Cost management (stop/start) is important for cloud instances but not required for initial functionality.

**Independent Test**: Can be tested by running `remo aws stop myinstance` and verifying the correct API call is made and the user receives status confirmation.

**Acceptance Scenarios**:

1. **Given** a running AWS instance, **When** the user runs `remo aws stop myinstance`, **Then** the instance is stopped and the user is informed of the state change.
2. **Given** a stopped AWS instance, **When** the user runs `remo aws start myinstance`, **Then** the instance starts and the user sees the new public IP.
3. **Given** an AWS instance, **When** the user runs `remo aws info myinstance`, **Then** detailed instance information (state, type, IP, volumes, uptime) is displayed.

---

### User Story 6 - Initialize and Self-Update (Priority: P3)

A developer initializes the remo tool with `remo init` (installs dependencies) and keeps it up to date with `remo self-update`. The tool passively checks for newer versions and displays a hint when one is available.

**Why this priority**: Setup and updates are one-time or infrequent operations. They support the overall experience but are not part of daily workflows.

**Independent Test**: Can be tested by running `remo self-update --check` and verifying it queries the GitHub API and compares versions correctly.

**Acceptance Scenarios**:

1. **Given** a fresh installation, **When** the user runs `remo init`, **Then** all dependencies are installed and Ansible galaxy collections are downloaded.
2. **Given** a newer version exists on GitHub, **When** the user runs `remo self-update`, **Then** the tool updates itself and confirms the new version.
3. **Given** a newer version exists, **When** the user runs any command, **Then** a non-intrusive hint is displayed suggesting an update (checked at most once per 24 hours).

---

### User Story 7 - Incus Host Bootstrap (Priority: P3)

An administrator initializes an Incus host with `remo incus bootstrap`, which configures networking (macvlan), storage pools, and base infrastructure required before containers can be created.

**Why this priority**: Bootstrap is a one-time setup operation per host and is already well-served by the existing Ansible playbook.

**Independent Test**: Can be tested by running `remo incus bootstrap` on a fresh host and verifying the Ansible playbook completes and Incus is properly configured.

**Acceptance Scenarios**:

1. **Given** a fresh host with Incus installed, **When** the user runs `remo incus bootstrap`, **Then** the bootstrap Ansible playbook runs and configures networking and storage.
2. **Given** a remote host, **When** the user runs `remo incus bootstrap -i "host," -e "ansible_user=admin"`, **Then** the playbook targets the specified remote host.

---

### Edge Cases

- What happens when the interactive picker is presented but no environments are registered? The user sees a clear message directing them to create an environment first.
- What happens when the user runs `remo cp` with an ambiguous environment name that matches multiple entries? The command fails with a clear error listing the matches.
- What happens when AWS credentials are expired or missing? The user sees a specific error message about credential configuration rather than a raw API error.
- What happens when the Ansible virtual environment or dependencies are not initialized? Commands that require Ansible prompt the user to run `remo init` first.
- What happens when a user runs `remo shell` and the target host is unreachable? The SSH connection times out with a clear error rather than hanging indefinitely.
- What happens during `remo self-update` if there are local modifications to the remo source? The user is warned about uncommitted changes before updating.
- What happens when the GitHub API is unreachable during `remo self-update --check`? The update check fails silently (for passive checks) or with a clear error (for explicit checks), never blocking normal command execution.
- What happens when the Hetzner API returns an error during `remo hetzner sync`? The sync fails fast with a user-friendly error message indicating the API issue, no retries.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The CLI MUST preserve all existing command-line interfaces, flags, and argument formats so that existing user workflows and scripts continue to work without modification.
- **FR-002**: The CLI MUST replace the fzf-based interactive picker with a native interactive selection widget that supports searching/filtering, displays environment type, name, and host, and works without external binary dependencies.
- **FR-003**: The CLI MUST invoke existing Ansible playbooks for all provisioning, configuration, and teardown operations, passing the same extra variables and inventory specifications as the current shell implementation.
- **FR-004**: The CLI MUST filter and format Ansible playbook output identically to the current behavior: showing PLAY and TASK names progressively, hiding skipped tasks, and displaying full output on errors.
- **FR-005**: The CLI MUST manage a local known_hosts registry file (at `$REMO_HOME/known_hosts` or `$XDG_CONFIG_HOME/remo/known_hosts`) in the same colon-delimited format for environment registration, lookup, and removal.
- **FR-006**: The CLI MUST support SSH connections with multiplexing, AWS SSM proxy commands, timezone propagation, and port forwarding, matching the current SSH option construction.
- **FR-007**: The CLI MUST restore full terminal state (mouse tracking, alternate screen buffer, bracketed paste mode, application cursor keys, cursor visibility, and line discipline) after SSH disconnections, whether graceful or abrupt.
- **FR-008**: The CLI MUST support file transfers via rsync with correct SSH option passthrough, including proper quoting of options containing spaces (e.g., SSM ProxyCommand).
- **FR-009**: The CLI MUST query AWS APIs for instance lifecycle operations (stop, start, reboot, info), instance discovery (sync), and auto-starting stopped instances before shell connections.
- **FR-010**: The CLI MUST query the Hetzner Cloud API for server discovery during sync operations.
- **FR-011**: The CLI MUST query the local Incus CLI or remote Incus host for container discovery during sync operations.
- **FR-012**: The CLI MUST support the self-update mechanism, checking the GitHub API for newer releases, comparing semantic versions, and performing git-based updates.
- **FR-013**: The CLI MUST implement passive version checking with a 24-hour cache so update hints are shown non-intrusively on regular command invocations.
- **FR-014**: The CLI MUST validate user inputs (environment names, port numbers, regions, tool names) with the same rules as the current implementation.
- **FR-015**: The CLI MUST produce colored terminal output (error, success, info, warning messages) consistent with the current visual style.
- **FR-016**: The project MUST be structured as a pip-installable package with a `console_scripts` entry point (so `remo` is the command name), with clear separation between CLI entry point, provider modules (Incus, Hetzner, AWS), SSH/connection handling, configuration management, and Ansible integration.
- **FR-017**: The server-side Ansible playbooks, roles, and task files MUST remain unchanged and continue to be invoked from their existing locations within the `ansible/` directory.
- **FR-018**: The CLI MUST support the `remo init` command to set up the environment and install required dependencies including Ansible collections.
- **FR-019**: The CLI MUST support a `--verbose` / `-v` flag and the `REMO_VERBOSE=1` environment variable to show full Ansible playbook output instead of filtered task names.
- **FR-020**: Destructive operations (destroy, teardown) MUST prompt for confirmation using simple text-based yes/no prompts, accepting "yes" and common affirmative variants.
- **FR-021**: When external API calls (AWS, Hetzner, GitHub) fail or are unreachable, the CLI MUST fail fast with a clear, user-friendly error message and MUST NOT silently retry.
- **FR-022**: The Python CLI MUST fully replace the bash `remo` script as a direct replacement, with no coexistence or gradual migration period. The old bash script is archived.

### Key Entities

- **Environment**: A registered development environment with type (incus/hetzner/aws), name, host, user, and optional provider-specific attributes (instance ID, access mode, region).
- **Provider**: A cloud or local infrastructure backend (Incus, Hetzner, AWS) with its own create/destroy/update/list/sync operations and API integration.
- **Known Hosts Registry**: The local configuration file tracking all registered environments in a colon-delimited format.
- **Ansible Playbook Runner**: The component responsible for invoking ansible-playbook with correct arguments, filtering output, and handling errors.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All existing `remo` commands and flags produce identical behavior and output from the user's perspective, with zero breaking changes to command-line interfaces.
- **SC-002**: Users can select environments interactively without requiring fzf to be installed on the local machine.
- **SC-003**: Environment creation, update, and destruction complete successfully by invoking the same Ansible playbooks with the same parameters.
- **SC-004**: Terminal state is fully restored after SSH connection drops, with no residual mouse tracking codes, alternate screen artifacts, or broken input modes.
- **SC-005**: File transfers via `remo cp` work identically for all access modes (direct SSH, AWS SSM) with correct rsync behavior.
- **SC-006**: The project can be installed and run with standard packaging tools, with all dependencies declared and manageable.
- **SC-007**: The codebase is organized into distinct modules that can be understood, tested, and modified independently (e.g., changing the AWS provider does not require touching Incus code).
- **SC-008**: The Ansible playbooks and roles remain completely unmodified throughout the rewrite.

## Assumptions

- The minimum Python version for the rewritten CLI will be 3.11, consistent with the current venv configuration.
- The existing `known_hosts` file format will be preserved for backward compatibility, so users upgrading from the shell version retain their registered environments.
- The Ansible playbooks will continue to be co-located in the `ansible/` directory relative to the project root, not packaged separately.
- The self-update mechanism will continue to use git operations since remo is distributed as a git repository clone.
- rsync will remain the file transfer tool (invoked as a subprocess), as reimplementing its delta-transfer algorithm would be impractical.
- SSH will continue to be invoked as a subprocess for interactive shell sessions, as library-based SSH does not adequately support full PTY passthrough with multiplexing and SSM proxy commands.
- The `run.sh` wrapper script may be retired or simplified, as its venv-activation role will be handled by the Python package itself.
- The project will be distributed via git clone and installed with `pip install -e .` (or `uv pip install -e .`), not published to PyPI.
- The bash `remo` script will be archived (e.g., renamed to `remo.bash.archived`) when the Python CLI is ready, not deleted, to preserve history.
