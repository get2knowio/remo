# Research: SSM Connector

**Feature**: `025-ssm-connector` | **Date**: 2026-09-27 | Grounded against `main` @ `680dbf7` and the AWS documentation fetched on 2026-09-27 (URLs in each item).

Every anchor below was verified against the working tree by a read-only sweep; line numbers are as of `680dbf7`.

## R1. Session type and document shape

**Decision**: `schemaVersion: "1.0"`, `sessionType: InteractiveCommands`, one `properties.linux.commands` string, `runAsElevated: false`. The document ships as JSON at `src/remo_cli/core/remo_attach_document.json` (package data; no `pyproject.toml` change needed because `[tool.hatch.build.targets.wheel] packages = ["src/remo_cli"]` ships every file under the package).

**Verified** (session-manager-schema.html, fetched 2026-09-27):
- Session documents support only schema version 1.0; valid `sessionType` values are `InteractiveCommands | NonInteractiveCommands | Port | Standard_Stream`.
- `properties` for `InteractiveCommands` holds `linux.commands` / `windows.commands` and the `runAsElevated` boolean ("whether commands are run as root"). The `inputs` block (`runAsEnabled`, `runAsDefaultUser`, timeouts, logging) is documented for `Standard_Stream` documents.
- Parameters follow the SSM document parameter schema (`type`, `description`, `default`, `allowedValues`, `allowedPattern`, `minChars`, `maxChars`); the example uses `allowedPattern` and `maxChars`.
- Parameters are substituted into the command string as `{{ name }}` (session-manager-restrict-command-access.html shows `"commands": "tail -f {{ logpath }}"`).
- The restrict-command-access page states: "Documents with the `sessionType` of `InteractiveCommands` are only supported for sessions started from the AWS Command Line Interface (AWS CLI)." This is a documented AWS constraint, recorded here for the browser-direct consumer (Spec Prompts Sequence 4) — it does not affect this feature, whose client is the AWS CLI with the Session Manager plugin. Tracked as a deferred issue (R15).

**PTY**: `InteractiveCommands` runs the command in an interactive session (the CLI attaches the local terminal via the Session Manager plugin, which sets the remote PTY size and forwards resizes). The exit-code-not-delivered and shared-stream behaviors are properties of the agent's interactive shell plugin (`aws/amazon-ssm-agent`, `agent/session/shell`); they are the reason for the error line (R3). Resize propagation (agent sets winsize → SIGWINCH to the foreground process → `ssh -tt` forwards a window-change → Zellij redraws) is the standard PTY chain and is exercised by the manual gate (SC-008); nothing in this feature alters it.

**Alternatives rejected**: `Standard_Stream` (gives a general shell; forbidden by the spec); `NonInteractiveCommands` (no PTY); `Port` (SSH-over-SSM again, which the EC2 path already does and which needs an inbound SSH listener on the target from the connector's perspective — it also cannot reach a Proxmox container behind the connector without a second hop).

## R2. The `target` parameter: encoding and length bound

**Decision**: `target` is `base64url(JSON)` without padding, of `{"v":1,"host":"<host>","project":"<project>"}` serialized compactly (`separators=(",", ":")`, `ensure_ascii=False`, UTF-8). `allowedPattern: "^[A-Za-z0-9_-]{1,1024}$"` and `maxChars: 1024`. The launcher additionally enforces the decoded caps below.

**Justification of N = 1024**:
- Host names pass `core/validation.py:validate_name` (`^[a-zA-Z0-9][a-zA-Z0-9._/-]*$`, max 63 chars, `validation.py:17-21`). ASCII only, ≤ 63 bytes.
- Project names pass `validate_project_name` (`validation.py:75`): control characters, any `/`, leading `.` and `..` traversal are rejected; spaces, Unicode, quotes and a leading `-` are allowed; there is **no length cap today**. A project is a directory name under `~/projects` on the host (`dev_workspace_dir`), so the practical cap is `NAME_MAX` = 255 bytes of UTF-8. Contract v1 fixes **255 bytes UTF-8** as the project cap; the launcher rejects longer decoded projects with `invalid-name`.
- Worst-case JSON: `{"v":1,"host":"","project":""}` is 30 bytes; host ≤ 63; a project of 255 `"` characters escapes to 510 bytes. Total ≤ 603 bytes → base64 ≤ ⌈603/3⌉·4 = 804 characters. 1024 leaves headroom for v1 without inviting abuse; a raw name of any shape (space, quote, `/`, Unicode, leading `.`) fails the pattern before the agent runs anything.
- The document command uses `--` before `{{ target }}` so a value that begins with `-` (legal in base64url) can never be parsed as an option.

**Alternatives rejected**: two parameters `host` + `project` with their own patterns (a project pattern that admits spaces and quotes cannot be made injection-safe against string substitution); hex (2× longer, no benefit); padded base64 (`=` is not in a conservative pattern and is unnecessary).

## R3. Error line and codes

**Decision**: On any pre-exec failure the launcher writes exactly one line to stdout, flushes, and exits 1:

    remo-connector-error: <code> <message>

Codes (contract v1, `core/connector.py:ErrorCode`): `bad-target` (undecodable, not JSON, not an object, missing field, wrong field type), `unsupported-version` (`v` not in the supported set `{1}`), `invalid-name` (host fails `validate_name` or project fails `validate_project_name` / exceeds 255 bytes), `not-exposed` (pair absent from the exposure file, or the file is absent/empty), `run-as-not-in-effect` (euid 0 or user name `ssm-user`), `config-unreadable` (state dir, registry, exposure file, identity or known-hosts missing/unreadable/unparseable — the message names the path), `ssh-missing` (`ssh` not on PATH). `not-exposed` deliberately does not distinguish "unknown host" from "known but not exposed" (spec edge case).

**Why stdout and no color**: under `InteractiveCommands` the client sees a single PTY stream; `core/output.print_error` colors and prefixes, which would put escape codes into a line that clients parse, so the launcher prints the line itself with `print(..., flush=True)`.

**Exit boundary (Constitution III)**: `providers/connector.py:attach()` returns `1` after printing the line, and `cli/providers/factory.py:provider_command` (line 59) turns an int return into `sys.exit(rc)` — the same path `remo add`/`remo remove` use. No new exception-to-exit translation is introduced. On success `attach()` calls `os.execvp(argv[0], argv)` and never returns.

## R4. The shared attach builder moves to `core/`

**Verified**: `web/terminal.py:176-232 build_attach_argv(host, project, *, control_dir=None, settings=None)` = `validate_project_name` + `build_ssh_base_cmd(host, tty=True, multiplex=True, control_dir, identity_file=settings.ssh_identity_for(host), use_registry_identity=False, known_hosts_file=settings.ssh_known_hosts_file)` + `build_remo_host_shell_cmd("sessions attach", project=project)` + `BatchMode=yes` inserted after `argv[0]`. The only web-specific inputs are the two settings-derived paths and `use_registry_identity=False`. Callers: `web/api/terminals.py:407,416`; tests monkeypatch it **by name** on `remo_cli.web.terminal` (`tests/unit/web/test_terminals_api.py:119,389`, `tests/integration/test_security_rejections.py:131,168`); `tests/integration/test_web_cli_parity.py:134-152` pins the argv order `[ssh, -o, BatchMode=yes, *opts, -tt, target, remote_cmd]`; `test_build_host_shell_argv_is_attach_argv_minus_remote_command` pins `shell == attach[:-1]`.

**Decision**: new `src/remo_cli/core/attach.py`:

    def build_attach_argv(host, project, *, control_dir=None, identity_file=None,
                          known_hosts_file=None, use_registry_identity=True) -> list[str]

containing exactly the host-agnostic body. `web/terminal.py:build_attach_argv` keeps its name and signature and becomes a thin wrapper that resolves `WebSettings` and calls the core builder with `use_registry_identity=False`. `build_host_shell_argv` is untouched. A characterization test pins the web wrapper's argv before the refactor (Constitution VI). No `core → web` import is added; `core/attach.py` imports only `core.ssh`, `core.remo_host_client`, `core.validation`, `models.host`.

**Note on `remo shell`**: the CLI's `remo shell -p` (`cli/shell.py:48`, `core/ssh.py:520 shell_connect`) runs `project-launch` directly via `subprocess.run`, not `remo-host sessions attach`, and does not `exec`. Both land in the same Zellij session because `remo-host sessions attach` itself `exec`s `project-launch` (`remo-host.sh.j2:279`). The launcher mirrors the **web** path, as the prompt asks; `remo shell` is not modified (FR-022).

## R5. Connector filesystem layout and how the launcher finds it

**Decision**: fixed paths, part of contract v1 (the document hard-codes the launcher path):

| Path | Owner / mode | Purpose |
|------|--------------|---------|
| `/opt/remo-connector/bin/remo` | root, 0755 tree | pinned remo-cli installed by `uv tool install` with `UV_TOOL_DIR=/opt/remo-connector/tools`, `UV_TOOL_BIN_DIR=/opt/remo-connector/bin`, `UV_PYTHON_INSTALL_DIR=/opt/remo-connector/python` (uv-managed CPython so the result is identical on Debian 12 and Ubuntu 24.04) |
| `/var/lib/remo-connector/` | run-as user, 0700 | state dir = `REMO_HOME` for the launcher |
| `…/registry.json` | 0600 | connector's private registry v2 (R8) |
| `…/exposure.json` | 0600 | exposure allow-list (data-model E2) |
| `…/id_ed25519`, `…/id_ed25519.pub` | 0600 / 0644 | connector identity |
| `…/known_hosts` | 0600 | strict host keys for exposed hosts (R9) |
| `…/connector.json` | 0600 | node id, region, run-as user, versions (data-model E3) |
| `…/ssh/` | 0700 | ControlMaster sockets (short path: AF_UNIX `sun_path` limit) |

Document command (contract v1): `exec /opt/remo-connector/bin/remo connector attach -- {{ target }}`. The launcher sets `REMO_HOME=/var/lib/remo-connector` (overridable by `REMO_CONNECTOR_STATE_DIR` for tests) **before** touching the registry, so `core/config.get_registry_path()` (`config.py:151`) and `core/registry.read_registry` resolve the connector's private registry with no new registry API. Installing system-wide (not into the run-as user's `~`) is what lets the launcher run — and refuse — when the session lands on the wrong user (`ssm-user` has no access to the run-as user's home, which would yield "permission denied" instead of the contract's error line).

**Alternatives rejected**: install into the run-as user's home via `~/.local/bin` (unreachable from `ssm-user`, so the refusal backstop never fires); `pipx` (not the portfolio tool; remo's own installer is uv-based); relying on the distro `python3` (Debian 12 ships 3.11, fine today, but the uv-managed interpreter removes the variable).

## R6. Enrollment: where it runs, the activation secret, the vars file

**Verified**:
- `core/ansible_runner.py:171 run_playbook(playbook, extra_vars, inventory, verbose)` appends `extra_vars` verbatim to the `ansible-playbook` argv; every existing caller passes `-e key=value` pairs (`providers/added.py:422-450`). No vars-file mechanism exists. In filtered mode it writes the full log to a `mkstemp` file and **prints the whole log to stdout on failure**.
- `no_log: true` appears nowhere under `ansible/` (the only `no_log` is `false` at `roles/proxmox_container/tasks/main.yml:59`).
- Interactive secret input precedent: `cli/web.py:254,356,481` `click.prompt("Pairing code", hide_input=True)`.
- Hybrid activation (activations.html, hybrid-multicloud-ssm-agent-install-linux.html, fetched 2026-09-27): `aws ssm create-activation` returns an **activation ID** and an **activation code**; registration on Linux is `amazon-ssm-agent -register -code "<code>" -id "<id>" -region "<region>"` (or `ssm-setup-cli -register -activation-code … -activation-id … -region …`); hybrid nodes get the `mi-` prefix; an activation can be re-used up to its registration limit; account-side removal is `aws ssm deregister-managed-instance --instance-id mi-…`; local material is cleared with `amazon-ssm-agent -register -clear`. Effective 2026-09-30 Session Manager on hybrid nodes is pay-as-you-go (the pricing page is linked, never a number).

**Decisions**:
- Enrollment runs from the operator's workstation: `remo connector enroll NAME --activation-id ID --region R --expose HOST/PROJECT …` against a host already in the operator's registry (clarification 1, FR-014). The connector reaches the operator through the same generic inventory the `remo add` configure path uses (`ansible/ssh_configure.yml:32-86`: `add_host` with `remo_ssh_host/user/port/identity`), extended with `remo_ssh_common_args` built from `core/ssh.build_ssh_opts(host)` so a provider host (e.g. an AWS instance in SSM mode) is reached exactly as `remo shell` reaches it — no `host.type` branching anywhere.
- The activation code is read by `click.prompt(hide_input=True)` when stdin is a TTY, else one line from stdin; an empty value is a `PreconditionError` before any playbook runs. There is no `--activation-code` option and no environment variable (FR-015).
- The code travels in a `tempfile.mkstemp` file `fchmod`ed to 0600 containing `{"ssm_activation_code": "…"}` plus the non-secret structured inputs (exposures, registry entries), passed as `-e @<path>`; the file is unlinked in `finally` (SIGINT inside `run_playbook` raises `SystemExit`, so `finally` runs). Every task that references `ssm_activation_code` carries `no_log: true`, which also censors it in the failure log dump.
- **Residual, documented honestly**: AWS's registration command takes the code as an argument, so for the seconds the `amazon-ssm-agent -register` task runs, the code is visible in the process list **on the connector** (a host the operator controls, via `no_log: true`). Neither AWS tool reads the code from stdin. The spec's "never in argv" guarantee is therefore scoped to the operator's workstation and the automation transcript; the connector-side residual is stated in `docs/ssm-connector.md` (FR-016 is amended to say so).

**Alternatives rejected**: on-connector self-enrollment (needs remo installed first — a bootstrap problem — and a second, divergent flow); `-e ssm_activation_code=…` (in `ps` on the workstation and in the failure dump); Ansible Vault (adds a vault password that would itself need a channel).

## R7. Ansible role `ssm_connector`

**Verified precedent**: role layout `tasks/main.yml` + `defaults/main.yml` + `templates/` (`roles/user_setup`), `handlers/` (`roles/docker`), FQCN modules, `| default()` on every registered access, probes with `changed_when: false` / `failed_when: false`, apt-only hosts asserted in `ssh_configure.yml`. Collections available: `ansible.posix` (`authorized_key`), `community.general`, `community.crypto` (`openssh_keypair`) — `ansible/requirements.yml`.

**Decisions** (`ansible/ssm_connector_enroll.yml`, three plays; `ansible/ssm_connector_unenroll.yml`, one play; `ansible/roles/ssm_connector/`):
1. **Play 1 (localhost)**: assert inputs; `add_host` the connector as `remo_connector` and every exposed host into `remo_exposed_hosts` (each with its own address/user/port/identity/common-args from the vars file).
2. **Play 2 (`remo_connector`, become)**: assert Debian family; install `openssh-client`, `curl`, `ca-certificates`; install SSM Agent from the `.deb` (`https://amazon-ssm-{{ ssm_region }}.s3.{{ ssm_region }}.amazonaws.com/latest/debian_{{ arch }}/amazon-ssm-agent.deb`, `ansible.builtin.apt: deb:` — idempotent) unless `amazon-ssm-agent` is already present; **register** only when `/var/lib/amazon/ssm/registration` is absent (`stat`), with `no_log: true`; enable+start the unit; create the run-as user (`ansible.builtin.user`, no sudo, shell `/bin/bash`, `create_home`); install uv into `/opt/remo-connector/uv` via the official installer and `uv tool install "remo-cli=={{ remo_version }}"` (or `{{ remo_source }}` for Tier 1 testing) with the `UV_*` dirs from R5, guarded by a `remo --version` probe so a matching version reports no change; create the state dir and generate the ed25519 identity (`community.crypto.openssh_keypair`, idempotent); template `registry.json`, `exposure.json`, `connector.json`; keyscan each exposed host **from the connector** and append lines whose lookup key is absent from `known_hosts` (changed only when appended; a present-but-different key fails with the operator remediation); read `/var/lib/amazon/ssm/registration` to publish `ssm_node_id` as a fact and a task named with it (the filtered runner prints task names, `core/ansible_runner.py:79 _filter_line`).
3. **Play 3 (`remo_exposed_hosts`, `become: false`)**: `ansible.posix.authorized_key` for the connecting user with the connector public key from `hostvars['remo_connector']`, `comment`/marker ` remo-connector@<NAME>` so re-enrollment replaces rather than duplicates. Self-target works unchanged: the exposed host is the same machine, reached at `localhost`.
4. **Unenroll**: stop+disable the agent, `amazon-ssm-agent -register -clear`, remove `connector.json`; `--purge` additionally removes the state dir, `/opt/remo-connector` and the run-as user. The CLI prints the account-side deregistration command and the expired-activation note (FR-021).

**SSM Agent in an unprivileged LXC**: not an AWS-documented configuration; the `.deb` installs a systemd unit, which runs in a systemd-based LXC. Persistence of `/var/lib/amazon/ssm/registration` and `/etc/machine-id` across container restarts is the known failure point (AWS troubleshooting: "MachineFingerprintDoesNotMatch … when the machine ID doesn't persist"). Verification is the manual gate; the docs list exactly this and say it is unverified until then.

**Ansible idempotency tests**: the repo has no live-host harness (`tests/ansible/` is structural PyYAML plus a syntax-check gated on `ansible-playbook`). The structural tests assert: `no_log: true` on every task touching the code; every registered access defaulted; the registration task guarded by the stat; idempotent modules for every write. The fresh-host/second-run proof is part of the manual gate issue (SC-004 is recorded there, not claimed by CI).

## R8. The connector's registry and exposure gate

**Verified**: registry v2 (`core/registry.py:36-37`, `KNOWN_TYPES` includes `ssh`), `known_host_to_entry` (`registry.py:148`) serializes an `ssh` host as `{"type","name","host","user","access":"direct","ssh":{"port","identity_file"}}`; `_validate_single_host` (`registry.py:328`) allows `access: ssm` only for `type: aws`. `resolve_remo_host_by_name` (`core/known_hosts.py:269`) calls `sys.exit` on a miss — the launcher must not use it.

**Decisions**:
- Enrollment materializes each exposed host as a **type `ssh`** entry in the connector registry (address = the operator registry's `host.host`, or `localhost` for the connector itself; user = `host.user`; port = `host.ssh_port`; identity = the connector key path). The JSON is produced on the workstation by `core/registry` serializers, so the schema stays owned by `registry.py` (Constitution VII), and templated onto the connector as a whole file. No registry schema change (FR-018).
- An exposed host whose operator entry is not directly reachable (AWS `access_mode == "ssm"`) is refused at enrollment with a `PreconditionError` ("exposed hosts must be directly reachable from the connector"); an AWS instance already has SSM and does not need a connector.
- The launcher looks the host up with `get_known_hosts()` filtered by name (no exit), then checks `exposure.json` (E2). Missing/empty/unparseable exposure → `not-exposed` (missing/empty) or `config-unreadable` (unparseable). Re-enrollment rewrites both files from the new `--expose` set (converges; FR-018).

## R9. Host-key trust for the connector

**Verified**: `core/web_adopt.py:644 scan_host_keys(hostname, port)` (`ssh-keyscan -T 5 -t ed25519,ecdsa,rsa`), `known_hosts_lookup_key`, `_parse_known_hosts_pairs`; the console's adoption flow is trust-on-first-push with fingerprint display.

**Decision** (clarification 4): scanning runs **on the connector** (its network vantage point is the one that matters for a NAT'd host), as a role task per exposed host, appending only when the lookup key is absent; a changed key fails the play with the remediation "remove the stale line from /var/lib/remo-connector/known_hosts". The launcher passes `known_hosts_file=/var/lib/remo-connector/known_hosts` and relies on OpenSSH's default `StrictHostKeyChecking=ask` under `BatchMode=yes`, which makes an unknown or changed key a hard failure (no prompt is possible).

## R10. Run-as: what is documented, what is not

**Verified** (session-preferences-run-as.html, fetched 2026-09-27): Run As is enabled in Session Manager preferences (per region) with an OS user name, or per-principal with the `SSMSessionRunAs` IAM tag, which is checked first; Session Manager verifies the OS user exists on the node or the session fails; with Run As on, sessions never fall back to `ssm-user`; `root` is not supported. The schema page documents `runAsEnabled`/`runAsDefaultUser` under `Standard_Stream` inputs and `runAsElevated` under `InteractiveCommands` properties. **Nothing states whether the preference's Run As user applies to a custom `InteractiveCommands` document** — exactly the prompt's warning.

**Decision**: the document sets `runAsElevated: false`; the docs tell the account owner to enable Run As with `remo-connector` (default, clarification 2) by preference or tag; the launcher refuses euid 0 and user `ssm-user` (FR-007) so a misconfiguration fails closed with `run-as-not-in-effect`; the empirical answer is recorded by the manual gate in the tracked issue and in `docs/ssm-connector.md`. `getpass.getuser()` is the user-name source (it reads `LOGNAME/USER/LNAME/USERNAME`, then the passwd entry for the euid); `os.geteuid()` the uid.

## R11. Least-privilege caller policy (documentation)

**Decision** (from getting-started-restrict-access-examples / restrict-command-access, and the hybrid managed-instance ARN form):

    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Action": "ssm:StartSession",
          "Resource": "arn:aws:ssm:REGION:ACCOUNT:managed-instance/*",
          "Condition": {
            "StringEquals": { "ssm:resourceTag/remo:role": "connector" },
            "BoolIfExists": { "ssm:SessionDocumentAccessCheck": "true" }
          }
        },
        {
          "Effect": "Allow",
          "Action": "ssm:StartSession",
          "Resource": "arn:aws:ssm:REGION:ACCOUNT:document/remo-attach"
        },
        {
          "Effect": "Allow",
          "Action": ["ssm:TerminateSession", "ssm:ResumeSession"],
          "Resource": "arn:aws:ssm:*:*:session/${aws:userid}-*"
        },
        {
          "Effect": "Allow",
          "Action": "ssmmessages:OpenDataChannel",
          "Resource": "arn:aws:ssm:*:*:session/${aws:userid}-*"
        }
      ]
    }

Tagging: the account owner passes `--tags Key=remo:role,Value=connector` to `create-activation`, so every node registered with that activation carries the tag. Revocation: `aws ssm terminate-session`, then `aws ssm deregister-managed-instance`; the docs show both.

## R12. CLI surface and registration

**Verified**: groups are registered in `cli/main.py:269 _register_commands()`; `cli/web.py` is the group precedent (lazy imports in bodies); `tests/unit/cli/test_main.py:83-95 EXPECTED_COMMANDS` is an exact-set assertion and must gain `"connector"`; `tests/unit/cli/test_surface_preservation.py` covers only provider groups (unaffected); `cli/providers/factory.py:59 provider_command` maps `ProviderError` → exit code and `int` return → `sys.exit`.

**Decision**: `cli/connector.py` `@click.group() def connector`, subcommands `enroll`, `attach` (hidden from `--help` listing? No — visible but documented as document-invoked), `document`, `status`, `unenroll`, each `@provider_command`, business logic in `providers/connector.py` (raising `core/errors.py` types, no Click, no `sys.exit`), pure logic in `core/connector.py`. `attach` takes a single argument after `--`.

## R13. Documentation gates

**Verified**: `tests/unit/test_docs_structure.py` diffs `src/remo_cli/**/*.py` against the `## Project Structure` fence in **both** `CLAUDE.md` and `AGENTS.md` (separate, already-divergent files); new files `core/attach.py`, `core/connector.py`, `providers/connector.py`, `cli/connector.py` must appear in both. The `ansible/` subtree in the diagram is not gated but is updated for VIII. `docs/ssm-connector.md` is new; `README.md` gains an "SSM connector" entry in the CLI reference and platform list; `docs/aws.md`'s SSM section gets a cross-link.

## R14. Packaging surface (Constitution IX)

Adding `remo_attach_document.json` as package data and a new console-reachable command group is a packaging-surface change. `tests/unit/core/test_connector_document.py` loads the document through `importlib.resources` so the wheel-install smoke job (`.github/workflows/ci.yml` `wheel-install`) exercises the shipped file; the PR must state Tier 2 validation (`gh workflow run dev-build.yml`) before the release that ships document v1, and that release's notes name "remo-attach document v1".

## R15. Deferred work → issues (filed at push time; bodies drafted in `tasks.md`)

1. **Manual gate SC-008**: live enrollment in a test account, two targets incl. one non-AWS, resize/Unicode/TUI, client-kill leaves Zellij, run-as proof for a custom `InteractiveCommands` document, LXC persistence across restarts, Hetzner self-target; record results in `docs/ssm-connector.md`.
2. **Connector as a Docker container**: not proven in this feature; explicitly deferred (prompt §6).
3. **`InteractiveCommands` is documented as AWS-CLI-only**: risk for the browser-direct transport (Sequence 4); needs a data-channel experiment before that spec is planned.
4. **`remo connector expose/unexpose` without re-running enrollment**: convenience follow-up; re-enrollment converges today.
