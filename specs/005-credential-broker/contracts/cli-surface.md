# Contract: CLI Surface

Date: 2026-05-25
Branch: 005-credential-broker

All new and modified `remo` commands introduced by this feature. The naming style and `--help` shape match the existing `remo {provider} {action}` conventions in `cli/providers/`.

## Modified commands

### `remo init`

Adds backend selection + fnox detection.

Prompts (interactive) or accepts flags:

```
remo init [--backend {1password|vault|aws-sm|age-git}] [--admin-sa-fnox-key KEY] [--non-interactive]
```

Behavior:
- Refuses to proceed if `fnox --version` is not on PATH; surfaces install pointer (research R1).
- On `--backend age-git`: emits the FR-003 warning ("no per-instance scoping primitives — bootstrap tokens for Hetzner/Incus/Proxmox will be downgraded to laptop-unlock-per-session, AWS unaffected"); requires explicit `--accept-downgrade` to proceed.
- Refuses any backend identity type that requires interactive authentication for retrieval (FR-003a / Clarifications Q2). On 1Password the laptop-side `op signin` may still be interactive; what's refused here is configuring an *identity that the broker would have to use interactively* (e.g., providing a personal account with biometric unlock instead of a Service Account token).
- Writes laptop-side fnox configuration locating the chosen backend; does not write any secret values.

Exit codes:
- `0` success.
- `2` user declined a required warning (e.g., age-git without `--accept-downgrade`).
- `3` `fnox` not installed.
- `4` interactive identity rejected.

### `remo destroy <instance>`

Adds pre-deletion bootstrap-token revocation (FR-020).

Order of operations:
1. Resolve instance from `known_hosts`.
2. Look up the bootstrap-token's backend-side identifier from provider tags / `nodes.yml`.
3. Call `revoke_bootstrap_token(backend, token_id)` (research R9). On failure, abort destroy with a clear message and a `--force` escape hatch that documents the leaked-token risk.
4. Perform the existing provider-specific destroy (existing behavior).

Exit codes: existing + `5` (revocation failed, `--force` not provided).

### `remo {hetzner,aws,incus,proxmox} create`

Existing commands extend the `*_configure.yml` Ansible playbook invocation to include the new `broker_install` role and provider-specific bootstrap delivery (research R2/R3/R4). No new flags; behavior changes are server-side.

## New commands

### `remo {incus,proxmox} add-node <name>`

```
remo incus  add-node NAME --host HOST [--ssh-user USER] --admin-sa-fnox-key KEY
remo proxmox add-node NAME --host HOST [--ssh-user USER] --admin-sa-fnox-key KEY
```

Behavior:
- Validates `name` against `^[a-z][a-z0-9-]{0,31}$`.
- Refuses if `name` already present in `nodes.yml` (idempotency: prints "already registered" + exit `0` if all fields match; exit `6` if fields differ — operator must `remove-node` first).
- SSHes to `HOST` as `USER` (default: `root` for proxmox, `incus` for incus) and installs the token-manager helper under `/usr/local/libexec/remo-broker-tokens` (idempotent — re-running is a no-op when helper version matches).
- Creates `/var/lib/remo-broker/instance-tokens/<dev>/` on the node, owned by the helper.
- Writes the node entry to `~/.config/remo/nodes.yml`.

### `remo rotate-bootstrap [<instance>]`

```
remo rotate-bootstrap                    # rotate every instance whose cadence is due
remo rotate-bootstrap <instance>         # rotate one specific instance immediately
remo rotate-bootstrap --all              # rotate all instances regardless of cadence
```

Behavior:
- For each in-scope instance: mint fresh sub-token via backend → deliver via provider-specific transport → confirm broker reload → revoke previous sub-token.
- Idempotent: an instance rotated within the last hour declines re-rotation unless `--force` is given (Principle III).
- Default cadence: 7 days (FR-021 / Clarifications Q3). Per-instance override read from `known_hosts` metadata column added in this feature.

Exit codes: `0` (success) / `7` (one or more rotations failed; partial-success report printed).

### Passive overdue reminder

Every `remo` invocation (any subcommand) ends by printing a one-line yellow warning per instance whose `remo_last_rotation_at` + `remo_rotation_cadence_days` (Hetzner labels) — or equivalent AWS tags `remo:last-rotation-at` + `remo:rotation-cadence-days` — indicates an overdue rotation. The check is short-circuited if any of: cadence=0 (disabled), no metadata tags present (pre-feature instance), or the user passed `--quiet`.

### `remo audit <instance> [--tail N] [--json] [--since DURATION]`

```
remo audit web-1 --tail 200
remo audit web-1 --since 1h
remo audit web-1 --json | jq 'select(.decision=="deny")'
```

Behavior:
- SSHes to instance, runs `sudo cat /var/log/remo-broker/audit.log` (or `tail -n N` / a journalctl-style filter for `--since`).
- Default render: table grouped by project, columns `ts | project | secret | decision | reason | cache`.
- `--json` emits the raw JSON-lines (research R7).

Exit codes: `0` (records found) / `8` (broker not installed / audit log missing).
