# Data Model: SSM Connector

**Feature**: `025-ssm-connector` | Sources: [spec.md](spec.md) Key Entities, [research.md](research.md) R2–R9

All entities are files on the connector or in-memory values; none touches the registry schema (FR-018).

## E1. Encoded target (`core/connector.py:TargetV1`)

The caller's request, carried as the document's single parameter.

| Field | Type | Constraint |
|-------|------|------------|
| `v` | int | MUST be `1` (the supported set is `{1}`); any other value → `unsupported-version` |
| `host` | str | MUST pass `core/validation.validate_name` (`^[a-zA-Z0-9][a-zA-Z0-9._/-]*$`, ≤ 63 chars) → else `invalid-name` |
| `project` | str | MUST pass `core/validation.validate_project_name` **and** be ≤ 255 bytes UTF-8 → else `invalid-name` |

Wire form: `base64url(json.dumps({"v":1,"host":…,"project":…}, separators=(",",":"), ensure_ascii=False).encode("utf-8"))` with `=` padding stripped. Decoding re-adds padding, decodes strict base64url, parses JSON, requires an object with exactly those three keys present (unknown extra keys are ignored within `v == 1`; a missing key is `bad-target`). Total encoded length ≤ 1024 (pattern); the launcher re-checks it.

Validation order in the launcher: length/pattern → decode → `v` → field presence/types → `host` name → `project` name → run-as → config → exposure. (Run-as is checked **first** at process start in practice; the codec is exercised independently by tests.)

## E2. Exposure configuration (`/var/lib/remo-connector/exposure.json`)

```json
{
  "version": 1,
  "exposures": [
    {"host": "proxmox-1/dev", "projects": ["remo", "blog"]},
    {"host": "hetz1", "projects": ["remo"]}
  ]
}
```

| Field | Constraint |
|-------|------------|
| `version` | int, MUST be `1` |
| `exposures[].host` | registry host **name** (not address); MUST also exist in the connector registry (E5) |
| `exposures[].projects` | non-empty list of project names, each valid per `validate_project_name`; no wildcard in v1 (spec assumption) |

Semantics: `is_exposed(host, project)` is true iff some entry has that host and that project. Absent file, `exposures == []`, or `version != 1` → deny all (`not-exposed`); a file that is not valid JSON/object → `config-unreadable` (names the path). Written only by enrollment (whole-file template, 0600, run-as user); re-enrollment overwrites it from the new `--expose` set.

## E3. Connector state record (`/var/lib/remo-connector/connector.json`)

```json
{
  "version": 1,
  "name": "lxc-connector",
  "node_id": "mi-0123456789abcdef0",
  "region": "eu-central-1",
  "run_as_user": "remo-connector",
  "remo_version": "4.4.0",
  "document_version": 1,
  "enrolled_at": "2026-09-27T12:00:00Z"
}
```

Written by the role after registration; read by `remo connector status` (over SSH, via `sudo cat`) and removed by `unenroll`. `node_id` comes from `/var/lib/amazon/ssm/registration` (`ManagedInstanceID`).

## E4. Session document (`src/remo_cli/core/remo_attach_document.json`)

Constants in `core/connector.py`: `DOCUMENT_NAME = "remo-attach"`, `DOCUMENT_VERSION = 1`, `TARGET_PATTERN = r"^[A-Za-z0-9_-]{1,1000}$"`, `TARGET_MAX_CHARS = 1000`, `PROJECT_MAX_BYTES = 255`, `LAUNCHER_PATH = "/opt/remo-connector/bin/remo"`, `STATE_DIR = "/var/lib/remo-connector"`, `RUN_AS_USER_DEFAULT = "remo-connector"`. The file content is the contract in [contracts/session-document.md](contracts/session-document.md); a unit test asserts the constants and the file agree (one parameter, that pattern, that command).

## E5. Connector registry (`/var/lib/remo-connector/registry.json`)

A standard registry v2 file (`core/registry.py`), containing only `type: "ssh"` entries, one per exposed host, produced on the workstation by `core/registry.known_host_to_entry` from a `KnownHost(type="ssh", name=<operator name>, host=<address or "localhost">, user=<host.user>, instance_id=str(port), region="/var/lib/remo-connector/id_ed25519")` and templated onto the connector. No schema change; the launcher reads it through `get_known_hosts()` with `REMO_HOME` pointed at the state dir.

## E6. Error line (`core/connector.py:ErrorCode`, `format_error_line`)

`remo-connector-error: <code> <message>` — one line, stdout, flushed, exit 1. Codes: `bad-target`, `unsupported-version`, `invalid-name`, `not-exposed`, `run-as-not-in-effect`, `config-unreadable`, `ssh-missing`. The message is a single line (newlines replaced by spaces), states what failed and the fix (Constitution III), and never echoes a decoded project or host name for `not-exposed`.

## E7. Enrollment inputs (workstation → playbook, via the 0600 vars file + `-e` pairs)

| Var | Secret | Source |
|-----|--------|--------|
| `ssm_activation_code` | **yes** (vars file only, `no_log`) | prompt / stdin |
| `ssm_activation_id`, `ssm_region` | no | `--activation-id`, `--region` |
| `connector_name` | no | positional `NAME` |
| `connector_run_as_user` | no | `--run-as-user` (default `remo-connector`) |
| `remo_version` / `remo_source` | no | `--remo-version` (default: the enrolling CLI's version) / `--remo-source git+…@ref` (Tier 1 testing) |
| `remo_ssh_host/user/port/identity/common_args` | no | connector's `KnownHost` via `build_ssh_opts` |
| `connector_exposures` | no (vars file, JSON) | parsed `--expose HOST/PROJECT` (split on the **last** `/`; a project never contains `/`) |
| `connector_registry_json` | no (vars file) | serialized E5 |
| `connector_exposed_hosts` | no (vars file) | per exposed host: name, address, user, port, identity, common_args (for play 3) |

Validation before any playbook runs: `NAME` exists in the operator registry; every exposed host exists and is directly reachable (not AWS SSM mode); every project passes `validate_project_name`; the code is non-empty; `--run-as-user` is not `root` or `ssm-user`.

## State transitions

```
(not enrolled) --enroll--> enrolled(node_id) --enroll (same inputs)--> enrolled (no change)
enrolled --enroll (new --expose set)--> enrolled (registry/exposure/known_hosts/authorized_keys converge)
enrolled --unenroll--> unregistered (agent stopped+disabled, local registration cleared, connector.json gone)
unregistered --unenroll --purge--> (state dir, /opt/remo-connector, run-as user removed)
```
