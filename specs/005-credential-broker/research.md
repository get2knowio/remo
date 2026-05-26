# Phase 0 Research: Credential Broker

Date: 2026-05-25
Branch: 005-credential-broker

This document records the decisions and rationale for cross-cutting questions that surfaced while filling Technical Context. The broker daemon's *internal* design is owned by `get2knowio/remo-broker`'s `specs/001-broker-daemon/research.md` and is referenced rather than duplicated.

## R1 — Laptop-side `fnox` invocation shape

**Decision**: Subprocess via `subprocess.run(["fnox", "get", name], capture_output=True, text=True, env=…)`. On any non-zero exit, raise `core.fnox.FnoxError` with the captured stderr line. Presence-check at `remo init` via `fnox --version`; refuse to proceed if not installed and surface the upstream install pointer (https://github.com/jdx/fnox#install).

**Rationale**: A single subprocess per provisioning operation is fine — `remo create` already shells out heavily (ansible-playbook, ssh) and one extra ~50 ms per `fnox get` is invisible inside a 30 s provisioning flow. Importing fnox-core as a Rust library is correct for the broker (high-frequency hot path) but unjustified on the laptop (low frequency, simpler to ship, lets users upgrade fnox independently). Using `lookup('pipe', 'fnox get HETZNER_API_TOKEN')` in Ansible inherits the same subprocess behavior naturally.

**Alternatives**:
- *PyO3 bindings to fnox-core*: rejected — adds compile step to the Python install, no measurable benefit at provisioning frequency.
- *fnox HTTP shim*: rejected — fnox doesn't ship a server mode by design.

## R2 — Hetzner bootstrap-token SSH push

**Decision**: After `hcloud server create` returns and the server's SSH host key is known (we already wait for it in the existing `_wait_for_ssh` helper in `providers/hetzner.py`), call a new helper `providers/hetzner._push_bootstrap_token(server, token)` that:

1. Mints a per-instance sub-token via the laptop's admin SA (1Password SCIM, Vault token-create, etc. — backend-dispatched in `providers/broker.py`).
2. SSH-pipes the token bytes via `ssh root@<ip> 'install -D -m 0400 -o root -g root /dev/stdin /etc/remo-broker/bootstrap-token'` reading from stdin (token never appears as an SSH argv).
3. Records the sub-token's backend identifier (not the secret) in a per-instance metadata file so `remo destroy` can revoke it later.

The token is **not** included in cloud-init user-data (Hetzner's console shows user-data by default; this would be visible to anyone with console access). SC-002 verification asserts this.

**Rationale**: `install -D -m 0400 … /dev/stdin` is atomic, idempotent, and avoids any temporary world-readable file. Stdin keeps the secret out of `ps`-visible argv. Order-of-operations (mint → push → record) ensures we never have an unrevocable orphan token.

**Alternatives**:
- *Cloud-init `write_files` with token*: rejected — Hetzner cloud-init user-data is retrievable via the panel and via metadata service.
- *Hetzner Cloud secret-injection*: not available; provider has no first-class secret-injection primitive.

## R3 — AWS bootstrap via instance profile

**Decision**: At `remo aws create`:

1. Ensure a per-developer-per-region IAM role `remo-broker-instance-<dev>` exists (idempotent via `iam:GetRole` → `iam:CreateRole`); attach a minimal trust policy for `ec2.amazonaws.com`.
2. Ensure a per-instance scoped inline policy or managed policy that grants `secretsmanager:GetSecretValue` only on ARNs matching this instance's allowed-secret prefix (the names referenced by the project manifests on that instance — but since manifests are per-project and projects are mobile, the simplest correct cut is: allow on a tag-based ARN pattern `arn:aws:secretsmanager:*:*:secret:remo/<dev>/*`).
3. Create an instance profile of the same name and attach the role to it.
4. Pass `IamInstanceProfile={"Name": …}` in the existing `boto3 ec2.run_instances` call.
5. Broker on the instance uses IMDSv2 to retrieve role creds; no on-disk token.

Revocation on `remo destroy` detaches and deletes the instance-specific role *after* `ec2.terminate_instances` (because EC2 holds the role attachment while running) and removes any STS tokens via `iam:UpdateAssumeRolePolicy` to deny-all as a fast revocation. SC-005 budget (60 s) accommodates STS propagation.

**Rationale**: Instance profile is the AWS-native answer; it sidesteps the on-disk token problem entirely and works cleanly over SSM (the existing AWS access mode). The per-developer-per-region scoping aligns with the per-developer admin SA model decided in Clarifications Q5.

**Alternatives**:
- *Single shared role*: rejected — violates the "per-developer scoping" principle from Q5.
- *AWS IAM Roles Anywhere*: overkill, requires cert lifecycle work, no benefit over native instance profile.

## R4 — Incus/Proxmox bind-mount of per-instance token

**Decision**: On the node, the token-manager helper writes the per-instance token to `/var/lib/remo-broker/instance-tokens/<developer>/<instance>` (mode 0400, root). The container config (Incus or Proxmox LXC) gets a bind-mount entry:

- Incus: `lxc config device add <instance> remo-broker-token disk source=/var/lib/remo-broker/instance-tokens/<dev>/<instance> path=/etc/remo-broker/bootstrap-token readonly=true`
- Proxmox: `pct set <vmid> -mp0 /var/lib/remo-broker/instance-tokens/<dev>/<vmid>,mp=/etc/remo-broker/bootstrap-token,ro=1` (single-file mp works since Proxmox 7.x)

The container's `remo-broker.service` opens this file at start; if the file is unreadable the broker fails-fast with a clear systemd-status message (Principle IV).

**Rationale**: Bind-mount RO from outside is the strongest available guarantee that the container/instance never has write access to its own bootstrap token. Combined with TPM2 sealing of the underlying file on TPM-equipped nodes (currently opt-in, OQ-6 still open), an offline disk read from inside the instance yields nothing usable.

**Alternatives**:
- *systemd `LoadCredential` from a node-side daemon*: less portable across distros and harder to inspect.
- *Push via SSH at container start*: rejected — would require per-start coordination from the node and creates a window where the token lives in container RAM but the broker isn't ready yet.

## R5 — Devcontainer auto-synthesis language detection

**Decision**: New `core/devcontainer.py` runs a simple file-marker scan when `cli/shell.py` enters a project without `.devcontainer/devcontainer.json` and without `.remo/devcontainer.json`. Priority order, first match wins:

| Marker | Synthesized base image |
|---|---|
| `package.json` | `mcr.microsoft.com/devcontainers/javascript-node:20` |
| `pyproject.toml` or `requirements.txt` or `Pipfile` | `mcr.microsoft.com/devcontainers/python:3.12` |
| `Cargo.toml` | `mcr.microsoft.com/devcontainers/rust:1` |
| `go.mod` | `mcr.microsoft.com/devcontainers/go:1.22` |
| `Gemfile` | `mcr.microsoft.com/devcontainers/ruby:3` |
| (none of the above) | `mcr.microsoft.com/devcontainers/base:ubuntu-24.04` |

The synthesized file lives at `.remo/devcontainer.json` (gitignored via the same hook that creates `.remo/`); contains the broker-socket bind-mount declaration plus the matched base image. Project menu always launches via `devcontainer up`; no instance-OS fallback (FR-017).

**Rationale**: The marker set covers >90% of typical projects with zero false negatives. Microsoft-published images are the de-facto baseline and re-evaluated by upstream regularly. Putting the synthesized file under `.remo/` (not `.devcontainer/`) keeps it out of the repo, so committed devcontainer config (when added later) always wins via FR-012's priority order.

**Alternatives**:
- *LSP-style language detection*: rejected — overkill, requires a parser stack.
- *Always-Ubuntu fallback only*: rejected — defeats the value of an immediately-useful devcontainer for Node/Python/Rust projects, which are the bulk of typical Remo usage.

## R6 — Cross-repo manifest schema versioning

**Decision**: The remo-broker repo owns `docs/manifest-schema.md` (authoritative TOML schema) and publishes a generated `manifest-schema-v<N>.json` (JSON Schema Draft 2020-12) per release. The Remo side:

1. Pins a manifest schema version per Remo release in `core/manifest.py` (`SUPPORTED_SCHEMA_VERSIONS = {1, 2}` initially `{1}`).
2. Fetches the JSON Schema file from the remo-broker GitHub Releases asset at install time and caches under `~/.cache/remo/manifest-schema-v<N>.json`. CI vendors the latest as a fallback baked into the wheel.
3. Validates synthesized + user-committed manifests with `jsonschema.validate(...)` before sending the broker anything; surfaces validation errors with line numbers (TOML position info from `tomllib`).
4. The broker on the instance independently validates incoming manifests on socket-creation and refuses unknown `schema_version` values with a clear error logged to the audit file.

**Rationale**: Double validation (laptop + broker) catches both stale Remo clients (refuses to push a schema the broker won't accept) and stale brokers (refuses to serve a schema the broker doesn't grok). The `schema_version` integer is a smaller, safer surface than a free-form string.

**Alternatives**:
- *Single-side validation (broker only)*: rejected — users would only see schema errors after a failed devcontainer launch, not at edit time.
- *Inline JSON Schema in every release*: more cache-stable but couples broker version bumps to Remo bumps tighter than needed; the GitHub-release-asset approach lets Remo lag broker minor versions safely.

## R7 — Audit log format

**Decision**: The broker writes one JSON line per access decision to `/var/log/remo-broker/audit.log` (mode 0600, root). Each line:

```json
{"ts":"2026-05-25T10:42:13.512Z","project":"foo","socket":"/run/remo-broker/foo.sock","secret":"GITHUB_TOKEN","decision":"allow","reason":"in-manifest","fetched_from":"1password","cache":"hit"}
```

`reason` is one of `in-manifest`, `not-in-manifest`, `manifest-missing`, `backend-error`, `interactive-required`. `cache` is `hit` / `miss` / `none`. No secret values are logged.

The `remo audit <instance>` command runs `ssh <instance> 'sudo cat /var/log/remo-broker/audit.log'` (or `tail -n N`) and renders it as a table by default, with `--json` for raw output.

**Rationale**: JSON-lines is grep-friendly, parser-stable, and matches the broker's log appender (which is line-buffered for crash safety). The explicit `decision`/`reason` split makes the SC-002 supply-chain-attack assertion mechanically testable: count denial lines per project and assert no secret leaked outside the manifest.

**Alternatives**:
- *Structured `journalctl` JSON*: rejected — would require `--unit=remo-broker.service` filtering, and we want a stable on-disk file for offline forensics after `journald` rotation.
- *CSV*: rejected — quoting hell for `reason` strings containing commas.

## R8 — `nodes.yml` storage shape and bootstrap-admin SA reference

**Decision**: `~/.config/remo/nodes.yml` (laptop, per-developer) stores:

```yaml
version: 1
nodes:
  - name: workstation-01
    provider: incus
    host: 192.168.4.10
    ssh_user: incusadmin
    admin_sa_fnox_key: "incus_workstation_01_admin_sa"  # the fnox key under which this developer's admin SA lives
    registered_at: 2026-05-25T10:00:00Z
  - name: lab-prox-02
    provider: proxmox
    host: 10.0.0.42
    ssh_user: root
    admin_sa_fnox_key: "proxmox_lab_prox_02_admin_sa"
    registered_at: 2026-05-23T08:12:11Z
```

The admin SA token itself lives only in laptop fnox under `admin_sa_fnox_key`. `nodes.yml` is mode 0600. Multi-developer scenarios produce one `nodes.yml` per developer's laptop; the node-side helper stores per-developer subdirectories under `/var/lib/remo-broker/instance-tokens/<dev>/`.

**Rationale**: Indirecting through fnox keeps the high-value admin SA in one auditable location (fnox) and avoids two places to compromise. Per-developer subdirectories on the node give kernel-level path separation between developers' instance tokens even on shared hardware.

**Alternatives**:
- *Single `~/.config/remo/admin-sa-tokens` flat file*: rejected — duplicates fnox's responsibility and adds another secrets-on-disk surface.
- *No registry at all (re-prompt each time)*: rejected — operator ergonomics fail when a developer manages 5+ nodes.

## R9 — Backend revocation API per backend

**Decision**: `providers/broker.py` exposes a `revoke_bootstrap_token(backend, token_id)` dispatcher with backend-specific implementations:

| Backend | Revocation primitive |
|---|---|
| 1Password | SCIM API `DELETE /v2/ServiceAccountTokens/<id>` (the admin SA must have SCIM permission). |
| Vault / OpenBao | `POST /v1/auth/token/revoke` with the token accessor. |
| AWS Secrets Manager | Revocation is implicit in deleting the per-instance IAM role + STS deny update (see R3). |
| age + git | No backend-side revocation primitive; the spec's FR-003 init-time warning applies. Revoke = remove the recipient key from `age` recipients list and re-encrypt; out of scope for this release if the warning has steered the user to a different backend. |

Each impl is idempotent (revoking an already-revoked token returns success) so that destroy-flow failures can be retried.

**Rationale**: Each backend has a single canonical revocation primitive; surfacing it through one dispatcher means `remo destroy` and `remo rotate-bootstrap` share the same code path. SC-005 (60 s budget) is comfortable for all four — 1Password SCIM and Vault token revoke propagate in seconds; AWS STS propagation is also seconds in practice.

**Alternatives**:
- *Time-based expiry only (no active revocation)*: rejected — leaves a window after destroy where a leaked token still works, defeating User Story 5.

## Resolved status

All Technical Context items previously marked NEEDS CLARIFICATION are now resolved. The four still-open spec questions (OQ-3, OQ-6, OQ-7, OQ-8) are either lower-impact operational policies (OQ-3, OQ-6), already-closed-elsewhere (OQ-7 closed in spec.md §Component Sourcing), or owned by the remo-broker repo (OQ-8 wire protocol). None block Phase 1.
