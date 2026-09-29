---

description: "Task list for 026-deacon-default-runtime"
---

# Tasks: Make deacon the Default Devcontainer Runtime

**Input**: Design documents from `/specs/026-deacon-default-runtime/` (spec.md, plan.md, research.md R1–R9, data-model.md E1–E5, contracts/*.md, quickstart.md)

**Tests**: Required (spec SC-001..004; Constitution V/VI). Structural PyYAML tests + rendered-template execution as in the repo's precedents.

## Format: `[ID] [P?] [Story] Description with file path`

## Phase 1: Setup

- [ ] T001 Record the baseline: `uv run pytest -q`, `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli` (counts; pre-existing Docker/live failures listed, not chased).

## Phase 2: Foundational

- [ ] T002 [P] Bump `ansible/roles/deacon/defaults/main.yml` to `deacon_version: "0.4.0"` and rewrite the header comment (stable release; verified flags per research R2). 
- [ ] T003 [P] `tests/ansible/test_deacon_role.py`: pin is stable semver (no `-rc`), the role's regex extracts `0.4.0` from `deacon 0.4.0` and `0.2.0-rc.11` from the old form, the equality check is exact (`0.2.0-rc.1` ≠ `0.2.0-rc.11`), the download URL renders for `x86_64`/`aarch64` with the musl suffix, every registered access uses `| default()`.
- [ ] T004 [P] CLI: `src/remo_cli/core/config.py` `DEVCONTAINER_RUNTIMES = ("auto", "deacon", "devcontainer")`, `DEFAULT_DEVCONTAINER_RUNTIME = "auto"`, comments; `src/remo_cli/core/provider_registry.py` `DEVCONTAINER_RUNTIME` help ("auto (default) picks deacon on new hosts, keeps a host's recorded runtime, and keeps the reference CLI on hosts whose kernel refuses nested overlayfs; deacon; devcontainer (the Node reference CLI)"); `src/remo_cli/core/validation.py` docstring.
- [ ] T005 [P] `tests/unit/providers/test_proxmox_devcontainer_runtime.py`: default → `auto`; explicit `auto` → `auto`; env `auto`; existing deacon/devcontainer/override/invalid cases kept.

## Phase 3: US2 — Rebuild parity (P1)

- [ ] T006 [US2] `ansible/roles/user_setup/templates/remo-host.sh.j2`: add the `REBUILD_SUPPORTED` case list after `DEVCONTAINER_BIN` (contracts/remo-host-capabilities.md); `capabilities` uses it for `rebuild_op`; `cmd_projects_rebuild` checks it (message names `--remove-existing-container`); update the header comment (lines ~14, 92-98, 748-752) to say deacon ≥ 0.4.0 supports it.
- [ ] T007 [US2] `tests/unit/test_ansible_templates.py`: replace `test_remo_host_rebuild_unsupported_with_deacon` with `test_remo_host_rebuild_passes_runtime_check_with_deacon` (render `deacon`; `projects rebuild --project beta` without `--json` → exit 2, stderr mentions `--json`) and `test_remo_host_capabilities_deacon_advertises_rebuild`; add `test_remo_host_rebuild_unsupported_runtime_is_omitted_and_refused` (render `nope` → operations lacks it, `projects rebuild … --json` → exit 4, empty stdout); update `_render_remo_host`'s docstring.

## Phase 4: US1 + US3 — auto resolution, marker, conditional flip (P1)

- [ ] T008 [US1] Create `ansible/tasks/resolve_devcontainer_runtime.yml` per contracts/runtime-resolution.md (rules 1–5, probes with `failed_when: false`/`changed_when: false`, `| default()` everywhere, decision surfaced as a task name).
- [ ] T009 [US1] `ansible/tasks/configure_dev_tools.yml`: `include_tasks: tasks/resolve_devcontainer_runtime.yml` as the first task (before docker/user_setup); gate both runtime `include_role`s on `devcontainer_runtime_effective | default(devcontainer_runtime)` (+ the toggle); add the marker `copy` task right after `include_role: user_setup` (when `configure_devcontainers`); add the nested-overlayfs+deacon warning task after the runtime roles; rewrite the comment block (deacon is the default via `auto`; reference CLI fully supported).
- [ ] T010 [US1] `ansible/roles/user_setup/defaults/main.yml`: `devcontainer_runtime: "auto"`; `devcontainer_cli_bin`/`devcontainer_up_extra_args` keyed on `(devcontainer_runtime_effective | default(devcontainer_runtime)) == 'deacon'`; comment block rewritten (no "experimental").
- [ ] T011 [P] [US1] `ansible/roles/nested_docker/tasks/main.yml`: the "shim SKIPPED" task name/msg says the automatic default keeps the reference CLI on such hosts and that a forced deacon host is unverified (docs link).
- [ ] T012 [P] [US1] `tests/ansible/test_devcontainer_runtime_resolution.py`: structural (resolver is the first task of configure_dev_tools; both runtime roles gated on the effective var and the toggle; marker task after user_setup with owner/mode/content; warning gated on deacon AND nested overlayfs; every `register`ed var in the resolver accessed with `| default(`; no `.rc ==` without default) + rendered rules via jinja2 for: explicit deacon, explicit devcontainer, marker deacon, marker devcontainer, invalid marker + legacy CLI, no marker + legacy CLI, no marker + nested overlayfs, fresh host → deacon; plus `user_setup` defaults render `deacon`/`--trust-workspace-persist` for effective deacon and `devcontainer`/empty for effective devcontainer and for unresolved `auto`.
- [ ] T013 [US1] Syntax-check the five configure playbooks (`cd ansible && uv run ansible-playbook --syntax-check … -e remo_ssh_host=h -e remo_ssh_user=u -e remo_ssh_port=22`) — skip-not-fail if Galaxy collections are missing, like `tests/ansible/test_ssm_connector_role.py`.

## Phase 5: US4 + US5 — Docs (P2/P3)

- [ ] T014 [P] [US4] `docs/nested-overlayfs.md` "The deacon runtime" section: conditional default, the reason, what deacon does (buildx / compose / exec-based updateUID), the candidate environment, "not verified on a real host — issue #TBD", how to force and what to expect.
- [ ] T015 [P] [US5] `docs/proxmox.md`: rename "Experimental: Deacon runtime" → "Devcontainer runtime (deacon by default)"; `auto` resolution order; existing-host stickiness; switching procedure (stop projects → switch explicitly → `.devcontainer-rebuild` → old containers not adopted, listed for removal); mixed runtimes unsupported; keep the known-gaps note as current facts (not "experimental").
- [ ] T016 [P] [US5] `README.md` env-var row (`auto` default; link) and the OrbStack paragraph (one sentence: the automatic choice keeps the reference CLI there); `ansible/README.md` tool list + role table (`deacon` role row; `devcontainers` row says "reference CLI, used on nested-overlayfs and legacy hosts"); `docs/aws.md`/`hetzner.md`/`incus.md`/`proxmox.md` "Available tools" lines unchanged (the toggle name is still `devcontainers`).
- [ ] T017 [US5] `CLAUDE.md` + `AGENTS.md`: ansible subtree gains `tasks/resolve_devcontainer_runtime.yml`; `roles/deacon/` line; Recent Changes entry (3-slot window: move the displaced entry to `docs/feature-history.md`); Active Technologies line. Run `uv run pytest tests/unit/test_docs_structure.py -q`.
- [ ] T018 [P] [US5] `tests/unit/test_docs_runtime_claims.py`: `docs/nested-overlayfs.md` contains "not verified" in the deacon section and names the conditional rule; `docs/proxmox.md` documents `auto`; no doc describes deacon as "experimental" default; `ansible/README.md` lists the `deacon` role.

## Phase 6: Polish

- [ ] T019 `uv run ruff check src/remo_cli tests/unit/providers/test_proxmox_devcontainer_runtime.py tests/ansible tests/unit/test_ansible_templates.py tests/unit/test_docs_runtime_claims.py`; `uv run mypy src/remo_cli`; `uv run pytest -q` (compare with T001).
- [ ] T020 The Ansible greps from quickstart §A return nothing new.
- [ ] T021 `specs/026-deacon-default-runtime/issues.md`: three drafts (research R9) — filed after merge; replace `#TBD` in docs afterwards.
- [ ] T022 Manual gate (SC-005) — not in this run; tracked.

## Dependencies

T002/T004 → T003/T005; T006 → T007; T008 → T009/T010 → T012/T013; docs after the command surface (T004) and resolver (T008) are final.
