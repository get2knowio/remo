# Contract: `ansible/roles/ssm_connector` and its playbooks

## Playbooks

### `ansible/ssm_connector_enroll.yml`

Caller: `providers/connector.py:enroll` via `core/ansible_runner.run_playbook("ssm_connector_enroll.yml", ["-e", "@<0600 vars file>", "-e", "remo_ssh_host=…", …])`. Never run by hand with the code on the command line.

| Play | Hosts | become | Purpose |
|------|-------|--------|---------|
| 1 | `localhost` | no | assert inputs (`ssm_activation_id`, `ssm_region`, `ssm_activation_code` defined & non-empty — asserted with `no_log: true`); `add_host` the connector as `remo_connector` and each `connector_exposed_hosts[]` into `remo_exposed_hosts` (address/user/port/identity/`ansible_ssh_common_args`) |
| 2 | `remo_connector` | yes | role `ssm_connector` |
| 3 | `remo_exposed_hosts` | **no** | `ansible.posix.authorized_key` (user = `ansible_user`, key = `hostvars['remo_connector']['connector_public_key']`, comment ` remo-connector@{{ connector_name }}`, `exclusive: false`) |

### `ansible/ssm_connector_unenroll.yml`

One play on the connector (become): stop+disable `amazon-ssm-agent`, `amazon-ssm-agent -register -clear` (guarded by the registration file's existence), remove `connector.json`; when `connector_purge | default(false) | bool`: remove state dir, `/opt/remo-connector`, the run-as user (`remove: true`).

## Role `ssm_connector`

### Defaults (`defaults/main.yml`)

```yaml
connector_run_as_user: remo-connector
connector_state_dir: /var/lib/remo-connector
connector_install_dir: /opt/remo-connector
remo_version: ""          # required unless remo_source is set
remo_source: ""           # PEP 508 / git spec for Tier 1 testing
ssm_region: ""            # required
ssm_agent_deb_url: "https://amazon-ssm-{{ ssm_region }}.s3.{{ ssm_region }}.amazonaws.com/latest/debian_{{ 'arm64' if ansible_architecture | default('') in ['aarch64', 'arm64'] else 'amd64' }}/amazon-ssm-agent.deb"
connector_exposures: []   # E2 shape
connector_registry_json: ""  # E5 content
connector_exposed_hosts: []  # name, address, user, port
```

### Tasks (`tasks/main.yml`, in order) and their idempotency guarantee

| # | Task | Module | Idempotent because | Conditional paths to test |
|---|------|--------|--------------------|---------------------------|
| 1 | Assert Debian family and required vars | `assert` | read-only | non-Debian → fail before changes |
| 2 | Install `openssh-client curl ca-certificates` | `apt` | package state | already installed |
| 3 | Probe `amazon-ssm-agent` binary | `stat` (`changed_when: false`) | probe | present / absent |
| 4 | Install SSM Agent from `.deb` | `apt: deb=` | dpkg state | skipped when 3 says present |
| 5 | Probe registration `/var/lib/amazon/ssm/registration` | `stat` | probe | present / absent |
| 6 | Stop agent before registering | `systemd` | only `when` 5 absent | skipped path |
| 7 | **Register** `amazon-ssm-agent -register -code … -id … -region … -y` | `command`, **`no_log: true`** | `when` 5 absent; `creates:` the registration file | absent → runs; present → skipped; failure surfaced with the activation-expired hint |
| 8 | Enable + start agent | `systemd: enabled=true state=started` | unit state | already running |
| 9 | Read registration file → `ssm_node_id` fact | `slurp` + `set_fact` | read-only | file present (else fail: "registration missing after register") |
| 10 | "Managed node id: {{ ssm_node_id }}" | `debug` (task name carries the id) | read-only | — |
| 11 | Create run-as user | `user` (`shell: /bin/bash`, `create_home`, no sudoers entry) | user state | exists / absent |
| 12 | Create install dir | `file` | fs state | — |
| 13 | Install uv into `{{ connector_install_dir }}/uv` | `shell` with `creates:` | `creates` | present / absent |
| 14 | Probe installed remo version | `command … remo --version` (`failed_when: false`, `changed_when: false`) | probe | present / absent |
| 15 | `uv tool install --force "remo-cli=={{ remo_version }}"` (or `{{ remo_source }}`) with `UV_TOOL_DIR/UV_TOOL_BIN_DIR/UV_PYTHON_INSTALL_DIR` | `command` | `when` 14's version ≠ `remo_version` (source installs always run, reported changed) | match → skipped; mismatch → runs |
| 16 | State dir `0700` owned by run-as user; `ssh/` subdir | `file` | fs state | — |
| 17 | Generate identity `id_ed25519` | `community.crypto.openssh_keypair` | key exists | present / absent |
| 18 | Read public key → `connector_public_key` fact | `slurp` | read-only | — |
| 19 | Write `registry.json`, `exposure.json` (0600) | `copy content=` | content compare | changed only on content change |
| 20 | Keyscan each exposed host from the connector | `command ssh-keyscan -T 5 -p PORT ADDRESS` per host (`changed_when: false`, `failed_when: false`) | probe | reachable / unreachable (fail with "host unreachable from the connector") |
| 21 | Append absent host-key lines to `known_hosts`; fail on a differing key | `lineinfile`/`blockinfile` per line guarded by lookup | line present → no change | absent / present-same / present-different (fail, remediation names the file) |
| 22 | Write `connector.json` (0600) | `copy content=` | content compare | changed only on first enrollment or version change |

Every `register`ed result is read through `| default()`; every task that could see `ssm_activation_code` sets `no_log: true` (1, 7). Structural tests in `tests/ansible/test_ssm_connector_role.py` assert rows 1–22's module choice, guards and `no_log`; a live two-run proof is the manual gate.

### Handlers

`Restart amazon-ssm-agent` (systemd) — notified by 4 only.

### Templates / files

None beyond `copy content=`; the registry JSON and exposure JSON are produced on the workstation (E5/E2) so the role never composes registry syntax.
