# Implementation Plan: `remo resume` — Put Each Terminal Tab Back Where It Was

**Branch**: `028-tab-resume` | **Date**: 2026-10-04 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/028-tab-resume/spec.md`

## Summary

`remo shell` starts remembering, per terminal tab, which host it connected to
(and the project, for `-p`), keyed by a salted digest of the terminal's own
tab identifier; it forwards that key to the host over SSH. On the host,
`project-menu` and `project-launch` record which project each key last
attached to, and a new `remo-host sessions lookup` operation answers "which
project did this tab use, and is it still live?". A new `remo resume` command
combines the two: reattach the tab's exact live session with zero prompts,
and otherwise fall back — one explanatory line — to whatever `remo shell`
would have done from the furthest point reachable. See
[research.md](research.md) for every decision and its rationale.

## Technical Context

**Language/Version**: Python 3.11+ (CLI); Bash (host scripts, Ansible
templates); Ansible 2.14+ YAML

**Primary Dependencies**: Click (existing), stdlib `hmac`/`hashlib`/
`secrets`/`fcntl`/`json`/`os`. No new runtime dependency.

**Storage**: Workstation — `<REMO_HOME>/tab-records.json`, `tab-secret`
(0600), `tab-records.lock`. Host — one file per key under
`~/.local/state/remo/tabs/`. No registry schema change.

**Testing**: pytest (`uv run pytest`), including execution tests of the
rendered bash templates with fake `zellij`/`remo-host` binaries.

**Target Platform**: POSIX workstations (macOS, Linux, WSL2); Debian/Ubuntu
hosts configured by remo (every provider + added `ssh` hosts).

**Project Type**: CLI + host-side scripts (configuration via Ansible).

**Performance Goals**: Resume adds at most one SSH round-trip over
`remo shell -p` (SC-004); the lookup is bounded at 5 s (SC-006).

**Constraints**: Raw terminal ids never leave the workstation (FR-002); host
treats the key as untrusted (FR-008); old clients/hosts behave exactly as
today (SC-005); host changes ship only via configure/upgrade (FR-020).

**Scale/Scope**: ≤500 records per store, 30-day retention; a handful of tabs
per user.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Source: `.specify/memory/constitution.md` (v2.1.0).

| # | Principle | Check for this feature | Status |
|---|-----------|------------------------|--------|
| I | Layered Architecture | `cli/resume.py` parses only; logic in `core/tab_identity.py`, `core/tab_records.py`, `core/resume.py`; no Click in `core/`; no `providers/` change; cli → cli reuse of `cli/shell.py`'s connect flow | PASS |
| II | Providers Are Declared | No `host.type` branching; the upgrade-command line reuses `upgrade_command_hint` (descriptor-driven, only the `ssh` pseudo-type named) | PASS |
| III | Typed Errors, One Exit Boundary | Lookup failures arrive as the existing `RemoHostClientError` taxonomy and are converted to fallbacks; no `sys.exit` added outside the existing CLI boundary; every fallback line names the remediation | PASS |
| IV | Generated Contracts | No FastAPI model or WS frame changes; `RemoteCapability.operations` is `list[str]`, so a new op string moves no artifact | N/A |
| V | Defensive Variable Access | Only change is the `copy` content of the existing drop-in task; its registered var is already read with `\| default(false)` | PASS |
| VI | Test Skip/Fail Paths | Every decision-table row, every invalid-key/invalid-project path, store corruption, write failure, lookup timeout/unsupported are tested; `remo shell` refactor pinned by characterization tests first | PASS |
| VII | Idempotent & Re-runnable | Drop-in `copy` is idempotent (second configure: `changed=false`, no sshd restart); records overwrite per key | PASS |
| VIII | Docs Reflect Reality | README (Dev Workflow + CLI reference), protocol contract (`specs/010…/remo-host-protocol.md`), `docs/web-session-interface.md` op list, CLAUDE.md + AGENTS.md structure diagrams updated in this change | PASS |
| IX | Pre-Release Off-Index | No packaging surface touched (no entry points, extras, package data or build config) — Tier 1 suffices | N/A |

Re-check after Phase 1 design: unchanged — all PASS / N/A, no violations.

## Project Structure

### Documentation (this feature)

```text
specs/028-tab-resume/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli-resume.md
│   ├── remo-host-sessions-lookup.md
│   └── ssh-env-forwarding.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── main.py              # + cli.add_command(resume)
│   ├── shell.py             # extract public connect_to_host(); public upgrade_command_hint();
│   │                        #   record + forward the tab key (not for --detach)
│   └── resume.py            # NEW — remo resume [NAME] [-L] [--no-open] [--no-update-check] [--forget|--forget-all]
├── core/
│   ├── tab_identity.py      # NEW — detect_tab_identity(env), derive_tab_key(identity, secret)
│   ├── tab_records.py       # NEW — workstation store: load/record/forget/forget_all, secret, prune, lock
│   ├── resume.py            # NEW — ResumeReason, ResumeDecision, decide_resume() (pure), lookup wrapper (5 s)
│   ├── ssh.py               # shell_connect(..., tab_key=None): -o SendEnv=REMO_TAB_KEY + env
│   └── remo_host_client.py  # + TabLookup, lookup_tab(); build_remo_host_argv branch for sessions lookup
ansible/
├── tasks/configure_dev_tools.yml            # AcceptEnv TZ REMO_TAB_KEY
└── roles/user_setup/templates/
    ├── remo-host.sh.j2                      # + sessions record / sessions lookup; sessions.lookup op;
    │                                        #   shared zellij-state helper; usage text
    ├── project-menu.sh.j2                   # record before zellij attach (guarded)
    └── project-launch.sh.j2                 # record before exec zellij attach (guarded)
tests/
├── unit/core/test_tab_identity.py           # NEW
├── unit/core/test_tab_records.py            # NEW
├── unit/core/test_resume.py                 # NEW — decision table
├── unit/cli/test_resume.py                  # NEW
├── unit/cli/test_shell.py                   # + characterization, recording, forwarding
├── unit/cli/test_main.py                    # EXPECTED_COMMANDS += resume
├── unit/core/test_ssh.py                    # + tab_key forwarding
├── unit/core/test_remo_host_client.py       # + lookup_tab
├── unit/test_ansible_templates.py           # + record/lookup execution, capabilities
├── unit/test_project_launch_tab_record.py   # NEW — project-launch/project-menu recording
└── ansible/test_sshd_accept_env.py          # NEW — drop-in content
```

**Structure Decision**: Single Python package (existing src layout) plus the
existing Ansible tree; four new Python modules, no new top-level directories.

## Phases (for /speckit-tasks)

1. **Characterize** `remo shell` (pin current argv/flow), then extract
   `connect_to_host` and publish `upgrade_command_hint` — no behaviour change.
2. **Workstation core**: `tab_identity`, `tab_records` (+ tests).
3. **Forwarding**: `shell_connect(tab_key=)`; `remo shell` records + forwards.
   (US3 client-side value starts here.)
4. **Host side**: `remo-host sessions record|lookup`, menu/launch hooks,
   `AcceptEnv` (+ execution tests).
5. **Client lookup**: `lookup_tab` in `remo_host_client`.
6. **Resume**: `core/resume.py` decision + `cli/resume.py`, registration.
7. **Docs**: README, protocol contract, web-session doc op list, CLAUDE.md /
   AGENTS.md structure + Active Technologies/Recent Changes.
8. **Follow-up issues** (file, don't fix): consolidate the three
   upgrade-command-hint copies; a shared atomic-JSON-write helper for the
   five private copies.

## Complexity Tracking

No constitution violations — nothing to justify.
