# Implementation Plan: Local Machine as a Remo Host

**Branch**: `029-local-host` | **Date**: 2026-11-26 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/029-local-host/spec.md`

## Summary

Make the machine remo runs on a Remo host reachable without SSH. A new
`core/host_access.py` seam gives every host-reaching operation (menu, `-p`,
`--exec`, `--detach`, version check, `remo resume`'s lookup) two
implementations — SSH, byte-identical to today, and local, which runs the same
host-side commands with a detected bash ≥ 4 on the user's own TTY. A `local`
registry pseudo-type (modelled on `ssh`) and a `remo local {setup, check, add,
remove, list}` group register the machine and manage its projects. Local
setup reuses the *same* Ansible script-render tasks as remote hosts, run with
a local connection and no privilege escalation, and never touches rc files.
The host scripts become macOS-portable, start the devcontainer through a
Zellij session shell instead of `.bashrc`, and resolve projects through a
per-host **project index** (name → path) so existing checkouts anywhere can
be added in place. The local host never travels through `remo web push/sync`.
Every decision and its rationale is in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11+ (CLI); Bash ≥ 4 (host scripts, Ansible
Jinja templates); Ansible core 2.18/2.19 YAML

**Primary Dependencies**: Click, ansible-core (existing). No new runtime
dependency.

**Storage**: Registry v2 (`~/.config/remo/registry.json`) gains a `local`
pseudo-type entry (no format change). Host side: `~/.local/state/remo/
projects.tsv` (project index), `~/.local/state/remo/local.env` (detected bash,
local host only).

**Testing**: pytest (`uv run --no-sync pytest`, serial), including execution
tests of rendered bash templates with PATH shims; a new macOS CI job for the
host scripts.

**Target Platform**: Workstations: macOS (primary) and Linux. Remote hosts:
Debian/Ubuntu as today.

**Project Type**: CLI + host-side scripts provisioned by Ansible.

**Performance Goals**: Local `remo shell` reaches the menu in under 1 s on a
warm machine (no network round-trip); version check and resume lookup each
complete well under the existing 5 s budget.

**Constraints**: SSH argv byte-identical for every existing host type;
remo-host protocol v1 and all existing JSON shapes unchanged; no sudo, no user
creation, no rc-file edits on the local machine; no FastAPI model change.

**Scale/Scope**: One local host per machine; tens of projects in the index.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Source: `.specify/memory/constitution.md` (v2.1.0).

| # | Principle | Check for this feature | Status |
|---|-----------|------------------------|--------|
| I | Layered Architecture | `cli/local.py` is Click wiring only; `providers/local.py` has no Click/`sys.exit`; `core/host_access.py` names no provider (a `local` pseudo-type constant lives in `core/config.py` like `ADDED_HOST_TYPE`) | PASS |
| II | Providers Are Declared | `local` is a pseudo-type like `ssh`, not a provider; no `host.type` literal for any provider is added to core (the provider-literal gate bans only provider names) | PASS |
| III | Typed Errors, One Exit Boundary | Refusals and setup failures raise `PreconditionError`/`OperationFailedError` with the fix in the message | PASS |
| IV | Generated Contracts | No FastAPI model or WS frame changes; artifacts untouched | N/A |
| V | Defensive Variable Access | New/moved Ansible tasks (`host_scripts.yml`, `local_setup.yml`, the `.bashrc` shim) use `\| default()` on every registered var | PASS (checked in review) |
| VI | Test Skip/Fail Paths | Characterization tests pin today's SSH argv before routing; every refusal, missing-prerequisite and malformed-index path is tested | PASS |
| VII | Idempotent & Re-runnable | Local setup second run changes nothing; index writes are atomic; registry writes go through `core/registry.py` | PASS |
| VIII | Docs Reflect Reality | `docs/local.md`, README, CLAUDE.md/AGENTS.md structure + Recent Changes in this change | PASS |
| IX | Pre-Release Off-Index | New playbook and templates are package data: Tier 2 check on the CI-built wheel | PASS |

Post-design re-check: still all PASS — the design adds no provider literal to
core, no generated-contract change, and no new dependency.

## Project Structure

### Documentation (this feature)

```text
specs/029-local-host/
├── plan.md              # this file
├── research.md          # decisions R1–R13
├── data-model.md        # local host entry, project index, prerequisite
├── quickstart.md        # automated, macOS CI and manual acceptance
├── contracts/
│   ├── host-access.md          # SSH vs local transport
│   ├── project-index.md        # projects.tsv + remo-host projects verbs
│   ├── cli-local.md            # remo local {setup,check,add,remove,list}
│   └── registry-local-entry.md # the `local` pseudo-type in registry v2
└── tasks.md             # /speckit-tasks output
```

### Source Code (repository root)

```text
src/remo_cli/
├── core/
│   ├── config.py            # + LOCAL_HOST_TYPE = "local"
│   ├── host_access.py       # NEW: HostAccess protocol, SshAccess, LocalAccess, get_host_access()
│   ├── ssh.py               # shell_connect / check_remote_version route through host_access
│   ├── resume.py            # _ssh_prefix → host_access.command_prefix
│   ├── registry.py          # local pseudo-type: is_known_type, nested "local" object, parse/validate
│   ├── known_hosts.py       # upgrade_command_for(local) → "remo local setup"; guard_not_local_host
│   ├── web_adopt.py         # run_push / build_adoption_payload skip local
│   └── web_sync.py          # _sync_flow local list + parse_remote_registry skip local
├── models/host.py           # KnownHost.projects_dir property (region for type=local)
├── providers/local.py       # NEW: setup, check_prerequisites, add/remove/list via remo-host
├── cli/
│   ├── local.py             # NEW: remo local group (Click only)
│   ├── main.py              # mount remo local
│   ├── shell.py             # upgrade path for local; -L refusal surfaced
│   ├── cp.py                # refuse local
│   └── added.py             # configure refuses local
ansible/
├── local_setup.yml          # NEW: localhost, connection local, become false
└── roles/user_setup/
    ├── tasks/main.yml        # include host_scripts.yml; .bashrc devcontainer block → shim
    ├── tasks/host_scripts.yml # NEW: the shared script-render tasks (remote + local)
    └── templates/
        ├── remo-lib.sh.j2          # NEW: project index resolver + portability helpers
        ├── remo-session-shell.sh.j2 # NEW: devcontainer start-up as zellij session shell
        ├── remo-host.sh.j2         # index verbs, resolver, macOS portability
        ├── project-menu.sh.j2      # index listing, --default-shell launch, portability
        ├── project-launch.sh.j2    # resolver, --default-shell launch, portability
        └── devshell.sh.j2          # resolver, portability
tests/
├── unit/core/test_host_access.py       # NEW
├── unit/providers/test_local.py        # NEW
├── unit/cli/test_local_cmd.py          # NEW
├── unit/test_project_index.py          # NEW (executes rendered scripts)
├── unit/test_local_setup_playbook.py   # NEW (runs local_setup.yml twice in a temp HOME)
└── (existing argv / template / registry / web tests extended)
docs/local.md                # NEW
.github/workflows/ci.yml     # + host-scripts-macos job
```

**Structure Decision**: Single Python project with Ansible-provisioned host
scripts, following the existing `cli/` → `providers/` → `core/` layering. The
local pseudo-type follows the `ssh` pseudo-type's pattern; the host-access
seam is new core infrastructure; all host-side behaviour stays in the shared
templates.

## Phasing

1. **Characterize** today's SSH argv for every host type (tests first).
2. **Host access seam** + route consumers; SSH tests stay green.
3. **`local` pseudo-type** in registry/known_hosts/model + web filters.
4. **Host scripts**: `remo-lib.sh` resolver + index verbs; macOS portability;
   `remo-session-shell` + `--default-shell` launch; `.bashrc` shim.
5. **Shared render tasks** + `local_setup.yml`.
6. **`providers/local.py` + `remo local` CLI** (setup, check, add, remove,
   list) and the refusals.
7. **CI macOS job**, docs, structure diagram, Recent Changes; Tier 2 wheel
   check.

## Complexity Tracking

No constitution violations to justify.
