# Credential Broker — Operator Runbook + Threat Model

Status: shipping with Remo 2.1 (broker daemon owned by `get2knowio/remo-broker`)

## What this gets you

Long-lived developer credentials no longer live on Remo instances or in laptop
shell env. Each instance runs `remo-broker.service`, which fetches narrowly-
scoped secrets from your configured backend and serves them to each project's
devcontainer over a per-project Unix socket.

| Property | Before | After |
|---|---|---|
| Project secrets at rest on instance | Yes (`~/.aws`, `~/.npmrc`, `~/.netrc`) | None — broker holds them in RAM only |
| Provisioning creds on laptop | `HETZNER_API_TOKEN`, `AWS_ACCESS_KEY_ID` in shell env | Stored in laptop `fnox`; `lookup('pipe', 'fnox get ...')` in Ansible |
| Per-instance scoping | Shared developer creds | Per-developer-per-instance sub-tokens (mintable, revocable) |
| Token revocation on `remo destroy` | None — leaked tokens lived forever | Backend-side revoke BEFORE provider-side delete (SC-005: ≤60 s window) |
| Multi-device access | Required per-device dot-file setup | Devices are interchangeable as long as you're authed to the backend |

## Threat model

What this defends against:

- **Supply-chain attack inside a project**: a malicious dependency cannot
  read project secrets from disk because they're never on disk. Allowlist
  is enforced per-project via the manifest (`remo-broker.toml`).
- **Compromised devcontainer**: the broker only serves secrets named in the
  project's manifest. Requests for other secrets are logged as `deny` and the
  cache never holds out-of-manifest values.
- **Compromised laptop shell session**: provisioning creds are not in
  `printenv`; only `fnox` has them, and `fnox`'s identity may be biometric
  or hardware-keyed.
- **Multi-developer node share** (Incus/Proxmox): per-developer subdirectories
  on the node, per-developer admin SA in laptop `fnox`. Compromising one dev's
  laptop yields only their slice of the node.

What this does **not** defend against:

- **Root on the instance**: the broker runs as root; root can read
  `/etc/remo-broker/bootstrap-token` and impersonate the broker.
- **Compromised backend SCIM / Vault root token**: per backend; a stolen
  admin SA is a full compromise of everything that admin SA can mint.
- **age + git backend**: provides no per-instance revocation primitive.
  Acknowledged at `remo init` time; documented downgrade.

## Operator runbook

### One-time laptop setup

```bash
# 1. Install fnox: https://github.com/jdx/fnox
fnox --version

# 2. Store provisioning + admin SA creds in fnox
fnox set hetzner_api_token            # paste Hetzner API token
fnox set aws_access_key_id            # paste AWS access key
fnox set aws_secret_access_key

# Admin SA tokens (one per backend identity)
fnox set incus_ws_01_admin_sa         # 1Password / Vault admin SA for self-hosted node

# 3. Initialize Remo's backend selection
remo init --backend 1password
```

`remo init` will refuse if `fnox` is not on PATH (exit 3) or if you try to use
an interactive backend identity that needs human unlock (exit 4 — autonomous
overnight agents can't satisfy biometric prompts).

### Per-node setup (Incus / Proxmox only)

```bash
remo incus add-node workstation-01 \
  --host 192.168.4.10 \
  --ssh-user incusadmin \
  --admin-sa-fnox-key incus_workstation_01_admin_sa
```

This installs the node-side token-manager helper and writes
`~/.config/remo/nodes.yml` (mode 0600) — re-running is a no-op when fields match.

### Per-instance lifecycle

```bash
# Create — broker installed + bootstrap token delivered automatically.
remo aws create dev-1
remo hetzner create hetz-1
remo incus create lxc-1 --node workstation-01

# Rotate — mints fresh + revokes old. Refuses if rotated <1 h ago.
remo rotate-bootstrap dev-1
remo rotate-bootstrap --all          # all overdue instances

# Inspect what the broker has been doing.
remo audit dev-1 --tail 200
remo audit dev-1 --since 1h --json | jq 'select(.decision=="deny")'

# Destroy — revokes the token at the backend BEFORE deleting the instance.
remo destroy dev-1
# Exit 5 if revocation failed; pass --force only if you accept the risk.
```

### Project manifest

Each project declares which backend secrets the broker may serve to its
devcontainer in `.devcontainer/remo-broker.toml` (committed) or, if missing,
`.remo/broker.toml` (auto-synthesized + gitignored).

```toml
schema_version = 1

[mcp]
secrets = ["github_token", "npm_token"]
notes   = "Frontend project; needs gh + npm publish."
```

Changes take effect on next devcontainer restart.

## Failure modes (Principle IV: fail fast)

| Symptom | Likely cause | Fix |
|---|---|---|
| `remo init` exits 3 | `fnox` not installed | Install per the upstream README; re-run |
| `remo init` exits 4 | Interactive backend identity selected | Configure a Service Account / AppRole / IAM principal |
| `remo destroy` exits 5 | Backend revocation failed | Investigate backend; rerun with `--force` only if you accept the orphan-token risk |
| `remo audit` exits 8 | Broker not installed / audit log missing | Re-run `remo {provider} create` (the configure flow includes broker_install) |
| Broker won't start on instance | `bootstrap-token` file missing | Re-run the per-provider configure playbook; check `journalctl -u remo-broker` |
| `gh auth status` fails inside devcontainer | Secret not in manifest | Add it to `[mcp].secrets` and restart the devcontainer |
| Overdue rotation reminder appears | `remo:rotation-cadence-days` exceeded since `remo:last-rotation-at` | Run `remo rotate-bootstrap <instance>` |

## Cross-repo split

The broker daemon (Rust, `fnox-core` integration, wire protocol, in-memory
cache, audit-log appender) lives in `get2knowio/remo-broker`. This repo owns:

- Laptop-side `fnox` subprocess wrapper
- Ansible `broker_install` role + per-provider `bootstrap_token_*` assertion roles
- `remo audit` / `remo rotate-bootstrap` CLI surface
- Project manifest TOML schema (consumer side; authoritative schema published
  per release by remo-broker)
- Provider-specific bootstrap delivery (SSH push / IMDS / bind-mount)

Broker binary releases are signed; the install role verifies the SHA-256
before placing the binary at `/usr/local/bin/remo-broker`.
