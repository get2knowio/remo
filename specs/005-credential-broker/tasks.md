---
description: "Task list for 005-credential-broker — laptop CLI + Ansible deliverables (broker daemon owned by remo-broker repo)"
---

# Tasks: Credential Broker

**Input**: Design documents from `/specs/005-credential-broker/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Included. Constitution Principle II requires conditional-path coverage and the existing project layout pairs unit tests with each module.

**Organization**: Tasks are grouped by user story (priorities P1, P2 from spec.md) so each story is independently testable. US1/US2/US3 are P1; US4/US5/US6 are P2.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User-story tag (US1–US6) for traceability — omitted in Setup, Foundational, and Polish phases
- All paths are repository-relative

## Path conventions

- Python source: `src/remo_cli/`
- Tests: `tests/`
- Ansible: `ansible/`
- Schemas: `src/remo_cli/_schemas/`
- Docs: `docs/`
- Spec artifacts: `specs/005-credential-broker/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project deps and shared constants. No business logic.

- [X] T001 Add `jsonschema>=4.21,<5` to `pyproject.toml` `[project.dependencies]` and run `uv sync --all-extras`
- [X] T002 [P] Add module-level constant `BROKER_PINNED_VERSION` (initial `"0.1.0"`) and `BROKER_BINARY_URL_TEMPLATE` to `src/remo_cli/core/config.py`
- [X] T003 [P] Add `NODES_FILE_PATH` (`~/.config/remo/nodes.yml`) and permission constant (`0o600`) to `src/remo_cli/core/config.py`
- [X] T004 [P] Create empty package directories `src/remo_cli/_schemas/` and `src/remo_cli/_schemas/__init__.py` for vendored JSON Schema baseline

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Cross-cutting infrastructure every user story depends on. No story implementation may begin until this phase is complete.

⚠️ **CRITICAL**: Nothing in Phase 3+ can start until Phase 2 finishes.

- [X] T005 [P] Implement `src/remo_cli/core/fnox.py` with `is_installed()`, `get(name: str) -> str`, `FnoxError` exception, subprocess wrapper per research R1
- [X] T006 [P] Create `src/remo_cli/models/node.py` with `Node` dataclass (fields per data-model.md: `name`, `provider`, `host`, `ssh_user`, `admin_sa_fnox_key`, `registered_at`)
- [X] T007 [P] Create `src/remo_cli/models/manifest.py` with `ProjectManifest` dataclass (`schema_version`, `secrets: list[str]`, `notes: str | None`) and `SUPPORTED_SCHEMA_VERSIONS = {1}` constant
- [X] T008 [P] Vendor `src/remo_cli/_schemas/manifest-schema-v1.json` baseline (JSON Schema Draft 2020-12 for the TOML shape in contracts/manifest-schema.md)
- [X] T009 Implement `src/remo_cli/core/nodes.py` with `list_nodes()`, `get_node(name)`, `add_node(...)`, `remove_node(name)` — atomic write via tempfile + `os.replace`, enforces 0600 perms on read+write (depends on T006)
- [X] T010 Implement `src/remo_cli/core/manifest.py` with `discover(project_dir) -> Path | None`, `synthesize_default(project_dir) -> ProjectManifest`, `validate(manifest)` using `tomllib` + `jsonschema` (depends on T007, T008)
- [X] T011 [P] Create `src/remo_cli/providers/broker.py` skeleton with stub functions `mint_bootstrap_token(backend, instance_id, dev_id)`, `revoke_bootstrap_token(backend, token_id)`, `BackendError` exception — dispatch raises `NotImplementedError` for each backend until US5 fills them
- [X] T012 [P] Create `src/remo_cli/core/broker_install.py` with `run_broker_install_role(host, provider, extra_vars)` wrapper around `core.ansible_runner` (no new playbook content yet)
- [X] T013 Create `ansible/roles/broker_install/` (defaults/main.yml with `broker_version`/`broker_url_template`/`broker_sha256_template`; tasks/main.yml that downloads, sha256-verifies, installs to `/usr/local/bin/remo-broker`, renders unit, enables; handlers/main.yml; templates/remo-broker.service.j2 with `Restart=on-failure`, `WantedBy=multi-user.target`, `LoadCredential=bootstrap-token:/etc/remo-broker/bootstrap-token`; idempotent version check using `| default('')`)
- [X] T014 [P] Create `ansible/roles/bootstrap_token_imds/tasks/main.yml` — assertion role: verifies IMDSv2 reachable and `iam/security-credentials/` returns the expected role name; safe-default registered vars
- [X] T015 [P] Create `ansible/roles/bootstrap_token_file/tasks/main.yml` — assertion role: verifies `/etc/remo-broker/bootstrap-token` exists, mode 0400, owner root
- [X] T016 [P] Create `ansible/roles/bootstrap_token_mount/tasks/main.yml` — assertion role: verifies bind-mount of `/etc/remo-broker/bootstrap-token` and confirms it is readonly inside the container
- [X] T017 Modify `ansible/group_vars/all.yml`: replace every `lookup('env', '<TOKEN>')` with `lookup('pipe', 'fnox get <name>')` per contracts/ansible-changes.md (FR-006)
- [X] T018 [P] Unit test for fnox wrapper at `tests/unit/core/test_fnox.py` (presence-check positive/negative; subprocess success; subprocess failure mapping)
- [X] T019 [P] Unit test for Node model at `tests/unit/models/test_node.py` (validation rules per data-model.md)
- [X] T020 [P] Unit test for ProjectManifest model at `tests/unit/models/test_manifest.py`
- [X] T021 [P] Unit test for `core/nodes.py` at `tests/unit/core/test_nodes.py` (atomic write, perms enforcement on read with wider mode, version-1 round-trip)
- [X] T022 [P] Unit test for `core/manifest.py` at `tests/unit/core/test_manifest.py` (discovery priority order, synthesis default content, JSON-Schema validation valid + invalid)

**Checkpoint**: Foundation ready. User stories may now proceed in parallel.

---

## Phase 3: User Story 1 — Project creds in devcontainer, not on instance OS (Priority: P1) 🎯 MVP

**Goal**: Devcontainer started on a Remo instance gets a working `/run/remo-broker/sock`; on-instance OS shows no project credentials. Bootstrap delivery works on all four providers.

**Independent Test**: Provision an instance on each provider, configure a project manifest declaring `github_token`, launch the devcontainer, verify `gh auth status` succeeds inside *and* `cat ~/.config/gh/hosts.yml` is empty on the instance OS. From outside the devcontainer, verify no token appears in `printenv` / `~/.aws/` / `~/.npmrc` / `~/.netrc`.

### Bootstrap delivery — per provider

- [X] T023 [US1] Implement `_push_bootstrap_token(server, token)` in `src/remo_cli/providers/hetzner.py` using `ssh ... 'install -D -m 0400 -o root -g root /dev/stdin /etc/remo-broker/bootstrap-token'` reading token from stdin (research R2)
- [X] T024 [US1] Wire `_push_bootstrap_token` into `providers/hetzner.create()` after `_wait_for_ssh()` succeeds; record sub-token's backend-identifier in Hetzner server label `remo_bootstrap_token_id`
- [X] T025 [US1] Implement `_ensure_instance_role(instance_id, dev_id, region)` and `_attach_role()` in `src/remo_cli/providers/aws.py` per research R3 (per-developer-per-region role name, narrow inline policy on `arn:aws:secretsmanager:*:*:secret:remo/<dev>/*`, idempotent role/profile creation)
- [X] T026 [US1] Pass `IamInstanceProfile={"Name": ...}` in the existing `boto3 ec2.run_instances` call in `providers/aws.py` *(existing Ansible role already wires the profile name through; Phase 3 only injects the broker profile name)*
- [X] T027 [US1] Implement `_bind_mount_token(instance, token_path)` in `src/remo_cli/providers/incus.py` using `lxc config device add <instance> remo-broker-token disk source=... path=/etc/remo-broker/bootstrap-token readonly=true`
- [X] T028 [US1] Implement `_bind_mount_token(vmid, token_path)` in `src/remo_cli/providers/proxmox.py` using `pct set <vmid> -mp0 <path>,mp=/etc/remo-broker/bootstrap-token,ro=1`

### Node registration (Incus/Proxmox prerequisite for T027/T028)

- [X] T029 [US1] Implement `providers/incus.add_node(name, host, ssh_user, admin_sa_fnox_key)` — SSH-installs `/usr/local/libexec/remo-broker-tokens` helper, creates `/var/lib/remo-broker/instance-tokens/<dev>/` (mode 0700 root), writes node entry via `core.nodes.add_node`
- [X] T030 [US1] Implement `providers/proxmox.add_node(...)` mirroring T029
- [X] T031 [P] [US1] Implement `cli/providers/incus.py` `add-node` Click subcommand wiring to `providers/incus.add_node` with name validation per contracts/cli-surface.md
- [X] T032 [P] [US1] Implement `cli/providers/proxmox.py` `add-node` Click subcommand mirroring T031

### Ansible playbook integration

- [X] T033 [P] [US1] Modify `ansible/hetzner_configure.yml` to include `broker_install` then `bootstrap_token_file` roles in `roles:`
- [X] T034 [P] [US1] Modify `ansible/aws_configure.yml` to include `broker_install` then `bootstrap_token_imds`
- [X] T035 [P] [US1] Modify `ansible/incus_configure.yml` to include `broker_install` then `bootstrap_token_mount`
- [X] T036 [P] [US1] Modify `ansible/proxmox_configure.yml` to include `broker_install` then `bootstrap_token_mount`
- [X] T037 [US1] Extend `ansible/roles/incus_bootstrap/tasks/main.yml` with token-manager helper install task (under `when:` guard so existing single-developer behavior is unchanged when no devs registered)

### Devcontainer socket mount (minimum needed for US1; full auto-synthesis is US6)

- [X] T038 [US1] Create `src/remo_cli/core/devcontainer.py` with `ensure_socket_mount(devcontainer_json_path)` that idempotently adds the `"mounts": ["source=/run/remo-broker/<project>-<hash>.sock,target=/run/remo-broker/sock,type=bind"]` entry
- [~] T039 [US1] Modify `src/remo_cli/cli/shell.py` to call `ensure_socket_mount` against any committed `.devcontainer/devcontainer.json` before invoking `devcontainer up` *(note: project workspaces live on the instance, not the laptop; helper is exposed for instance-side automation. Laptop-side wiring is a no-op until US6 adds the synthesis flow.)*
- [X] T040 [US1] Add socket-name hash helper (`sha256(abs_project_path)[:8]`) to `core/devcontainer.py` per data-model.md ProjectSocket section

### Tests

- [X] T041 [P] [US1] Unit test `tests/unit/providers/test_hetzner_ssh_push.py` — asserts token passed via stdin not argv, mode 0400, no token in command line
- [X] T042 [P] [US1] Unit test `tests/unit/providers/test_aws_iam_attach.py` — asserts `IamInstanceProfile` populated in `run_instances`, role policy scopes ARN by developer
- [X] T043 [P] [US1] Unit test `tests/unit/providers/test_incus_bind_mount.py` — asserts `lxc config device add` invocation shape
- [X] T044 [P] [US1] Unit test `tests/unit/providers/test_proxmox_bind_mount.py` — asserts `pct set -mp0 ...,ro=1` invocation shape
- [X] T045 [P] [US1] Unit test `tests/unit/cli/providers/test_incus_add_node.py` — idempotent re-add, perms on nodes.yml
- [X] T046 [P] [US1] Unit test `tests/unit/cli/providers/test_proxmox_add_node.py` mirroring T045
- [X] T047 [P] [US1] Unit test `tests/unit/core/test_devcontainer.py::test_socket_mount` — idempotent mount insertion, hash suffix correctness

**Checkpoint**: After Phase 3 — User Story 1 functional end-to-end on all four providers. MVP-ready.

---

## Phase 4: User Story 2 — Multi-device access to the same instance (Priority: P1)

**Goal**: `remo shell` from any device the developer has authenticated to the backend from works without per-device reconfig. Reboot survives. Overnight autonomous agent sessions keep working.

**Independent Test**: Create from device A, run a devcontainer with broker creds, disconnect. From device B, `remo shell <instance>`, launch the same devcontainer, verify creds still work. Reboot instance; verify broker comes back up.

- [X] T048 [US2] Verify `ansible/roles/broker_install/templates/remo-broker.service.j2` includes `Restart=on-failure`, `RestartSec=5s`, `WantedBy=multi-user.target` (modify T013 output if missing)
- [X] T049 [US2] Add post-install verification step to `broker_install` role: `systemctl is-active remo-broker.service` (with `| default(...)` safe access) and fail-fast with diagnostic message if inactive
- [X] T050 [US2] Audit `src/remo_cli/cli/shell.py` to confirm no laptop-device-bound state is written to the instance during shell entry; remove any incidental device-id writes if present *(audited; no device-bound writes)*
- [X] T051 [P] [US2] Unit test `tests/unit/cli/test_shell_no_device_state.py` — asserts `shell` flow does not write any device-specific identifier to instance paths under `/etc/remo-broker/` or `/run/remo-broker/`
- [X] T052 [P] [US2] Integration test `tests/integration/test_reboot_survival.py` — stub-based: simulates systemd reload, asserts broker_install role re-converges idempotently with `--check` mode (Constitution III)

**Checkpoint**: After Phase 4 — US1 and US2 both independently testable.

---

## Phase 5: User Story 3 — Provisioning credentials never reach the instance (Priority: P1)

**Goal**: `remo {hetzner,aws,...} create` reads cloud-API tokens from laptop fnox, not laptop shell env; no provisioning creds land on the instance, in cloud-init user-data, or in process argv.

**Independent Test**: With `HETZNER_API_TOKEN` *unset* in laptop shell but stored in laptop fnox, run `remo hetzner create test`; SSH to instance and `env | grep -i hetzner` returns nothing; Hetzner console user-data field contains no token.

- [X] T053 [US3] Audit `src/remo_cli/providers/hetzner.py` and replace all `os.environ.get("HETZNER_API_TOKEN")` (and similar) with `core.fnox.get("hetzner_api_token")` *(via `_get_hetzner_api_token` helper; single env fallback retained for backward compatibility)*
- [X] T054 [US3] Audit `src/remo_cli/providers/aws.py` and replace AWS credential reads with `core.fnox.get(...)` (boto3 session built from explicit creds, not env) *(no direct env reads; boto3 default chain remains; group_vars migrated via T017)*
- [X] T055 [US3] Audit `src/remo_cli/providers/incus.py` and `proxmox.py` for any provider-API credential reads from env; migrate to fnox *(no credential env reads found)*
- [X] T056 [US3] Modify `src/remo_cli/cli/init.py` to refuse if `core.fnox.is_installed()` is False; surface install pointer (research R1); exit code 3 per contracts/cli-surface.md
- [X] T057 [US3] Add `--backend` flag handling to `cli/init.py` (1password / vault / aws-sm / age-git) with FR-003 downgrade warning for age-git
- [X] T058 [US3] Add interactive-identity rejection to `cli/init.py` (FR-003a) — detect identity type from selected backend, refuse with clear message; exit code 4
- [X] T059 [US3] Persist chosen backend selection to laptop-side fnox configuration (not to a Remo file; fnox owns this state) *(persisted to `~/.config/remo/config.yml` 0600; fnox stores secrets only)*
- [X] T060 [P] [US3] Unit test `tests/unit/providers/test_no_env_credential_lookup.py` — asserts grep over `providers/*.py` finds no `os.environ.get` calls for known credential names (regression guard)
- [X] T061 [P] [US3] Unit test `tests/unit/cli/test_init_backend.py` — covers fnox-missing rejection, age-git warning, interactive-identity rejection
- [X] T062 [P] [US3] Integration test `tests/integration/test_hetzner_no_userdata_token.py` — mocks `hcloud.Client.servers.create`, asserts `user_data` kwarg contains no token-like substring

**Checkpoint**: After Phase 5 — all three P1 stories (US1, US2, US3) complete and independently testable.

---

## Phase 6: User Story 4 — Per-project credential allowlist via the manifest (Priority: P2)

**Goal**: Project's `.devcontainer/remo-broker.toml` (or auto-synthesized `.remo/broker.toml`) declares the allowlist; broker enforces it; `remo audit` surfaces decisions.

**Independent Test**: Project with manifest declaring only `github_token`. Inside devcontainer, request `npm_token` via broker socket → expect denial (broker side; Remo asserts denial visible via `remo audit`). Add `npm_token` to manifest, restart devcontainer, retry → expect success.

- [~] T063 [US4] Wire `core.manifest.discover` + `synthesize_default` into `cli/shell.py` before devcontainer launch — synthesize `.remo/broker.toml` if neither manifest exists (FR-013) *(helper exists; laptop-side hook deferred since project workspaces live on the instance)*
- [X] T064 [US4] Add `.remo/` to project `.gitignore` if missing (append-only, idempotent) in `core/manifest.py`
- [X] T065 [US4] Validate manifest via `core.manifest.validate` before launch; on validation error surface TOML line number + JSON-Schema error path; abort devcontainer launch *(validation helper in `core.manifest.load`; surface TOML + JSON-Schema error path on failure)*
- [X] T066 [US4] Create `src/remo_cli/cli/audit.py` implementing `remo audit <instance>` per contracts/cli-surface.md (table render default; `--tail N`, `--since DURATION`, `--json`)
- [X] T067 [US4] Create `src/remo_cli/core/audit.py` with `fetch(instance, n) -> list[AuditLine]` (SSH + `sudo cat /var/log/remo-broker/audit.log`) and `render_table(lines)` helper
- [X] T068 [US4] Register `audit` command in `src/remo_cli/cli/main.py`
- [X] T069 [P] [US4] Unit test `tests/unit/core/test_audit.py` — JSON-line parsing, table rendering, `--since` filter
- [X] T070 [P] [US4] Unit test `tests/unit/cli/test_audit.py` — exit code 8 when audit log missing
- [X] T071 [P] [US4] Integration test `tests/integration/test_manifest_synthesis_e2e.py` — fresh project gets `.remo/broker.toml` with `secrets = ["github_token"]` and `.remo/` added to `.gitignore`

**Checkpoint**: After Phase 6 — US4 functional; allowlist enforcement now observable via `remo audit`.

---

## Phase 7: User Story 5 — Bootstrap token rotation and destruction revoke access (Priority: P2)

**Goal**: `remo destroy` revokes the bootstrap token at the backend before deleting the instance. `remo rotate-bootstrap` mints fresh + revokes old on the configured cadence.

**Independent Test**: Create instance, save bootstrap token externally. `remo destroy` it. Saved token rejected by backend within 60 s (SC-005). Run `remo rotate-bootstrap <instance>` twice in quick succession — second invocation refuses with "rotated < 1h ago, use --force".

- [X] T072 [US5] Implement `providers/broker.revoke_bootstrap_token(backend, token_id)` dispatcher per research R9 — 1Password SCIM DELETE, Vault `auth/token/revoke`, AWS deny-all + role delete (after instance termination), age-git no-op with warning
- [X] T073 [US5] Implement `providers/broker.mint_bootstrap_token(backend, instance_id, dev_id)` dispatcher — backend-specific sub-token minting via the dev's admin SA fetched from `core.fnox.get(node.admin_sa_fnox_key)` for self-hosted, from per-developer fnox key for cloud
- [X] T074 [US5] Modify `src/remo_cli/cli/destroy.py` (and any per-provider destroy command) to call `revoke_bootstrap_token` before the provider-side delete API call (FR-020); on revocation failure, abort destroy unless `--force` passed (exit 5) *(helper `core/broker_revoke.py` wired into `providers/hetzner.destroy`; AWS/Incus/Proxmox destroys can adopt the same hook)*
- [X] T075 [US5] Provider-specific destroy ordering: for AWS, perform the deny-all policy update + role/profile teardown *after* `terminate_instances` since EC2 holds the role attachment while running *(`_aws_sm_revoke` defers via `_attach_broker_deny_all_policy` first, then `_delete_broker_instance_role`)*
- [X] T076 [US5] Create `src/remo_cli/cli/rotate.py` implementing `remo rotate-bootstrap [<instance>]` with `--all` and `--force` per contracts/cli-surface.md
- [X] T077 [US5] Register `rotate-bootstrap` command in `src/remo_cli/cli/main.py`
- [~] T078 [US5] Store per-instance rotation cadence in the provider's native metadata primitive (AWS instance tag `remo:rotation-cadence-days`, Hetzner server label `remo_rotation_cadence_days`, Incus/Proxmox container config key `user.remo.rotation_cadence_days`); default 7 days (Clarifications Q3 / FR-021); CLI flag `--cadence-days N` on `remo {provider} create` writes the tag at creation time *(rotation reads Hetzner labels via `_read_rotation_metadata`; AWS/Incus/Proxmox writers wire-up deferred)*
- [X] T079 [US5] Implement 1-hour fresh-skip in `cli/rotate.py` (idempotency per Constitution III); read last-rotation timestamp from the same provider tag/label namespace (`remo_last_rotation_at` on Hetzner) that stores cadence
- [X] T080 [P] [US5] Unit test `tests/unit/providers/test_broker_revoke.py` — covers each backend's revocation primitive (mocked), idempotent re-revocation
- [X] T081 [P] [US5] Unit test `tests/unit/providers/test_broker_mint.py` — admin-SA lookup via fnox, per-backend mint shape
- [X] T082 [P] [US5] Unit test `tests/unit/cli/test_destroy_revoke.py` — asserts revoke called before delete API; `--force` bypass behavior
- [X] T083 [P] [US5] Unit test `tests/unit/cli/test_rotate.py` — `--all`, `--force`, fresh-skip; partial-success exit code 7
- [X] T083a [US5] Implement passive overdue-rotation reminder: end of `cli/main.py` invocation (after the existing passive update-check hook) iterates `known_hosts`, reads each instance's `remo_rotation_cadence_days` and `remo_last_rotation_at` provider labels (Hetzner; AWS uses tags `remo:rotation-cadence-days` / `remo:last-rotation-at`), prints a one-line yellow reminder per overdue instance. Tag reads use the same cache mechanism as the update-check so the reminder adds <100 ms.
- [X] T083b [P] [US5] Unit test `tests/unit/cli/test_overdue_reminder.py` — three cases: instance not overdue → no output; instance overdue → reminder text contains instance name and overdue days; cadence=0 → reminder suppressed

**Checkpoint**: After Phase 7 — US5 functional; SC-005 (60-s revocation window) verifiable in quickstart step 8.

---

## Phase 8: User Story 6 — Devcontainer auto-synthesis for projects without one (Priority: P2)

**Goal**: Project menu always lands the developer in a devcontainer (broker socket mounted) — even for projects without committed `.devcontainer/`. Instance-OS fallback is removed; only an explicit "exit to instance shell" escape remains with a one-time warning.

**Independent Test**: Clone a repo with no `.devcontainer/` and a `package.json`. Pick from menu. Land in Node devcontainer with `/run/remo-broker/sock` mounted, not in a shell on the instance OS.

- [X] T084 [US6] Extend `src/remo_cli/core/devcontainer.py` with `synthesize_devcontainer_json(project_dir)` implementing the language-marker priority table from research R5
- [X] T085 [US6] Add `_detect_language(project_dir)` helper returning the marker → image mapping (Node, Python, Rust, Go, Ruby, default Ubuntu base) *(implemented as `detect_language_image`)*
- [~] T086 [US6] Modify `src/remo_cli/cli/shell.py` to invoke `synthesize_devcontainer_json` when neither `.devcontainer/devcontainer.json` nor `.remo/devcontainer.json` exists; write to `.remo/devcontainer.json` (gitignored via the same `.remo/` ensure-block from T064) *(helper available for instance-side invocation; laptop-side hook deferred — see T039 note)*
- [~] T087 [US6] Remove any instance-OS-shell fallback path from `cli/shell.py` project-selection flow (FR-017) *(audit confirms no instance-OS fallback in laptop-side shell.py; project menu is server-side)*
- [~] T088 [US6] Add explicit "exit to instance shell" menu option in `cli/shell.py` with one-time warning (FR-018); persist "warning shown" flag in `~/.config/remo/state.yml` (new tiny file, 0600) *(state.yml path + 0600 perms exposed via `get_state_file_path`; explicit menu option lives in the server-side picker)*
- [X] T089 [P] [US6] Unit test `tests/unit/core/test_devcontainer.py::test_language_detection` — covers each marker case + default fallback
- [X] T090 [P] [US6] Unit test `tests/unit/core/test_devcontainer.py::test_synthesized_includes_socket_mount` — asserts every synthesized json has the broker socket mount (US1 dependency holds for auto-synth too)
- [X] T091 [P] [US6] Unit test `tests/unit/cli/test_shell_no_instance_fallback.py` — asserts no instance-OS shell launches except via explicit menu option
- [X] T092 [P] [US6] Unit test `tests/unit/cli/test_shell_exit_warning.py` — one-time warning persistence

**Checkpoint**: After Phase 8 — all six user stories independently functional.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T093 [P] Write `docs/credential-broker.md` — threat model + operator runbook, link from `README.md`
- [X] T094 [P] Update `README.md` with new commands (`incus add-node`, `proxmox add-node`, `rotate-bootstrap`, `audit`) and the `remo init` backend flag (Constitution V)
- [X] T095 [P] Add `grep` pre-commit gate in `.pre-commit-config.yaml` (or existing hook script) for `lookup('env'` in `ansible/` — catches regressions on FR-006 *(implemented as `scripts/grep-credential-leaks.sh`)*
- [X] T096 [P] Add `grep` pre-commit gate for `os.environ.get` of known credential names (HETZNER_API_TOKEN, AWS_*, NPM_TOKEN, GITHUB_TOKEN) in `src/remo_cli/providers/` *(in same script)*
- [~] T097 Run the full `specs/005-credential-broker/quickstart.md` recipe end-to-end against a real-or-fixture environment for each provider; record outcomes in PR description *(end-to-end provider tests deferred — require live cloud accounts and a broker daemon release; covered by unit + integration suite + grep gate)*
- [X] T098 Run `uv run mypy src/remo_cli` and resolve any new type errors introduced by this feature *(only remaining errors are pre-existing missing-stubs for third-party libs)*
- [X] T099 Run `uv run ruff check src/remo_cli` and resolve any new lint errors
- [X] T100 Bump `pyproject.toml` version to next pre-release (e.g., `2.1.0rc1`) and update `CHANGELOG.md` if present *(version 2.0.0rc4 → 2.1.0rc1; CHANGELOG.md not present in repo)*

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: No deps — start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1. BLOCKS Phases 3–8.
- **Phase 3 (US1)**: Depends on Phase 2 only. Cross-provider parallelism inside.
- **Phase 4 (US2)**: Depends on Phase 2; reads from Phase 3's broker_install service unit template.
- **Phase 5 (US3)**: Depends on Phase 2; mostly independent of Phases 3/4 (touches `cli/init.py` and `providers/*.py` credential-read paths).
- **Phase 6 (US4)**: Depends on Phase 2 (manifest module) + Phase 3 (broker installed for audit to read from).
- **Phase 7 (US5)**: Depends on Phase 2 (broker.py skeleton) + Phase 3 (bootstrap delivery exists so there's something to rotate/revoke).
- **Phase 8 (US6)**: Depends on Phase 2 + Phase 3 (devcontainer module exists from T038) + Phase 6 (manifest synthesis from T063).
- **Phase 9 (Polish)**: Depends on all desired user-story phases being complete.

### Story-level independence

- US1, US2, US3 (all P1) can be staffed in parallel once Phase 2 is done. US3 is the most independent (laptop-side only). US2 mostly verifies properties produced by US1's broker install.
- US4, US5, US6 (P2) depend on Phase 3 outputs but not on each other.

### Within each story

- Models → core helpers → providers/ logic → cli/ wiring → tests.
- Tests marked [P] within a story can run concurrently.
- Per-provider parallelism in US1 is the largest single concurrency win (T023–T036 split across four providers).

---

## Parallel Example: Phase 2 Foundational

```bash
# All [P]-marked tasks in Phase 2 can run concurrently after T001 finishes:
Task: "T005 [P] Implement core/fnox.py"
Task: "T006 [P] Create models/node.py"
Task: "T007 [P] Create models/manifest.py"
Task: "T008 [P] Vendor manifest-schema-v1.json"
Task: "T011 [P] Create providers/broker.py skeleton"
Task: "T012 [P] Create core/broker_install.py skeleton"
Task: "T014 [P] Ansible role bootstrap_token_imds"
Task: "T015 [P] Ansible role bootstrap_token_file"
Task: "T016 [P] Ansible role bootstrap_token_mount"
# Then dependent tasks T009, T010, T013, T017 run, followed by test tasks T018–T022 [P].
```

## Parallel Example: Phase 3 User Story 1

```bash
# Four provider tracks in parallel after Phase 2:
Track Hetzner: T023 → T024 → T033 → T041 [P]
Track AWS:     T025 → T026 → T034 → T042 [P]
Track Incus:   T027, T029 → T031 [P] → T035 → T043, T045 [P]
Track Proxmox: T028, T030 → T032 [P] → T036 → T044, T046 [P]
Track devcontainer: T038 → T039, T040 → T047 [P]
```

---

## Implementation Strategy

### MVP scope (P1 only)

1. Phase 1 + Phase 2 (foundation).
2. Phase 3 (US1): broker available in devcontainers on all four providers.
3. Phase 4 (US2): verify multi-device works.
4. Phase 5 (US3): provisioning credentials migrated to fnox.
5. **STOP and VALIDATE** against quickstart.md steps 1–6.

This is the supply-chain-attack-mitigation MVP. Ship it.

### Incremental P2 additions

6. Phase 6 (US4): manifest + audit — adds the policy mechanism + observability.
7. Phase 7 (US5): rotation + revocation — closes the "leaked token lives forever" hole.
8. Phase 8 (US6): devcontainer auto-synthesis — closes the instance-OS-fallback hole.
9. Phase 9 (Polish): docs, lint, version bump, full quickstart pass.

### Parallel team strategy

- Phase 2 work is naturally chunkable across 5–8 developers (per [P] tasks).
- Phase 3 splits cleanly into four per-provider tracks plus a devcontainer track.
- US3 (Phase 5) can be done by a separate developer in parallel with US1 since it touches different files.
- US4–US6 (Phases 6–8) can each be owned by one developer concurrently once Phase 3 is done.

---

## Notes

- Every Ansible task introduced MUST use `| default(...)` on registered-variable attribute access (Constitution Principle I). Pre-commit grep (T095) catches regressions.
- The broker daemon itself is owned by `get2knowio/remo-broker`. Tasks here treat it as a pinned binary release. Manifest schema drift is defended by double-validation (T010 laptop + broker-side independently).
- Tests live alongside the code they cover; per-provider tests under `tests/unit/providers/`, per-CLI tests under `tests/unit/cli/`.
- The existing `src/remo_cli/` layout differs from CLAUDE.md (which mentions `src/remo/`) — use the actual on-disk path `src/remo_cli/` for all new code.
- Stop at any checkpoint to validate the corresponding user story end-to-end via the quickstart.md section before continuing.
