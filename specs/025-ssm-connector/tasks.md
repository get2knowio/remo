---

description: "Task list for 025-ssm-connector"
---

# Tasks: SSM Connector — Reach Remo Project Sessions Through AWS Systems Manager

**Input**: Design documents from `/specs/025-ssm-connector/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R15), data-model.md (E1–E7), contracts/{session-document,cli,ansible-role}.md, quickstart.md

**Tests**: Required. The spec's FRs and Constitution V/VI demand test-backed verification for the codec, the pattern, argv parity, default deny, the run-as refusal, the error line, the secret path, and every role conditional. Write each story's tests first and watch them fail.

**Organization**: Phases by user story (US1 attach, US2 default deny/refusals, US3 enrollment lifecycle, US4 document, US5 docs). Every task carries an exact path. `[P]` = different files, no dependency on an unfinished task.

## Format: `[ID] [P?] [Story] Description`

## Path Conventions

Single project: `src/remo_cli/`, `tests/`, `ansible/`, `docs/` at repo root (plan.md "Source Code").

---

## Phase 1: Setup

- [X] T001 Confirm the baseline is green before changing anything: run `uv run pytest --tb=short -q`, `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli`; record the counts in the implementation report (pre-existing failures are recorded, not chased).
- [X] T002 [P] Characterization test for the web attach argv BEFORE the refactor (Constitution VI): add `tests/unit/core/test_attach_builder.py::test_web_build_attach_argv_characterization` that builds `remo_cli.web.terminal.build_attach_argv(host, "demo-project", control_dir="/run/x", settings=<WebSettings stub with ssh_identity_for→"/k", ssh_known_hosts_file→"/kh">)` for a `type="ssh"` host on port 2222 and asserts the exact list `["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", "ControlPath=/run/x/remo-%r@%h-%p", "-o", "ControlPersist=60s", "-o", "Port=2222", "-o", "IdentityFile=/k", "-o", "IdentitiesOnly=yes", "-o", "UserKnownHostsFile=/kh", <"-o","SendEnv=TZ" iff detect_timezone()>, "-tt", "user@host", 'PATH="$HOME/.local/bin:$PATH" remo-host sessions attach --project demo-project']` (monkeypatch `remo_cli.core.ssh.detect_timezone` to return `None` for determinism).

---

## Phase 2: Foundational (blocks all stories)

- [X] T003 Create `src/remo_cli/core/attach.py` with `build_attach_argv(host: KnownHost, project: str, *, control_dir: str | None = None, identity_file: str | None = None, known_hosts_file: str | None = None, use_registry_identity: bool = True) -> list[str]`: `validate_project_name(project)`; `base = build_ssh_base_cmd(host, tty=True, multiplex=True, control_dir=…, identity_file=…, known_hosts_file=…, use_registry_identity=…)`; `remote = build_remo_host_shell_cmd("sessions attach", project=project)`; return `[base[0], "-o", "BatchMode=yes", *base[1:], remote]`. Imports only `core.ssh`, `core.remo_host_client`, `core.validation`, `models.host`. Module docstring cites research R4 and FR-009/FR-010.
- [X] T004 Refactor `src/remo_cli/web/terminal.py::build_attach_argv` into a thin wrapper: keep the name, signature and docstring intent; resolve `settings`; `return core_build_attach_argv(host, project, control_dir=control_dir, identity_file=resolved.ssh_identity_for(host), known_hosts_file=resolved.ssh_known_hosts_file, use_registry_identity=False)`. Leave `build_host_shell_argv` untouched. Run `uv run pytest tests/unit/web tests/integration/test_web_cli_parity.py tests/integration/test_security_rejections.py -q` and `tests/unit/core/test_attach_builder.py` — the T002 characterization must still pass byte-for-byte.
- [X] T005 [P] Add parity tests to `tests/unit/core/test_attach_builder.py`: `test_web_wrapper_equals_core_builder` (same inputs → identical lists), `test_core_builder_rejects_invalid_project` (`ValueError`), `test_core_builder_has_no_web_import` (assert `"remo_cli.web" not in sys.modules` after `import remo_cli.core.attach` in a fresh subprocess: `python -c "import remo_cli.core.attach, sys; assert not [m for m in sys.modules if m.startswith('remo_cli.web')]"`).
- [X] T006 [P] Create `src/remo_cli/core/connector.py` — constants and pure helpers per data-model E1/E2/E3/E4/E6 and research R2/R3/R5: `DOCUMENT_NAME="remo-attach"`, `DOCUMENT_VERSION=1`, `SUPPORTED_TARGET_VERSIONS=frozenset({1})`, `TARGET_PATTERN=r"^[A-Za-z0-9_-]{1,1024}$"`, `TARGET_MAX_CHARS=1024`, `PROJECT_MAX_BYTES=255`, `LAUNCHER_PATH="/opt/remo-connector/bin/remo"`, `INSTALL_DIR="/opt/remo-connector"`, `STATE_DIR_DEFAULT="/var/lib/remo-connector"`, `STATE_DIR_ENV="REMO_CONNECTOR_STATE_DIR"`, `RUN_AS_USER_DEFAULT="remo-connector"`, `ERROR_LINE_PREFIX="remo-connector-error:"`; `class ErrorCode(StrEnum)` with `bad-target`, `unsupported-version`, `invalid-name`, `not-exposed`, `run-as-not-in-effect`, `config-unreadable`, `ssh-missing`; `class ConnectorRefusal(Exception)` carrying `code: ErrorCode` and `message: str`; `format_error_line(code, message) -> str` (single line: newlines → spaces); `@dataclass(frozen=True) class TargetV1(host: str, project: str)`; `encode_target(host, project) -> str` (compact JSON, `ensure_ascii=False`, base64url, strip `=`); `decode_target(raw: str) -> TargetV1` raising `ConnectorRefusal` with `bad-target` / `unsupported-version` / `invalid-name` per the E1 validation order (re-check length ≤ 1024 and `re.fullmatch(TARGET_PATTERN)`, strict base64 with re-added padding, JSON object, `v` int in the supported set, `host`/`project` str, `validate_name(host, label="host")` (catch `click.BadParameter` → `invalid-name`; note `validate_name` raises `click.BadParameter`, so wrap it), `validate_project_name(project)` (catch `ValueError` → `invalid-name`), `len(project.encode()) <= PROJECT_MAX_BYTES`); `state_dir() -> Path` (env override else default); `@dataclass class ExposureConfig(exposures: dict[str, frozenset[str]])` with `load_exposure(path) -> ExposureConfig` (missing → empty; invalid JSON/shape → `ConnectorRefusal(config-unreadable, names the path)`; `version != 1` → empty) and `is_exposed(host, project) -> bool`; `@dataclass class ConnectorState` (E3 fields) with `load_connector_state(path)` / `dump_connector_state(state) -> str`; `load_document_text() -> str` via `importlib.resources.files("remo_cli.core").joinpath("remo_attach_document.json").read_text()`; `connector_attach_argv(host: KnownHost, project: str, state: Path) -> list[str]` = `core.attach.build_attach_argv(host, project, control_dir=str(state/"ssh"), identity_file=str(state/"id_ed25519"), known_hosts_file=str(state/"known_hosts"), use_registry_identity=False)`.
- [X] T007 [P] Create `src/remo_cli/core/remo_attach_document.json` exactly as in contracts/session-document.md (2-space indent, trailing newline, keys in that order).
- [X] T008 [P] Tests `tests/unit/core/test_connector_target.py`: hostile corpus `["plain", "with space", 'quo"te', "it's", "ünïcödé 日本", "-leading-dash", "a;b&&c", "$(rm -rf /)", "`x`", "tab\there"?→invalid, "../etc", "a/b"→invalid, ".hidden"→invalid, 255-byte project, 256-byte project→invalid]` — round-trip `decode_target(encode_target(h, p)) == TargetV1(h, p)` for every valid pair; every **raw** corpus string fails `re.fullmatch(TARGET_PATTERN)`; every encoded valid value matches it and is ≤ 1024; `{"v":2,…}` → `unsupported-version`; missing key / non-object / bad base64 / non-JSON → `bad-target`; host `"Bad Host!"` → `invalid-name`; extra key inside v1 is ignored; `format_error_line` yields exactly one line with the prefix, code and message.
- [X] T009 [P] Tests `tests/unit/core/test_connector_exposure.py`: missing file → nothing exposed; `{"version":1,"exposures":[]}` → nothing; version 2 → nothing; matching pair → True; same host other project → False; other host same project → False; invalid JSON → `ConnectorRefusal(config-unreadable)` whose message contains the path.
- [X] T010 [P] Tests `tests/unit/core/test_connector_document.py`: `load_document_text()` parses; `schemaVersion == "1.0"`; `sessionType == "InteractiveCommands"`; `list(parameters) == ["target"]`; `parameters["target"]["allowedPattern"] == TARGET_PATTERN` and `maxChars == TARGET_MAX_CHARS`; `properties["linux"]["commands"] == f"exec {LAUNCHER_PATH} connector attach -- {{{{ target }}}}"`; `runAsElevated is False`; the file text ends with exactly one `\n`; the file is reachable through `importlib.resources` (not a repo-relative path) so the wheel-install job exercises it.
- [X] T011 Add `"connector"` to `EXPECTED_COMMANDS` in `tests/unit/cli/test_main.py` (it fails until T017 lands — expected red until then).

**Checkpoint**: `uv run pytest tests/unit/core/test_attach_builder.py tests/unit/core/test_connector_target.py tests/unit/core/test_connector_exposure.py tests/unit/core/test_connector_document.py tests/unit/web -q` green; T011 red.

---

## Phase 3: User Story 1 — Attach to a project session through SSM (P1) 🎯 MVP

**Goal**: `remo connector attach -- TARGET` execs the exact web attach argv for an exposed pair, from the connector's state dir, with no web extra.

**Independent Test**: quickstart §B — with a populated `REMO_CONNECTOR_STATE_DIR`, the launcher replaces itself with the parity argv; run under a monkeypatched `os.execvp` in tests.

### Tests for User Story 1

- [X] T012 [P] [US1] `tests/unit/providers/test_connector_attach.py::test_attach_execs_parity_argv`: tmp state dir with `registry.json` (v2, one `ssh` entry name `lab`, host `10.0.0.5`, user `remo`, port 2222, identity `<state>/id_ed25519`), `exposure.json` exposing `lab/remo`, empty `known_hosts`, `ssh/` dir, touched key files; monkeypatch `os.geteuid→1000`, `getpass.getuser→"remo-connector"`, `shutil.which("ssh")→"/usr/bin/ssh"`, `os.execvp` to capture; call `providers.connector.attach(encode_target("lab","remo"))`; assert captured argv `== core.connector.connector_attach_argv(host, "remo", state)` and that `REMO_HOME` was set to the state dir before the registry read.
- [X] T013 [P] [US1] `tests/unit/core/test_attach_builder.py::test_connector_argv_matches_web_wrapper_for_same_paths`: `connector_attach_argv(host, p, state)` equals `web.terminal.build_attach_argv(host, p, control_dir=str(state/"ssh"), settings=<stub returning the state identity/known_hosts>)` — SC-002 in one assertion; run this test under `uv sync --extra dev` (no web) too by skipping the web half with `pytest.importorskip("fastapi")` guarding only the web comparison, never the core assertions.

### Implementation for User Story 1

- [X] T014 [US1] Create `src/remo_cli/providers/connector.py` with `attach(raw_target: str) -> int` per contracts/cli.md order: (1) `os.geteuid() == 0` or `getpass.getuser() == "ssm-user"` → refusal `run-as-not-in-effect` (message: "session is running as <user>; enable Session Manager Run As for user '<RUN_AS_USER_DEFAULT>' (preference or SSMSessionRunAs tag)"); (2) `shutil.which("ssh") is None` → `ssh-missing`; (3) `decode_target`; (4) `state = state_dir()`, set `os.environ["REMO_HOME"] = str(state)`, verify `registry.json`, `exposure.json`, `id_ed25519`, `known_hosts` exist and are readable else `config-unreadable` naming the path; (5) `host = next((h for h in get_known_hosts() if h.name == target.host), None)` — catch `RegistryError` → `config-unreadable`; (6) `if host is None or not load_exposure(state/"exposure.json").is_exposed(target.host, target.project)` → `not-exposed` (message never includes the names: "that host/project pair is not exposed on this connector; the operator can add it with: remo connector enroll <connector> … --expose HOST/PROJECT"); (7) `argv = connector_attach_argv(host, target.project, state)`; `os.execvp(argv[0], argv)`. Any `ConnectorRefusal` → `print(format_error_line(code, message), flush=True); return 1`. No Click, no `sys.exit`, no `remo_cli.web` import.
- [X] T015 [US1] Add `document() -> int` (prints `load_document_text()` with `sys.stdout.write`, returns 0) and `document_name() -> str` to `src/remo_cli/providers/connector.py`.
- [X] T016 [P] [US1] Create `src/remo_cli/cli/connector.py`: `@click.group() def connector()` (help: "SSM connector: enroll a host as a hybrid-activated managed node and attach through the remo-attach session document (docs/ssm-connector.md)"); `attach` command with one required argument `target` (help says "invoked by the remo-attach session document; not for interactive use"), `@provider_command`, body lazily imports `remo_cli.providers.connector` and returns `attach(target)`; `document` command with `--name` flag printing only the document name; lazy imports only (`# noqa: PLC0415`), no `remo_cli.web`.
- [X] T017 [US1] Register the group in `src/remo_cli/cli/main.py::_register_commands` (`from remo_cli.cli.connector import connector`; `cli.add_command(connector)` after `web`); T011 turns green; `uv run remo connector --help` lists `attach`, `document`.

**Checkpoint**: `uv run pytest tests/unit/providers/test_connector_attach.py tests/unit/core tests/unit/cli/test_main.py -q` green; `uv run remo connector document | python -m json.tool` valid.

---

## Phase 4: User Story 2 — Only what the operator exposed is reachable (P1)

**Goal**: every refusal path yields exactly one error line, rc 1, and no exec.

**Independent Test**: parametrized refusal tests, plus uid-0 / `ssm-user` runs.

### Tests for User Story 2

- [X] T018 [P] [US2] Extend `tests/unit/providers/test_connector_attach.py` with a parametrized `test_refusals_print_one_line_and_never_exec` over: exposed host other project → `not-exposed`; unexposed host → `not-exposed`; missing `exposure.json` → `not-exposed`; empty exposures → `not-exposed`; malformed `exposure.json` → `config-unreadable`; missing `registry.json` → `config-unreadable`; raw name (`base64` of `"lab remo"` not matching pattern) → `bad-target`; `{"v":9}` → `unsupported-version`; project of 300 bytes → `invalid-name`; `os.geteuid→0` → `run-as-not-in-effect`; `getuser→"ssm-user"` → `run-as-not-in-effect`; `which("ssh")→None` → `ssh-missing`. Each asserts: captured stdout is exactly one line matching `^remo-connector-error: (\S+) (.+)$` with the expected code, rc == 1, `os.execvp` never called, and for `not-exposed` the line contains neither the host nor the project string.
- [X] T019 [P] [US2] `test_not_exposed_does_not_reveal_registry_membership`: identical line text for "host in registry but not exposed" and "host not in registry".

### Implementation for User Story 2

- [X] T020 [US2] Make every refusal in `src/remo_cli/providers/connector.py::attach` conform to T018 (ordering per contracts/cli.md; messages actionable; `not-exposed` message constant). No new code paths beyond T014 should be needed — this task is the fix-up pass until T018/T019 are green.

**Checkpoint**: US1 + US2 suites green.

---

## Phase 5: User Story 4 — Anyone can create the session document (P2)

**Goal**: `remo connector document` prints the shipped file; the contract is versioned and documented.

**Independent Test**: T010 + a CLI runner test.

- [X] T021 [P] [US4] `tests/unit/cli/test_connector_cmd.py::test_document_prints_shipped_file`: `CliRunner().invoke(cli, ["connector", "document"])` output `== load_document_text()`; `["connector", "document", "--name"]` prints `remo-attach\n`; `["connector", "--help"]` lists `attach document enroll status unenroll`; `["connector", "enroll", "--help"]` shows no `--activation-code` option and mentions the prompt/stdin.
- [X] T022 [US4] Add the release-notes line to `CHANGELOG.md`? — No: `release-please` generates it from commits; instead ensure the feature commit message body contains "ships remo-attach session document v1 (contract: specs/025-ssm-connector/contracts/session-document.md)" so the generated notes name the document version (FR-004). Record this in the PR body.

---

## Phase 6: User Story 3 — Enroll, inspect, and unenroll a connector (P2)

**Goal**: `remo connector enroll NAME …` is idempotent, secret-safe, and converges exposure; `status` and `unenroll` work.

**Independent Test**: enrollment unit tests with `run_playbook` monkeypatched; role structural tests; syntax-check gated on `ansible-playbook`.

### Tests for User Story 3

- [X] T023 [P] [US3] `tests/unit/providers/test_connector_enroll.py`: fixture with an operator registry (`REMO_HOME` tmp) holding `lxc1` (ssh, 10.0.0.12:22, user remo, identity ~/.ssh/k), `proxmox-1/dev` (type proxmox, host 10.0.0.20, user remo), `aws1` (aws, access_mode ssm). Tests: (a) `test_code_never_in_argv_and_vars_file_0600_then_deleted` — monkeypatch `remo_cli.core.ansible_runner.run_playbook` to capture argv and inspect the `-e @<path>` file **during** the call (stat mode `0o600`, JSON contains `ssm_activation_code`), assert the code string appears in no argv element and the file is gone after return; (b) `test_code_from_stdin_when_not_tty` (monkeypatch `sys.stdin` with `io.StringIO("CODE\n")`, `isatty→False`) and `test_code_from_hidden_prompt_when_tty` (monkeypatch `click.prompt` and assert `hide_input=True`); (c) `test_empty_code_is_precondition_error_before_playbook`; (d) `test_unknown_connector_name`, `test_unknown_exposed_host`, `test_ssm_mode_host_refused` ("must be directly reachable from the connector"), `test_invalid_project_in_expose`, `test_run_as_user_root_or_ssm_user_refused` — all `PreconditionError`, `run_playbook` never called; (e) `test_self_target_uses_localhost` — `--expose lxc1/remo` produces a registry entry with `host == "localhost"`; (f) `test_registry_json_is_v2_from_core_serializer` — parse the vars file's `connector_registry_json` with `core.registry` and get back the exposed hosts as `ssh` type with port and identity `/var/lib/remo-connector/id_ed25519`; (g) `test_exposures_split_on_last_slash` — `proxmox-1/dev/remo` → host `proxmox-1/dev`, project `remo`; (h) `test_playbook_failure_is_operation_failed_error` (rc 2 → `OperationFailedError`, vars file still deleted); (i) `test_remo_version_default_is_running_version` and `--remo-source` exclusivity.
- [X] T024 [P] [US3] `tests/ansible/test_ssm_connector_role.py` (PyYAML structural, same style as `tests/ansible/test_remo_host_idempotency.py`): every task in `ansible/roles/ssm_connector/tasks/main.yml` that references `ssm_activation_code` has `no_log: true`; the register task uses `ansible.builtin.command` with `creates:` and `when:` on the registration stat with `| default(false)`; no `register`ed variable is accessed without `| default(`; SSM Agent install uses `ansible.builtin.apt` with `deb:`; user task has no sudoers side effect; keypair uses `community.crypto.openssh_keypair`; file writes use `ansible.builtin.copy` with `content:`; `ansible/ssm_connector_enroll.yml` has three plays with `become: false` on play 3 and `no_log: true` on the assert that names the code; `ansible/ssm_connector_unenroll.yml` guards `-register -clear` on the registration file; plus `test_enroll_playbook_syntax_check` gated on `shutil.which("ansible-playbook")` (skip, not fail, when unavailable).

### Implementation for User Story 3

- [X] T025 [US3] In `src/remo_cli/providers/connector.py` (no Click, no stdin handling — the CLI layer reads the code and passes it in as `code: str`; an empty/whitespace `code` is a `PreconditionError` here) add `parse_expose(values: tuple[str, ...]) -> dict[str, list[str]]` (rsplit on last `/`, validate each project), `_connection_vars(host: KnownHost) -> dict` (from `core.ssh.build_ssh_opts(host)`: split target into user/host, port from `Port=` opt if present, `ssh_common_args` = shlex-joined remaining `-o` pairs), `build_connector_registry_json(exposed: list[KnownHost], connector_name) -> str` (via `core.registry` public serializers / `canonical_entry`; self → `localhost`), `enroll(name, *, activation_id, region, expose, run_as_user, remo_version, remo_source, code, verbose) -> int`: pre-flight (contracts/cli.md), `tempfile.mkstemp(prefix="remo-connector-", suffix=".json")` + `os.fchmod(fd, 0o600)` + JSON `{"ssm_activation_code", "connector_exposures", "connector_registry_json", "connector_exposed_hosts"}`, `extra = ["-e", f"@{path}", "-e", f"ssm_activation_id={…}", "-e", f"ssm_region={…}", "-e", f"connector_name={…}", "-e", f"connector_run_as_user={…}", "-e", f"remo_version={…}" | f"remo_source={…}", "-e", f"remo_ssh_host=…", …]`, `try: rc = run_playbook("ssm_connector_enroll.yml", extra, verbose=verbose) finally: os.unlink(path)`; rc ≠ 0 → `OperationFailedError` naming `--verbose`; then `status(name)` to print the summary (contracts/cli.md).
- [X] T026 [US3] Add `status(name) -> int` to `src/remo_cli/providers/connector.py`: build `build_ssh_base_cmd(host, extra_opts=["-o","BatchMode=yes","-o","ConnectTimeout=10"])` + remote `sudo -n sh -c 'cat /var/lib/remo-connector/connector.json; echo ---; cat /var/lib/remo-connector/exposure.json; echo ---; systemctl is-active amazon-ssm-agent; /opt/remo-connector/bin/remo --version'`; parse; print the contracts/cli.md block; missing state → `PreconditionError("… not enrolled; run remo connector enroll …")`; SSH failure → `OperationFailedError`.
- [X] T027 [US3] Add `unenroll(name, *, purge, assume_yes, confirm: Callable[[str], bool]) -> int`: confirm unless `assume_yes` (`UserAbortedError` on decline); read `connector.json` first (for the node id/region in the closing message); `run_playbook("ssm_connector_unenroll.yml", ["-e", …connection vars, "-e", f"connector_purge={purge}"])`; print the contracts/cli.md closing block with the exact `aws ssm deregister-managed-instance` command.
- [X] T028 [US3] Wire `enroll`, `status`, `unenroll` in `src/remo_cli/cli/connector.py` per contracts/cli.md (options, `--expose` `multiple=True, required=True`, `--run-as-user` default from `RUN_AS_USER_DEFAULT`, `--remo-version`/`--remo-source` mutually exclusive → `click.UsageError`, `--yes/-y`, `--purge`, `--verbose`); the CLI computes `code = click.prompt("Activation code", hide_input=True) if sys.stdin.isatty() else sys.stdin.readline().strip()` and passes it in; `confirm` from `core.output.confirm`.
- [X] T029 [P] [US3] Create `ansible/roles/ssm_connector/defaults/main.yml` exactly per contracts/ansible-role.md (defaults block).
- [X] T030 [P] [US3] Create `ansible/roles/ssm_connector/tasks/main.yml` implementing rows 1–22 of contracts/ansible-role.md (FQCN modules, `| default()` everywhere, `no_log: true` on rows 1 and 7, `creates:` on 7 and 13, `changed_when`/`failed_when` on probes, the "Managed node id: {{ ssm_node_id }}" task name, keyscan-then-append with the differing-key failure message naming `/var/lib/remo-connector/known_hosts`, uv install with `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR`/`UV_PYTHON_INSTALL_DIR` under `{{ connector_install_dir }}` and `UV_INSTALL_DIR={{ connector_install_dir }}/uv`), and `ansible/roles/ssm_connector/handlers/main.yml` (`Restart amazon-ssm-agent`).
- [X] T031 [P] [US3] Create `ansible/ssm_connector_enroll.yml` (three plays per contracts/ansible-role.md; play 1 mirrors `ansible/ssh_configure.yml`'s `add_host` shape incl. `ansible_ssh_common_args` from `remo_ssh_common_args`; play 3 `ansible.posix.authorized_key` with `comment: "remo-connector@{{ connector_name }}"`) and `ansible/ssm_connector_unenroll.yml`.
- [X] T032 [US3] Run `uv run pytest tests/unit/providers/test_connector_enroll.py tests/ansible/test_ssm_connector_role.py tests/unit/cli/test_connector_cmd.py -q` and, if `ansible-playbook` is available locally, `cd ansible && uv run ansible-playbook --syntax-check ssm_connector_enroll.yml ssm_connector_unenroll.yml -e connector_name=x -e ssm_activation_id=x -e ssm_region=eu-central-1 -e ssm_activation_code=x -e remo_ssh_host=h -e remo_ssh_user=u -e remo_ssh_port=22`.

**Checkpoint**: enrollment/status/unenroll suites green; syntax-check green or skipped with reason.

---

## Phase 7: User Story 5 — Documented, proven deployment shapes (P3)

- [X] T033 [P] [US5] Write `docs/ssm-connector.md` per FR-026/FR-027 and research R1/R5/R6/R10/R11: architecture (diagram in prose), the v1 contract (link + summary of encoding and codes), creating the document, hybrid activation with `--tags Key=remo:role,Value=connector`, the least-privilege policy JSON from R11, Run As (preference vs `SSMSessionRunAs`, which wins, why not `ssm-user`, "run-as isolation protects the connector host, not the target session: the target's Remo user has passwordless sudo by design"), enrollment walkthrough (`remo connector enroll …`, prompt/stdin, `--remo-source` for Tier 1), status/unenroll/revocation (`terminate-session`, `deregister-managed-instance`, expired activation note), the connector-side residual from R6, the two reference shapes with a **"Verified / Not yet verified"** table that is honest today (unverified until the manual gate; link the issue), the AWS note that `InteractiveCommands` documents are documented as AWS-CLI-only, cost (link `https://aws.amazon.com/systems-manager/pricing/`, no number), troubleshooting by error code.
- [X] T034 [P] [US5] `README.md`: add "SSM connector (any host, no inbound ports)" to the platform/feature list and the `remo connector …` lines to the CLI Reference; cross-link from `docs/aws.md`'s "SSM Session Manager" section ("for non-EC2 hosts see docs/ssm-connector.md").
- [X] T035 [US5] `CLAUDE.md` and `AGENTS.md`: add `cli/connector.py`, `providers/connector.py`, `core/attach.py`, `core/connector.py` (and note `core/remo_attach_document.json` on the `core/connector.py` line) to BOTH `## Project Structure` fences in the existing style; add `ansible/ssm_connector_enroll.yml`, `ansible/ssm_connector_unenroll.yml`, `roles/ssm_connector/` to the ansible subtree; add `uv run remo connector {enroll,attach,document,status,unenroll}` to `## Commands`; rewrite the auto-generated 025 bullets in `## Active Technologies` into one house-style line ("No new runtime deps; no registry schema change; new `core/attach.py` (shared attach argv, web wrapper kept), `core/connector.py` + `remo_attach_document.json` (contract v1), `providers/connector.py`, `cli/connector.py`, role `ssm_connector` (025-ssm-connector)") and the `## Recent Changes` entry into a real summary; move the displaced `issues #160/#171 — nested overlayfs` Recent Changes entry into `docs/feature-history.md` (the generator dropped it from CLAUDE.md; it is not in feature-history.md yet — recover the exact text with `git show main:CLAUDE.md | grep -n '^- issues #160/#171'`, and keep AGENTS.md's copy of that entry if it has one). Run `uv run pytest tests/unit/test_docs_structure.py -q`.
- [X] T036 [US5] `docs/feature-history.md`: append the 025 entry in the file's existing format.

---

## Phase 8: Polish & Cross-Cutting

- [X] T037 Web-extra-absent proof (FR-010/SC-002): add `tests/unit/cli/test_connector_lazy_import.py` modeled on `tests/unit/web/test_lazy_import.py` — block `fastapi`/`uvicorn`/`starlette` **and** `remo_cli.web` (sentinel `None` entries in `sys.modules`) in a subprocess, import `remo_cli.core.attach`, `remo_cli.core.connector`, `remo_cli.providers.connector`, `remo_cli.cli.connector`, run `attach` against a `not-exposed` state dir and assert the error line; also assert no module whose name starts with `remo_cli.web` is loaded afterwards. Add `test_no_host_type_branching`: the four new modules' source contains no `host.type ==`/`.type in (` literal comparisons (Constitution II, FR-013).
- [X] T038 [P] mypy: `uv run mypy src/remo_cli` clean (full type hints; `from __future__ import annotations` in every new module).
- [X] T039 [P] ruff: `uv run ruff check src/remo_cli tests` clean (line length 100).
- [X] T040 Full suite: `uv run pytest --tb=short -q` green (compare with T001 baseline; no new failures, no new skips beyond the documented `ansible-playbook` gate).
- [X] T041 Draft the deferred-work issue bodies (research R15) into `specs/025-ssm-connector/issues.md` (NOT filed now — the branch is not being pushed in this run; file with `gh issue create` when it is, then replace the placeholders in `docs/ssm-connector.md` and this file): (1) "Manual gate for spec 025: live SSM connector attach, run-as proof, LXC + Hetzner self-target shapes (SC-008)"; (2) "Connector as a Docker container: prove or document unsupported"; (3) "InteractiveCommands documents are documented as AWS-CLI-only — verify the browser data channel before Sequence 4"; (4) "remo connector expose/unexpose without re-running enrollment". Reference `docs/ssm-connector.md` sections that say "pending issue #TBD".
- [X] T042 Run quickstart §A and §B locally (state dir in `$TMPDIR`) and record the outputs in the implementation report; §C valid JSON; note that §D–§F need AWS and are the manual gate.

---

## Dependencies & Execution Order

- Phase 1 → Phase 2 → (US1 → US2) → US4 ∥ US3 → US5 → Polish.
- T002 before T003/T004 (characterize, then move). T006/T007 before T008–T010 and before US1. T011 red until T017.
- US3 (T023–T032) depends on Phase 2 constants and on `providers/connector.py` existing (T014); its Ansible files (T029–T031) are independent of Python and can run in parallel with T025–T028.
- US5 docs depend on the final command surface (after T028).

## Parallel Example: Phase 2

```
T005 tests/unit/core/test_attach_builder.py (parity)      ─┐
T006 src/remo_cli/core/connector.py                        ├─ parallel after T003/T004
T007 src/remo_cli/core/remo_attach_document.json           │
T008–T010 core tests                                      ─┘
```

## Parallel Example: US3

```
T023 tests/unit/providers/test_connector_enroll.py  ─┐
T024 tests/ansible/test_ssm_connector_role.py        ├─ parallel
T029/T030/T031 ansible role + playbooks              ─┘   then T025–T028 (Python), then T032
```

## Implementation Strategy

MVP = Phases 1–4 (the launcher and its refusals) + T015–T017 (document command): demonstrable locally with a hand-populated state dir. Then US3 (enrollment) and US5 (docs). The live manual gate is out of session scope and tracked (T041).

## Notes

- Never import `remo_cli.web` from the new modules; never call `sys.exit` outside `cli/providers/factory.py`; never put the activation code in argv, env, or a log.
- Deferred work becomes issues (T041), never inline TODOs.
