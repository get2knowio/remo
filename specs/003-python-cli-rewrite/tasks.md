# Tasks: Python CLI Rewrite

**Input**: Design documents from `/specs/003-python-cli-rewrite/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md

**Tests**: Not explicitly requested in the feature specification. Test tasks are omitted.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Create project skeleton, packaging, and all `__init__.py` files

- [x] T001 Create Python package directory structure: `src/remo/`, `src/remo/cli/`, `src/remo/cli/providers/`, `src/remo/providers/`, `src/remo/core/`, `src/remo/models/`, `tests/`, `tests/unit/`, `tests/unit/core/`, `tests/unit/providers/`, `tests/unit/cli/`, `tests/integration/` with all `__init__.py` files
- [x] T002 Create `pyproject.toml` with hatchling build backend, `console_scripts` entry point (`remo = "remo.cli.main:cli"`), dependencies (click>=8.1, InquirerPy), optional-dependencies for aws (boto3), hetzner (hcloud), all, and dev (pytest, pytest-mock, ruff, mypy), Python >=3.11 requirement
- [x] T003 Create `src/remo/__init__.py` with `__version__` derived from package metadata and `src/remo/__main__.py` that imports and calls `cli` from `remo.cli.main`
- [x] T004 Create `tests/conftest.py` with shared fixtures (tmp config directory, mock subprocess helpers) and verify project installs and `remo --help` placeholder works by creating minimal `src/remo/cli/main.py` with empty Click group

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core modules that ALL user stories depend on. Must complete before any user story work begins.

**CRITICAL**: No user story work can begin until this phase is complete.

- [x] T005 [P] Implement KnownHost dataclass in `src/remo/models/host.py` with fields (type, name, host, user, instance_id, access_mode, region), `to_line()` serialization to colon-delimited format, `from_line()` classmethod for parsing, and `display_name` property for picker display
- [x] T006 [P] Implement config module in `src/remo/core/config.py` with `get_remo_home()` (respects `REMO_HOME` then `XDG_CONFIG_HOME/remo` then `~/.config/remo`), `get_ansible_dir()` (resolves `ansible/` relative to project root), `get_known_hosts_path()`, `is_verbose()` (checks `REMO_VERBOSE` env var only — CLI commands pass `verbose` param explicitly to functions that need it)
- [x] T007 [P] Implement output module in `src/remo/core/output.py` with `print_error()`, `print_success()`, `print_info()`, `print_warning()` using ANSI color codes matching current bash output style, plus `confirm()` function for yes/no prompts accepting "yes" and common affirmative variants
- [x] T008 [P] Implement validation module in `src/remo/core/validation.py` with `validate_name()` (regex `^[a-zA-Z0-9][a-zA-Z0-9._/-]*$`, max 63 chars), `validate_port()` (1-65535), `validate_region()` (AWS region format), `validate_tool_name()` (allowed tool names: docker, nodejs, zellij, fzf, github_cli, devcontainers), `build_tool_args()` for parsing `--tools`/`--only`/`--skip` flags
- [x] T009 Implement known_hosts registry in `src/remo/core/known_hosts.py` with `save_known_host(host: KnownHost)`, `remove_known_host(type, name)`, `get_known_hosts(type_filter=None)` returning list of KnownHost, `clear_known_hosts_by_type(type)`, `clear_known_hosts_by_prefix(prefix)`, `get_aws_region(name)`, `resolve_remo_host_by_name(name)` returning a single KnownHost by exact name match (raises error if not found, raises error listing matches if ambiguous) — all operating on the file at `get_known_hosts_path()`
- [x] T010 Implement SSH module in `src/remo/core/ssh.py` with `build_ssh_opts(multiplex=False, host: KnownHost)` returning list of SSH option strings (ControlMaster/ControlPath/ControlPersist for multiplex, SSM ProxyCommand for aws+ssm access mode, SendEnv=TZ for timezone propagation), `require_session_manager_plugin()` that checks for the AWS SSM plugin binary and raises a clear error if missing, `reset_terminal()` function sending escape sequences to disable mouse tracking/alt screen/bracketed paste/app cursor keys and running `stty sane`, `detect_timezone()` matching current bash logic
- [x] T011 Implement Ansible runner in `src/remo/core/ansible_runner.py` with `run_playbook(playbook, extra_vars=None, inventory=None, verbose=False)` that invokes `ansible-playbook` as subprocess from the `ansible/` directory, streams stdout filtering to show only PLAY/TASK names progressively (hiding skipped tasks) unless verbose mode, displays full output on error, returns exit code
- [x] T012 Implement picker module in `src/remo/core/picker.py` with `pick_environment(hosts: list[KnownHost], prompt="Select environment: ")` that uses InquirerPy fuzzy prompt to display `{type}: {display_name} ({host})` for each host, returns selected KnownHost. If only one host, return it directly without prompting. If no hosts, raise error with message to create an environment first.
- [x] T013 Implement root CLI group in `src/remo/cli/main.py` with Click group `cli`, `--version`/`-v` flag showing version from `remo.__version__`, `--help`/`-h` flag, register all subcommand groups (shell, cp, init, self-update, incus, hetzner, aws). Wire provider groups from `src/remo/cli/providers/`

**Checkpoint**: Foundation ready — `remo --version` and `remo --help` work, all core modules importable and functional.

---

## Phase 3: User Story 1 - Connect to a Remote Environment (Priority: P1) MVP

**Goal**: User can run `remo shell` to SSH into a registered environment with interactive picker, port forwarding, SSM support, and terminal reset on disconnect.

**Independent Test**: Register a known host entry manually, run `remo shell`, verify SSH connects with correct options and terminal resets on disconnect.

### Implementation for User Story 1

- [x] T014 [US1] Implement host resolution in `src/remo/core/ssh.py`: add `resolve_remo_host(name=None)` that loads known_hosts, if name given resolves by name (via `resolve_remo_host_by_name()`), if multiple hosts and no name invokes `pick_environment()`, if single host returns directly. Implement `auto_start_aws_if_stopped(host: KnownHost)` in `src/remo/providers/aws.py` (not core/ssh.py — core must not depend on provider SDKs) that queries instance state via boto3 and starts if stopped (with wait loop for public IP)
- [x] T015 [US1] Implement `shell_connect(host, tunnels, no_open)` in `src/remo/core/ssh.py`: build SSH command with `build_ssh_opts(multiplex=True)`, add `-L` flags for each tunnel spec, validate ports, check local port availability via `ss`, add SSH target, optionally auto-open browser for first tunnel, execute SSH with `subprocess.run()` wrapped in try/finally with `reset_terminal()` and EXIT signal trap, handle non-zero exit without propagating (connection drops return 255)
- [x] T016 [US1] Implement `remo shell` Click command in `src/remo/cli/shell.py` with `-L` option (multiple=True, short-only), `--no-open` flag, parsing tunnel specs (local:remote or single port), calling `resolve_remo_host()`, then `auto_start_aws_if_stopped()` from `providers/aws.py` if host is AWS SSM, then `shell_connect()`
- [x] T017 [US1] Wire `shell` command into root CLI group in `src/remo/cli/main.py`

**Checkpoint**: `remo shell` fully functional — SSH connect, picker, tunnels, terminal reset, SSM auto-start all working.

---

## Phase 4: User Story 2 - Create, Destroy, and Update Environments (Priority: P1)

**Goal**: User can provision, tear down, and update dev tools on Incus containers, Hetzner VMs, and AWS EC2 instances via `remo <provider> create/destroy/update`.

**Independent Test**: Run `remo incus create testenv` and verify Ansible playbook invoked with correct extra vars, environment registered in known_hosts.

### Implementation for User Story 2

- [x] T018 [P] [US2] Implement Incus provider create/destroy/update logic in `src/remo/providers/incus.py`: `create(name, host, domain, image, tools, timezone, auto_confirm)` building extra_vars and calling `run_playbook("incus_site.yml", ...)`, `destroy(name, host, remove_storage, auto_confirm)` calling `run_playbook("incus_teardown.yml", ...)`, `update(name, host, tools, timezone)` calling `run_playbook("incus_configure.yml", ...)`. Each function registers/unregisters known_host as appropriate.
- [x] T019 [P] [US2] Implement Hetzner provider create/destroy/update logic in `src/remo/providers/hetzner.py`: `create(name, server_type, location, volume_size, tools, timezone, auto_confirm)` calling `run_playbook("hetzner_site.yml", ...)` then querying Hetzner API for server IP to register, `destroy(name, remove_volume, auto_confirm)` calling `run_playbook("hetzner_teardown.yml", ...)`, `update(name, tools, timezone)` calling `run_playbook("hetzner_configure.yml", ...)`
- [x] T020 [P] [US2] Implement AWS provider create/destroy/update logic in `src/remo/providers/aws.py`: `create(name, instance_type, region, ebs_size, use_spot, iam_profile, tools, timezone, auto_confirm)` calling `run_playbook("aws_site.yml", ...)` then querying boto3 for instance details to register with SSM access mode, `destroy(name, auto_confirm)` calling `run_playbook("aws_teardown.yml", ...)`, `update(name, tools, timezone)` calling `run_playbook("aws_configure.yml", ...)`. Include `select_ssm_instance_profile()` using picker for IAM profile selection.
- [x] T021 [US2] Implement Incus CLI commands in `src/remo/cli/providers/incus.py`: Click group `incus` with `create` command (flags: `--name`, `--host`, `--domain`, `--image`, `--tools`, `--only`, `--skip`, `--yes`/`-y`), `destroy` command (flags: `--remove-storage`, `--yes`/`-y`), `update` command (flags: `--tools`, `--only`, `--skip`)
- [x] T022 [US2] Implement Hetzner CLI commands in `src/remo/cli/providers/hetzner.py`: Click group `hetzner` with `create` command (flags: `--name`, `--type`, `--location`, `--volume-size`, `--tools`, `--only`, `--skip`, `--yes`/`-y`), `destroy` command (flags: `--remove-volume`, `--yes`/`-y`), `update` command (flags: `--tools`, `--only`, `--skip`)
- [x] T023 [US2] Implement AWS CLI commands (create/destroy/update only) in `src/remo/cli/providers/aws.py`: Click group `aws` with `create` command (flags: `--name`, `--type`, `--region`, `--ebs-size`, `--spot`, `--iam-profile`, `--tools`, `--only`, `--skip`, `--yes`/`-y`), `destroy` command (flags: `--yes`/`-y`), `update` command (flags: `--tools`, `--only`, `--skip`)
- [x] T024 [US2] Wire incus, hetzner, and aws CLI groups into root CLI group in `src/remo/cli/main.py`

**Checkpoint**: `remo incus create`, `remo hetzner destroy`, `remo aws update` etc. all invoke correct Ansible playbooks and manage known_hosts.

---

## Phase 5: User Story 3 - Copy Files To/From Environments (Priority: P1)

**Goal**: User can transfer files between local machine and remote environments using `remo cp` with colon notation and rsync.

**Independent Test**: Run `remo cp file.txt :~/remote/path` and verify rsync invoked with correct SSH options and paths.

### Implementation for User Story 3

- [x] T025 [US3] Implement rsync module in `src/remo/core/rsync.py` with `transfer(ssh_opts, source, dest, recursive=False, progress=False)` that builds rsync command with `-e` SSH option string (properly quoting options containing spaces for SSM ProxyCommand compatibility), runs rsync as subprocess with progress output, captures stderr to temp file for error reporting, returns exit code
- [x] T026 [US3] Implement `remo cp` Click command in `src/remo/cli/cp.py` with `parse_remote_spec(spec)` to parse colon notation (`:path` for default env, `name:path` for named env), determine transfer direction (upload vs download), resolve host by name via `resolve_remo_host_by_name()` from `core/known_hosts.py` (non-interactive, fails with clear error on ambiguity), build SSH opts, invoke `transfer()`. Flags: `-r` (recursive), `--progress`
- [x] T027 [US3] Wire `cp` command into root CLI group in `src/remo/cli/main.py`

**Checkpoint**: `remo cp localfile :~/remote/` and `remo cp myenv:~/remote/file ./` both work for all access modes.

---

## Phase 6: User Story 4 - List and Discover Environments (Priority: P2)

**Goal**: User can list registered environments and discover/sync running environments from provider APIs.

**Independent Test**: Run `remo aws sync` and verify known_hosts updated with discovered instances.

### Implementation for User Story 4

- [x] T028 [P] [US4] Implement Incus list/sync logic in `src/remo/providers/incus.py`: `list_hosts()` filtering known_hosts by type=incus and formatting output, `sync(host=None)` querying `incus list` locally or via SSH on remote host, parsing CSV output, registering/clearing known_hosts
- [x] T029 [P] [US4] Implement Hetzner list/sync logic in `src/remo/providers/hetzner.py`: `list_hosts()` filtering known_hosts by type=hetzner and formatting output, `sync()` querying Hetzner API via hcloud SDK for servers with `remo` label, registering/clearing known_hosts
- [x] T030 [P] [US4] Implement AWS list/sync logic in `src/remo/providers/aws.py`: `list_hosts()` filtering known_hosts by type=aws and formatting output, `sync(region=None)` querying EC2 via boto3 for instances with `remo` tag, determining SSM access mode, registering/clearing known_hosts
- [x] T031 [US4] Add `list` and `sync` Click commands to each provider CLI group in `src/remo/cli/providers/incus.py` (sync flags: `--host`), `src/remo/cli/providers/hetzner.py`, and `src/remo/cli/providers/aws.py` (sync flags: `--region`)

**Checkpoint**: `remo incus list`, `remo hetzner sync`, `remo aws sync` all work and update known_hosts correctly.

---

## Phase 7: User Story 5 - AWS Instance Lifecycle Management (Priority: P2)

**Goal**: User can stop, start, reboot, and inspect AWS instances via `remo aws stop/start/reboot/info`.

**Independent Test**: Run `remo aws stop myinstance` and verify correct boto3 API call and status output.

### Implementation for User Story 5

- [x] T032 [US5] Implement AWS lifecycle logic in `src/remo/providers/aws.py`: `stop(name)` calling `ec2.stop_instances()`, `start(name)` calling `ec2.start_instances()` and waiting for public IP, `reboot(name)` calling `ec2.reboot_instances()`, `info(name)` querying instance details (state, type, IP, volumes, launch time, uptime) and formatting display. All functions resolve instance_id from known_hosts and handle missing credentials with clear errors.
- [x] T033 [US5] Add `stop`, `start`, `reboot`, and `info` Click commands to AWS CLI group in `src/remo/cli/providers/aws.py`, each taking a positional `name` argument

**Checkpoint**: `remo aws stop myinstance`, `remo aws start myinstance`, `remo aws reboot myinstance`, `remo aws info myinstance` all work.

---

## Phase 8: User Story 6 - Initialize and Self-Update (Priority: P3)

**Goal**: User can set up the remo environment with `remo init` and update with `remo self-update`. Passive version hints on regular usage.

**Independent Test**: Run `remo self-update --check` and verify GitHub API query and version comparison.

### Implementation for User Story 6

- [x] T034 [P] [US6] Implement version module in `src/remo/core/version.py` with `get_current_version()` (from package metadata or git tags), `get_latest_release()` (query GitHub API, parse semver), `version_is_newer(current, latest)` (semver comparison), `check_for_updates_passive()` (read/write cache file with 24-hour TTL, return hint string or None)
- [x] T035 [P] [US6] Implement init logic in `src/remo/core/init.py`: `handle_init(force=False)` function that runs `pip install` for dependencies and `ansible-galaxy collection install` from `ansible/requirements.yml`
- [x] T036 [US6] Implement self-update logic in `src/remo/core/version.py`: `handle_self_update(version=None, check_only=False, pre_release=False)` that checks for uncommitted changes (warn), fetches latest release, compares versions, performs git-based update (`git fetch`, `git checkout`)
- [x] T037 [US6] Implement `remo init` Click command in `src/remo/cli/init_cmd.py` (flags: `--force`) and `remo self-update` Click command in `src/remo/cli/self_update.py` (flags: `--version`, `--check`, `--pre-release`)
- [x] T038 [US6] Add passive update check to root CLI group in `src/remo/cli/main.py`: call `check_for_updates_passive()` on every invocation and print hint if available (non-blocking, silent on errors)
- [x] T039 [US6] Wire `init` and `self-update` commands into root CLI group in `src/remo/cli/main.py`

**Checkpoint**: `remo init`, `remo self-update`, and passive update hints all work.

---

## Phase 9: User Story 7 - Incus Host Bootstrap (Priority: P3)

**Goal**: Administrator can initialize an Incus host with `remo incus bootstrap`.

**Independent Test**: Run `remo incus bootstrap` and verify Ansible bootstrap playbook invoked correctly.

### Implementation for User Story 7

- [x] T040 [US7] Implement bootstrap logic in `src/remo/providers/incus.py`: `bootstrap(inventory=None, extra_vars=None, verbose=False)` calling `run_playbook("incus_bootstrap.yml", ...)` with passthrough of `-i` and `-e` arguments
- [x] T041 [US7] Add `bootstrap` Click command to Incus CLI group in `src/remo/cli/providers/incus.py` with flags `-i` (inventory), `-e` (extra-vars, multiple=True), `-v`/`--verbose`

**Checkpoint**: `remo incus bootstrap` and `remo incus bootstrap -i "host," -e "ansible_user=admin"` both work.

---

## Phase 10: Polish & Cross-Cutting Concerns

**Purpose**: Finalization, cleanup, and validation

- [x] T042 Archive bash script by renaming `remo` to `remo.bash.archived`
- [x] T043 Verify all CLI commands match bash interface exactly: compare `remo --help`, `remo shell --help`, `remo cp --help`, `remo incus --help`, `remo hetzner --help`, `remo aws --help` output against bash version
- [x] T044 Validate `pip install -e ".[all,dev]"` works cleanly, `remo --version` shows correct version, `remo --help` shows all commands
- [x] T045 Update CLAUDE.md with new project structure, Python commands, and development workflow
- [x] T046 Update README.md to reflect the Python CLI rewrite: installation via `pip install -e .`, new prerequisites (Python 3.11+), updated usage examples. Per constitution principle V, documentation MUST accompany feature changes.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion — BLOCKS all user stories
- **US1 Shell (Phase 3)**: Depends on Foundational — MVP
- **US2 Create/Destroy/Update (Phase 4)**: Depends on Foundational — can run in parallel with US1
- **US3 Copy Files (Phase 5)**: Depends on Foundational — can run in parallel with US1, US2
- **US4 List/Discover (Phase 6)**: Depends on Foundational — can run in parallel with US1-US3
- **US5 AWS Lifecycle (Phase 7)**: Depends on Foundational — can run in parallel, but benefits from US4 sync logic in providers/aws.py
- **US6 Init/Self-Update (Phase 8)**: Depends on Foundational — can run in parallel with all others
- **US7 Bootstrap (Phase 9)**: Depends on Foundational — can run in parallel with all others
- **Polish (Phase 10)**: Depends on ALL user stories being complete

### User Story Dependencies

- **US1 (P1)**: No dependencies on other stories. Uses core/ssh.py, core/picker.py, core/known_hosts.py
- **US2 (P1)**: No dependencies on other stories. Uses core/ansible_runner.py, core/known_hosts.py, core/validation.py
- **US3 (P1)**: No dependencies on other stories. Uses core/ssh.py, core/rsync.py (new), core/known_hosts.py
- **US4 (P2)**: No dependencies on other stories. Extends providers/ with list/sync functions
- **US5 (P2)**: Shares `src/remo/providers/aws.py` with US2 and US4 — coordinate if developing in parallel
- **US6 (P3)**: No dependencies on other stories. Uses core/version.py (new)
- **US7 (P3)**: No dependencies on other stories. Extends providers/incus.py

### Within Each User Story

- Provider logic before CLI commands
- Core modules (rsync, version) before dependent provider/CLI code
- Wire commands into main.py as final step

### Parallel Opportunities

**Phase 2 (Foundational)**:
- T005, T006, T007, T008 can all run in parallel (independent core modules)
- T009 depends on T005 (KnownHost model) and T006 (config)
- T010 depends on T006 (config)
- T011 depends on T006 (config) and T007 (output)
- T012 depends on T005 (KnownHost model)
- T013 depends on all above

**User Stories (after Phase 2)**:
- US1, US2, US3 can all start in parallel (different files, independent functionality)
- Within US2: T018, T019, T020 can run in parallel (different provider files)
- Within US4: T028, T029, T030 can run in parallel (different provider files)
- US6 and US7 can run in parallel with all other stories

---

## Parallel Example: User Story 2

```bash
# Launch all provider implementations together (different files):
Task T018: "Implement Incus provider in src/remo/providers/incus.py"
Task T019: "Implement Hetzner provider in src/remo/providers/hetzner.py"
Task T020: "Implement AWS provider in src/remo/providers/aws.py"

# Then sequentially: CLI commands (depend on provider logic)
Task T021: "Implement Incus CLI commands in src/remo/cli/providers/incus.py"
Task T022: "Implement Hetzner CLI commands in src/remo/cli/providers/hetzner.py"
Task T023: "Implement AWS CLI commands in src/remo/cli/providers/aws.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL — blocks all stories)
3. Complete Phase 3: User Story 1 (Shell Connect)
4. **STOP and VALIDATE**: `remo shell` works end-to-end with picker, tunnels, terminal reset
5. This is the minimum useful tool — a user can connect to registered environments

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US1 Shell → Test → **MVP deployed** (can connect to environments)
3. US2 Create/Destroy/Update → Test → Can provision environments
4. US3 Copy Files → Test → Can transfer files
5. US4 List/Discover → Test → Can discover environments
6. US5 AWS Lifecycle → Test → Can manage AWS costs
7. US6 Init/Self-Update → Test → Can self-manage
8. US7 Bootstrap → Test → Can initialize new hosts
9. Polish → Archive bash script, validate everything

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- The bash `remo` script serves as the reference implementation — compare behavior at each checkpoint
- All provider SDKs (boto3, hcloud) use lazy imports with clear error messages if missing
