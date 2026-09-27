# Contract: `remo connector` command group

All commands are wired in `cli/connector.py` (Click only), implemented in `providers/connector.py` (raises `core/errors.py` types, returns int rc), on pure helpers in `core/connector.py`. Every command goes through `provider_command` (exit 0 / 1 / 3-on-abort). No command imports `remo_cli.web`.

## `remo connector enroll NAME`

Turn the registered host `NAME` into a connector (idempotent; re-run to change exposure).

| Option | Required | Notes |
|--------|----------|-------|
| `--activation-id ID` | yes | non-secret |
| `--region REGION` | yes | validated by `validate_region` |
| `--expose HOST/PROJECT` | yes, repeatable | split on the last `/`; `HOST` may be `NAME` itself (self-target → `localhost`) |
| `--run-as-user USER` | no | default `remo-connector`; `root` and `ssm-user` are refused |
| `--remo-version VERSION` | no | default: the enrolling CLI's version (`remo_cli.__version__`) |
| `--remo-source SPEC` | no | e.g. `git+https://github.com/get2knowio/remo@<ref>` — Constitution IX Tier 1 testing; mutually exclusive with `--remo-version` |
| `--verbose` | no | pass-through Ansible output |

**Activation code**: prompted (`Activation code:`, hidden) when stdin is a TTY; otherwise read as one line from stdin. Never an option, never an environment variable. Empty → `PreconditionError` ("Activation code is required; paste it at the prompt or pipe it on stdin"), exit 1, before any automation.

**Pre-flight failures (exit 1, no automation run)**: unknown `NAME`; unknown exposed host; an exposed host in AWS SSM access mode ("must be directly reachable from the connector"); invalid project name; `--run-as-user root|ssm-user`.

**Output**: filtered play progress (task names), then a summary:

```
Connector 'lxc-connector' enrolled.
  Managed node:  mi-0123456789abcdef0  (eu-central-1)
  Run-as user:   remo-connector   <- configure Session Manager Run As to this name
  Exposed:       proxmox-1/dev: remo, blog
                 hetz1: remo
Next: tag/permissions per docs/ssm-connector.md; test with:
  aws ssm start-session --region eu-central-1 --target mi-0123456789abcdef0 \
    --document-name remo-attach --parameters target=<encoded>
```

## `remo connector attach -- TARGET`

Invoked only by the `remo-attach` document on the connector. Not for interactive use.

- Checks, in order: run-as (euid ≠ 0, user ≠ `ssm-user`), `ssh` on PATH, target decode + `v` + names, state dir readable, host in the connector registry, pair exposed.
- On any failure: prints one `remo-connector-error: <code> <message>` line to stdout and returns 1.
- On success: `os.execvp("ssh", argv)` with `argv = core.attach.build_attach_argv(host, project, control_dir=STATE/ssh, identity_file=STATE/id_ed25519, known_hosts_file=STATE/known_hosts, use_registry_identity=False)` — never returns.
- `REMO_CONNECTOR_STATE_DIR` overrides `/var/lib/remo-connector` (tests only; documented as such).

## `remo connector document`

Prints `src/remo_cli/core/remo_attach_document.json` byte-for-byte (trailing newline included). Exit 0. Optional `--name` prints only `remo-attach` (for scripting the `create-document` call).

## `remo connector status NAME`

Reads `connector.json`, `exposure.json` and the agent unit state over SSH (`sudo -n`), prints:

```
Connector:     lxc-connector (10.0.0.12)
Agent:         active (amazon-ssm-agent)
Managed node:  mi-0123456789abcdef0
Region:        eu-central-1
Run-as user:   remo-connector
Document:      remo-attach v1
remo:          4.4.0 at /opt/remo-connector/bin/remo
Exposed:       proxmox-1/dev: remo, blog
               hetz1: remo
```

Not enrolled → `PreconditionError` naming `remo connector enroll`.

## `remo connector unenroll NAME [--purge] [--yes]`

Confirmation prompt (`--yes` skips; Constitution VII). Runs `ansible/ssm_connector_unenroll.yml`: stop + disable the agent, `amazon-ssm-agent -register -clear`, remove `connector.json`; with `--purge` also remove `/var/lib/remo-connector`, `/opt/remo-connector` and the run-as user. Always ends with:

```
Local registration removed. The managed node still exists in your AWS account until you run:
  aws ssm deregister-managed-instance --region eu-central-1 --instance-id mi-0123456789abcdef0
Note: an expired activation does not disconnect a node that already registered; deregister it explicitly.
```

## Errors (all commands)

`PreconditionError` (bad inputs, not enrolled, unreachable), `OperationFailedError` (playbook rc ≠ 0, remote read failed), `UserAbortedError` (declined confirmation → exit 3). Messages name the fix.
