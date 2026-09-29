# Implementation Plan: Make deacon the Default Devcontainer Runtime

**Branch**: `026-deacon-default-runtime` | **Date**: 2026-09-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/026-deacon-default-runtime/spec.md`

## Summary

Bump the deacon pin to the stable v0.4.0 (flags verified against the binary), remove the two stale `projects.rebuild` guards in `remo-host` while keeping the omission mechanism as a tested code path, and flip the default runtime to deacon **through a new `auto` value** that resolves on the host: explicit flag > marker > legacy host with the reference CLI > nested-overlayfs host > deacon. The effective runtime is recorded in `~/.remo-devcontainer-runtime` so existing hosts never flip silently; nested-overlayfs hosts stay on the reference CLI (with its shim) until deacon is proven there (tracked). Docs describe all of it and say what is not verified. See [research.md](research.md) R1–R9.

## Technical Context

**Language/Version**: Ansible (ansible-core 2.18–2.19, YAML + Jinja2), Bash templates, Python 3.11+ (click option/default), pytest

**Primary Dependencies**: existing roles `deacon`, `devcontainers`, `user_setup`, `nested_docker`; `tasks/configure_dev_tools.yml`; `core/config.py`, `core/validation.py`, `core/provider_registry.py`, `providers/proxmox.py`

**Storage**: one marker file per host account: `/home/<remo_user>/.remo-devcontainer-runtime` (`deacon`|`devcontainer`)

**Testing**: pytest (template rendering + bash execution as in `test_ansible_templates.py` / `test_devcontainer_shim.py`; PyYAML structural tests as in `tests/ansible/`), ruff, mypy, docs-structure gate

**Target Platform**: Debian/Ubuntu hosts provisioned by remo (all providers + added SSH hosts)

**Project Type**: CLI + Ansible

**Performance Goals**: N/A

**Constraints**: constitution v2.1.0 I–IX; reference-CLI path byte-for-byte unchanged when selected; no remo-host protocol change; no devcontainer lifecycle reimplementation; `| default()` everywhere

**Scale/Scope**: ~8 Ansible files, 3 Python files, 5 docs, ~5 test modules

## Constitution Check

| # | Principle | Check | Status |
|---|-----------|-------|--------|
| I | Layered architecture | Only `core/config.py` constants/default, `core/provider_registry.py` help text, and (no change) `providers/proxmox.py` pass-through; no new cross-layer imports | PASS |
| II | Providers declared | The runtime option is the shared `DEVCONTAINER_RUNTIME` `OptionSpec`; no `host.type` branching | PASS |
| III | Typed errors | `resolve_devcontainer_runtime` keeps raising `click.BadParameter` on bad values; nothing else changes | PASS |
| IV | Generated contracts | untouched | N/A |
| V | Defensive variable access | every new registered access uses `\| default()`; structural test enforces | PASS |
| VI | Test skip/fail paths | resolution branches (5 rules × marker validity), both rebuild branches (supported/unsupported), warning gated both ways, second-run no-op reasoning | PASS |
| VII | Idempotent | marker written by content-compare; deacon role's exact version check; reference-CLI role unchanged | PASS |
| VIII | Docs reflect reality | five docs + both diagrams; "not verified" for the nested-overlayfs deacon path | PASS |
| IX | Pre-release off-index | no packaging surface (Ansible + a default); Tier 1 suffices; stated in the PR | PASS |

**Post-design re-check**: PASS; no Complexity Tracking entries.

## Project Structure

### Documentation (this feature)

```text
specs/026-deacon-default-runtime/
├── plan.md · research.md · data-model.md · quickstart.md · tasks.md
├── contracts/runtime-resolution.md      # the auto→effective rules, the marker, the variables
├── contracts/remo-host-capabilities.md  # operations advertisement + rebuild argv per runtime
└── checklists/requirements.md
```

### Source Code (repository root)

```text
ansible/
├── roles/deacon/defaults/main.yml                 # deacon_version: "0.4.0" (+ comment)
├── tasks/resolve_devcontainer_runtime.yml         # NEW: auto → devcontainer_runtime_effective (+ source), marker/legacy/nested probes
├── tasks/configure_dev_tools.yml                  # include resolver first; role selection on effective; marker write after user_setup; nested-overlayfs+deacon warning
├── roles/user_setup/defaults/main.yml             # devcontainer_runtime: "auto"; cli_bin/up_extra_args keyed on effective
├── roles/user_setup/templates/remo-host.sh.j2     # rebuild_supported case-list; guards removed
├── roles/nested_docker/tasks/main.yml             # SKIPPED message mentions the conditional default
└── README.md                                      # tool list + role table (deacon role, selection rule)
src/remo_cli/core/config.py                        # ("auto","deacon","devcontainer"), default "auto"
src/remo_cli/core/provider_registry.py             # option help
src/remo_cli/core/validation.py                    # docstring only
docs/nested-overlayfs.md · docs/proxmox.md · README.md · CLAUDE.md · AGENTS.md · docs/feature-history.md
tests/
├── unit/test_ansible_templates.py                 # rebuild tests: deacon supported, "nope" omitted
├── ansible/test_devcontainer_runtime_resolution.py  # NEW: structural + rendered rules
├── ansible/test_deacon_role.py                    # NEW: pin/idempotency/URL
├── unit/providers/test_proxmox_devcontainer_runtime.py  # default auto
└── unit/test_docs_runtime_claims.py               # NEW: docs say auto / not verified
```

**Structure Decision**: no new roles; one new shared task file (the resolver) beside `configure_dev_tools.yml`, which every playbook already includes.

## Implementation outline

1. Pin bump + role comment; `tests/ansible/test_deacon_role.py`.
2. `remo-host.sh.j2`: `rebuild_supported` case list; both guards use it; header comment; tests.
3. Resolver task file; `configure_dev_tools.yml` wiring (resolver → user_setup → marker write → runtime roles → nested_docker → warning); `user_setup` defaults; `nested_docker` message; structural + rendered tests.
4. CLI: values/default/help; provider tests; `resolve_devcontainer_runtime` docstring.
5. Docs: five files + diagrams + feature history; docs tests; gates.
6. `issues.md` drafts (filed after merge).
