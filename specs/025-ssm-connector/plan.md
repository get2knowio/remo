# Implementation Plan: SSM Connector — Reach Remo Project Sessions Through AWS Systems Manager

**Branch**: `025-ssm-connector` | **Date**: 2026-09-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/025-ssm-connector/spec.md`

## Summary

Add a transport, orthogonal to compute providers: a **connector** is a host remo already manages, enrolled (from the operator's workstation, via Ansible) as an SSM hybrid-activated managed node that runs SSM Agent, a dedicated non-admin run-as user, and a pinned system-wide remo. A shipped `InteractiveCommands` session document `remo-attach` takes one pattern-restricted parameter (`target` = base64url of `{"v":1,"host","project"}`) and runs `remo connector attach`, which re-validates the decoded names with the CLI's own validators, refuses uid 0 / `ssm-user`, checks a connector-local default-deny exposure file, and `exec`s the same `ssh -tt … remo-host sessions attach` argv the web console builds — moved into `core/attach.py` so it needs no web extra. Failures before `exec` are one machine-parseable `remo-connector-error: <code> <message>` line. Enrollment reads the activation code only from a hidden prompt or stdin and hands it to Ansible through a 0600 vars file on `no_log` tasks. See [research.md](research.md) R1–R15 for every decision and its AWS/code evidence.

## Technical Context

**Language/Version**: Python 3.11+ (CI matrix 3.11/3.12/3.13), Ansible 2.18–2.19 (`ansible-core>=2.18.0,<2.20.0`), Bash (document command), JSON (session document)

**Primary Dependencies**: click, existing `core/ssh`, `core/remo_host_client`, `core/validation`, `core/registry`, `core/ansible_runner`, `core/web_adopt` (keyscan helpers); Ansible collections already pinned (`ansible.posix`, `community.crypto`); **no new Python dependency**. On the connector: SSM Agent (`.deb` from AWS's regional bucket), `uv`, `openssh-client`.

**Storage**: connector-local files under `/var/lib/remo-connector/` (registry v2, exposure JSON, identity, known_hosts, state record); no change to the operator registry or its schema.

**Testing**: pytest (unit for codec/exposure/error line/launcher/enrollment argv; structural PyYAML tests for the role; characterization test pinning the web attach argv before the refactor); existing gates `test_architecture`, `test_docs_structure`, `test_main.EXPECTED_COMMANDS`, ruff, mypy. Live enrollment/attach is a manual gate (SC-008) tracked as an issue.

**Target Platform**: workstation CLI (macOS/Linux) driving Debian/Ubuntu connectors (Proxmox LXC, Hetzner VM); AWS CLI + Session Manager plugin as the client.

**Project Type**: CLI + Ansible (no web/frontend change beyond the wrapper refactor; no generated-contract change)

**Performance Goals**: attach adds no network round-trip before `exec` (no reachability probe — clarification 5); enrollment second run reports zero changes.

**Constraints**: Constitution I–IX; no `host.type` branching; no `remo_cli.web` import on the connector path; registry schema unchanged; `remo shell`, the EC2 SSM path and the `remo-host` protocol version untouched; the activation code never on the workstation command line, in logs, or in Ansible output.

**Scale/Scope**: ~5 new Python modules (`core/attach.py`, `core/connector.py`, `providers/connector.py`, `cli/connector.py`, plus the document JSON), one Ansible role + two playbooks, one docs page, README/CLAUDE.md/AGENTS.md updates, ~10 test modules.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Source: `.specify/memory/constitution.md` (v2.1.0). Mark each row PASS / N/A / VIOLATION. Any VIOLATION must be justified in Complexity Tracking below.

| # | Principle | Check for this feature | Status |
|---|-----------|------------------------|--------|
| I | Layered Architecture | `cli/connector.py` = Click only; `providers/connector.py` = business logic, no Click, no `sys.exit`, returns int rc / raises `core/errors`; `core/attach.py` + `core/connector.py` know no provider. No `core → web` import (the web wrapper calls core, not the reverse). Architecture test allowlists stay empty (FR-023). | PASS |
| II | Providers Are Declared | The connector is not a provider: no descriptor, no `host.type` literal; the connector host and the exposed hosts are reached through `core/ssh.build_ssh_opts` (descriptor-driven proxy hook) and materialized as `ssh`-type registry entries. | PASS |
| III | Typed Errors, One Exit Boundary | All commands use `provider_command`; the launcher prints the contract error line and **returns 1** (int → `sys.exit` inside the wrapper), so no second translation boundary is added. Messages name the fix. | PASS |
| IV | Generated Contracts | No FastAPI model or WS frame changes; the four artifacts are untouched. The `remo-attach` document is a hand-authored, versioned **external** contract (owned here, consumed by others), tested for agreement with the code constants. | PASS (N/A to generation) |
| V | Defensive Variable Access | Every registered access in the new role uses `\| default()`; structural test enforces it (contracts/ansible-role.md). | PASS |
| VI | Test Skip/Fail Paths | Every refusal code has a test; every `when:` in the role has both branches enumerated in the contract; the web attach argv is pinned by a characterization test **before** the move to core. | PASS |
| VII | Idempotent & Re-runnable | Enrollment converges (guards on registration file, version probe, content-compare writes, `authorized_key`); unenroll confirms (`--yes`); the connector registry file is produced by `core/registry` serializers, never composed by hand in YAML. | PASS |
| VIII | Docs Reflect Reality | `docs/ssm-connector.md` (new), README, CLAUDE.md + AGENTS.md structure diagrams (both gated), `docs/aws.md` cross-link; the docs state what the manual gate has not yet verified rather than claiming it. | PASS |
| IX | Pre-Release Off-Index | New package data + command group = packaging surface: validated at Tier 2 (`dev-build.yml`) before the tagged release that ships document v1; `--remo-source` gives Tier 1 for the connector install. Nothing non-final reaches PyPI. | PASS |

**Post-design re-check (after Phase 1)**: unchanged — PASS on all rows. One deliberate scope statement rather than a violation: FR-016's "never in argv" is scoped to the workstation and the automation transcript, because AWS's `amazon-ssm-agent -register` takes the code as an argument on the connector (research R6); the spec's FR-016 was amended to say so.

## Project Structure

### Documentation (this feature)

```text
specs/025-ssm-connector/
├── plan.md              # This file
├── research.md          # R1–R15
├── data-model.md        # E1–E7 + state transitions
├── quickstart.md        # Gates, local launcher checks, enrollment, manual gate
├── contracts/
│   ├── session-document.md   # remo-attach v1 (the external contract)
│   ├── cli.md                # remo connector enroll|attach|document|status|unenroll
│   └── ansible-role.md       # playbooks + role tasks with idempotency and conditional paths
├── checklists/requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── main.py                 # + cli.add_command(connector)
│   └── connector.py            # NEW: remo connector group (Click only; lazy imports; provider_command)
├── providers/
│   └── connector.py            # NEW: enroll/attach/document/status/unenroll business logic (no Click, no sys.exit)
├── core/
│   ├── attach.py               # NEW: build_attach_argv() — the shared ssh -tt … remo-host sessions attach builder
│   ├── connector.py            # NEW: contract constants, TargetV1 codec, exposure, ErrorCode/format_error_line,
│   │                           #      ConnectorState, document loader, connector argv (state-dir paths)
│   └── remo_attach_document.json   # NEW: the shipped session document (package data)
└── web/
    └── terminal.py             # build_attach_argv() becomes a thin wrapper over core/attach.py (same name/signature)

ansible/
├── ssm_connector_enroll.yml    # NEW: 3 plays (inventory → connector role → authorize key on exposed hosts)
├── ssm_connector_unenroll.yml  # NEW
└── roles/ssm_connector/        # NEW: defaults/main.yml, tasks/main.yml, handlers/main.yml

docs/ssm-connector.md           # NEW (FR-026/027)
README.md                       # SSM connector section + CLI reference lines
CLAUDE.md, AGENTS.md            # Project Structure entries (gated) + ansible subtree + Commands + Recent Changes

tests/
├── unit/core/test_connector_target.py      # codec, hostile corpus, pattern, caps, v rejection
├── unit/core/test_connector_exposure.py    # default deny, matching, unreadable
├── unit/core/test_connector_document.py    # file ↔ constants, single parameter, command shape, importlib.resources
├── unit/core/test_attach_builder.py        # core builder; web wrapper characterization + parity; launcher argv
├── unit/providers/test_connector_attach.py # refusals (uid0, ssm-user, each code), one line, rc 1, exec argv
├── unit/providers/test_connector_enroll.py # pre-flight errors; vars file 0600+deleted; code absent from argv;
│                                           #   stdin vs prompt; self-target → localhost; SSM-mode host refused
├── unit/cli/test_connector_cmd.py          # group wiring, no --activation-code option, --remo-version/--remo-source exclusivity
├── unit/cli/test_main.py                   # EXPECTED_COMMANDS += "connector"
└── ansible/test_ssm_connector_role.py      # structural: no_log, defaults, guards, idempotent modules, playbook plays
```

**Structure Decision**: single Python project + Ansible tree, exactly the existing layout. New logic lands in the three existing layers with one new module each (plus `core/attach.py` for the shared builder), one new role, two new playbooks. No new package, no new top-level directory.

## Implementation outline (feeds /speckit-tasks)

1. **Characterize, then move the attach builder**: pin `web/terminal.build_attach_argv` argv in a test; add `core/attach.py`; make the web function a wrapper; run the web/terminal/parity suites unchanged.
2. **Contract constants + document**: `core/connector.py` constants; `remo_attach_document.json`; `remo connector document`; document test.
3. **Target codec + exposure + error line** (`core/connector.py`), with the hostile corpus test.
4. **Launcher** (`providers/connector.attach`): run-as check, ssh present, decode/validate, state dir (`REMO_HOME` override), registry lookup without exit, exposure, `execvp`; tests monkeypatch `os.geteuid`, `getpass.getuser`, `shutil.which`, `os.execvp`.
5. **CLI group** (`cli/connector.py`, `main.py` registration, `EXPECTED_COMMANDS`).
6. **Enrollment** (`providers/connector.enroll`): pre-flight, code input, vars file, inventory vars from `build_ssh_opts`, registry JSON via `core/registry` serializers, playbook run, summary; `status`; `unenroll`.
7. **Ansible**: role + playbooks per contracts/ansible-role.md; structural tests; `ansible-playbook --syntax-check` gated test like `test_remo_host_idempotency.py`.
8. **Docs**: `docs/ssm-connector.md`, README, CLAUDE.md/AGENTS.md (structure + commands + recent changes), `docs/aws.md` cross-link; docs-structure gate.
9. **Gates**: ruff, mypy, full pytest; web extra absent import check; Tier 2 note for the PR.
10. **Deferred issues** (R15) drafted in tasks.md; filed when the branch is pushed.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

None. (The connector's private registry is written by Ansible as a whole file, but its content is produced by `core/registry`'s serializer on the workstation, so schema ownership stays with `registry.py`; the operator's registry is never written by this feature.)
