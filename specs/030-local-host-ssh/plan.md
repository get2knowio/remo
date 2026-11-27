# Implementation Plan: Reach a Local Host over SSH Too

**Branch**: `030-local-host-ssh` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/030-local-host-ssh/spec.md`

## Summary

Make a machine set up with `remo local setup` (spec 029) the same host whether
the user sits at it or reaches it over SSH from another workstation. The
pre-connect probe remo already runs learns two more facts from a new host-side
**host-info** record (`host_kind`, `menu_command`) in the same round trip;
with them a bare `remo shell` starts the project menu explicitly as the SSH
command (gated, so older hosts are untouched), and a locally managed host gets
`remo local setup` advice instead of `remo configure`. On macOS the host
scripts pin Zellij to one private per-user session directory and load the
tool PATH recorded at setup, so desk and SSH logins see the same sessions and
tools. An explicit, reversible `--allow-ssh-resume` adds the sshd `AcceptEnv`
drop-in (the only sudo step), and a new `remo projects` group manages any
host's project index from anywhere. Decisions R1–R9 are in
[research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11+ (CLI); Bash ≥ 4 host scripts (Jinja templates); Ansible core 2.18/2.19

**Primary Dependencies**: Click, ansible-core (existing). No new runtime dependency.

**Storage**: Host side: new `~/.local/state/remo/host-info` (key=value); `local.env` gains `REMO_PATH`; macOS session dir `~/.local/state/remo/zellij` (0700). Registry unchanged (v2, no new type).

**Testing**: pytest (serial), rendered-template execution with PATH/`uname` shims; macOS CI job `host-scripts-macos`.

**Target Platform**: Workstations macOS/Linux; remote hosts Debian/Ubuntu.

**Project Type**: CLI + Ansible-provisioned host scripts.

**Performance Goals**: No additional SSH round trip on `remo shell`/`remo resume` (the probe is the existing one).

**Constraints**: Every SSH argv except the bare-menu case byte-identical; remo-host protocol v1, additive JSON only; no FastAPI model change; no rc-file edits on the local machine; sudo only behind the explicit opt-in.

**Scale/Scope**: One host-info file per host; a handful of new CLI options/commands.

## Constitution Check

Source: `.specify/memory/constitution.md` (v2.1.0).

| # | Principle | Check | Status |
|---|-----------|-------|--------|
| I | Layered Architecture | `cli/projects.py` Click only; `providers/projects.py` no Click/sys.exit; probe + host-info parsing in `core/ssh.py`/`models/` name no provider | PASS |
| II | Providers Declared | No provider literal added; host kind comes from the host, not a `host.type` branch | PASS |
| III | Typed Errors | Configure refusal, unsupported-verb and opt-in failures raise `PreconditionError`/`OperationFailedError` with the fix | PASS |
| IV | Generated Contracts | Web `CapabilityOut` untouched; no artifact regeneration | N/A |
| V | Defensive Ansible | New host-info task and hook guard use `\| default()` for every var | PASS |
| VI | Test Skip/Fail Paths | Probe failure/timeout, gate both sides, non-private dir, declined/non-interactive/validate-fail opt-in, unsupported verbs | PASS |
| VII | Idempotent | Host-info in `_state_signature`; opt-in re-run requests no sudo; playbook second run changed=0 | PASS |
| VIII | Docs | `docs/local.md` section, README, CLAUDE.md/AGENTS.md structure + Recent Changes | PASS |
| IX | Off-index | Template/package-data change → Tier 2 (`check_plugin_wheels.sh` on CI wheel) | PASS |

Post-design re-check: all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/030-local-host-ssh/
├── plan.md
├── research.md            # R1–R9
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── host-probe.md      # probe command, host-info record, capabilities host_kind
│   ├── ssh-menu.md        # bare-menu argv + hook guard
│   ├── ssh-resume-optin.md
│   └── cli-projects.md    # remo projects list|add|remove
└── tasks.md
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/ssh.py               # probe_host_tools()/HostToolsProbe; check_remote_version wraps it; shell_connect(menu_command=)
├── core/host_access.py       # SSH_MENU_COMMAND constant
├── core/known_hosts.py       # upgrade_command_for(host, *, host_kind=None)
├── models/host_info.py       # NEW: HostInfo (parse key=value; tolerant)
├── models/capability.py      # + host_kind
├── cli/shell.py              # connect_to_host uses the probe; local-managed advice, no auto-upgrade
├── cli/local.py              # setup --allow-ssh-resume/--no-allow-ssh-resume
├── cli/projects.py           # NEW: remo projects list|add|remove HOST
├── cli/main.py               # mount projects
├── providers/projects.py     # NEW: generic index ops over any HostAccess
├── providers/local.py        # wrappers onto providers/projects; REMO_PATH; ssh-resume opt-in; old-session note; host-info in signature
└── providers/added.py        # configure refuses a locally managed host before the confirm
ansible/
├── local_setup.yml           # local.env REMO_PATH
└── roles/user_setup/
    ├── tasks/host_scripts.yml # + host-info record
    ├── tasks/main.yml         # PROJECT MENU hook guard on REMO_MENU
    └── templates/
        ├── remo-lib.sh.j2     # remo_pin_session_dir, remo_load_local_path
        ├── remo-host.sh.j2    # capabilities host_kind; projects add ~ expansion; call helpers
        ├── project-menu.sh.j2 / project-launch.sh.j2 / devshell.sh.j2  # call helpers
tests/ (new) unit/core/test_host_probe.py, unit/test_session_dir_pin.py, unit/test_host_info.py,
       unit/providers/test_ssh_resume_optin.py, unit/providers/test_projects.py, unit/cli/test_projects_cmd.py
docs/local.md, README.md, CLAUDE.md, AGENTS.md, .github/workflows/ci.yml
```

**Structure Decision**: follows 029's layering; the generic project-index
operations move from `providers/local.py` into `providers/projects.py`.

## Phasing

1. **Pin** current probe and bare-menu argv (characterization) before changing them.
2. **Host-info record + capabilities `host_kind`** (templates, host_scripts.yml, model).
3. **Probe** (`probe_host_tools`) and **explicit menu** (shell_connect, hook guard).
4. **Advice/refusal** for locally managed hosts (upgrade_command_for, connect_to_host, configure).
5. **macOS session dir + PATH** helpers, old-session note.
6. **Resume opt-in**.
7. **`remo projects`**.
8. Docs, CI job list, structure diagrams, Recent Changes, Tier 2 check.

## Complexity Tracking

None.
