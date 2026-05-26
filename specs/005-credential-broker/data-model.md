# Phase 1 Data Model: Credential Broker

Date: 2026-05-25
Branch: 005-credential-broker

Entities owned by this repo (Remo laptop CLI + Ansible). Entities owned by the `remo-broker` repo (broker process internals, in-memory cache shape, wire frame structs) are not duplicated here; this doc describes the contract surfaces Remo touches.

## Entity: Node (NEW)

Represents an Incus or Proxmox node registered by a developer with Remo. Persisted in the laptop's `~/.config/remo/nodes.yml` (mode 0600).

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Developer-chosen short identifier; unique within this developer's `nodes.yml`. `^[a-z][a-z0-9-]{0,31}$`. |
| `provider` | enum: `incus` \| `proxmox` | yes | |
| `host` | string | yes | SSH-reachable hostname or IP. |
| `ssh_user` | string | yes | The non-root user (or `root` for Proxmox) the laptop SSHes as for node-side helper calls. |
| `admin_sa_fnox_key` | string | yes | Key under which this developer's backend admin SA token is stored in laptop fnox. Never the token value itself. |
| `registered_at` | RFC3339 string | yes | Set by `remo <provider> add-node`. |

Identity & uniqueness:
- `(name)` unique within one developer's `nodes.yml`.
- Multiple developers registering the *same* physical node each have an independent entry in their own `nodes.yml` and an independent `admin_sa_fnox_key`. The node side dispatches by developer (see R8).

Lifecycle:
- Created by `remo {incus,proxmox} add-node <name> --host … --admin-sa-key …`.
- Read by every `remo {incus,proxmox} create` and `remo {incus,proxmox} destroy` to look up node-side credentials.
- Removed by `remo {incus,proxmox} remove-node <name>` (out of scope for this release — manually edit if needed).

Not used for AWS or Hetzner.

## Entity: KnownHost (EXISTING, unchanged)

`~/.config/remo/known_hosts`, colon-delimited. Continues to represent L2 instances. **No schema changes** required for this feature — the new bootstrap-token lifecycle hangs off provider-side metadata (instance tags / labels) and the laptop-side `nodes.yml`, not the per-instance registry.

## Entity: BootstrapToken (no laptop-side persistence — opaque)

The per-instance backend identity the broker uses to authenticate upward. Never modeled as a laptop-side dataclass; only referenced as an opaque string in flight.

Storage by layer:

| Layer | Location | Mode | Notes |
|---|---|---|---|
| Backend | n/a (token is an artifact of the backend's identity primitive) | n/a | Has a backend-side identifier (1Password SCIM ID, Vault accessor, AWS role+instance-id) which Remo records in provider tags for later revocation. |
| Node (Incus/Proxmox only) | `/var/lib/remo-broker/instance-tokens/<dev>/<instance>` | 0400 root | One per dev × instance. |
| Instance (Incus/Proxmox) | `/etc/remo-broker/bootstrap-token` (bind-mounted RO from node) | 0400 root | Container has no write access. |
| Instance (Hetzner) | `/etc/remo-broker/bootstrap-token` | 0400 root | SSH-pushed at create time; TPM2 sealing opt-in (OQ-6). |
| Instance (AWS) | absent on disk | n/a | Broker uses IMDSv2 to fetch role creds; no token file. |

State transitions:
- `mint` → `deliver` → `serve` → `rotate` (mints fresh, delivers, revokes old) → `revoke` (on destroy, before instance deletion per FR-020).
- Default rotation cadence: 7 days (Clarifications Q3 → FR-021).

Rotation metadata: cadence days and last-rotation timestamp are stored as provider-side tags/labels (Hetzner labels `remo_rotation_cadence_days`, `remo_last_rotation_at`; AWS tags `remo:rotation-cadence-days`, `remo:last-rotation-at`) — never on the laptop. Hetzner label keys disallow `:`, so the underscore form is the canonical key on that provider. This keeps cadence with the instance across multi-device use and preserves KnownHost's "unchanged" contract.

## Entity: ProjectManifest (NEW)

TOML file declaring the secret allowlist for one project. Discovered in priority order (FR-012):

1. `<project>/.devcontainer/remo-broker.toml` (committed)
2. `<project>/.remo/broker.toml` (auto-synthesized, gitignored)

Schema version is owned by `remo-broker/docs/manifest-schema.md`. This repo consumes JSON Schema v1 (initial). Minimal valid manifest:

```toml
schema_version = 1
[mcp]
secrets = ["github_token"]
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | integer | yes | Initial supported: `1`. Unknown values rejected by broker. |
| `[mcp].secrets` | array of strings | yes | Backend-resolvable secret names. Names use `lower_snake_case` to match fnox conventions. |
| `[mcp].notes` | string | no | Human comment, surfaced in `remo audit`. |

Validation:
- TOML parse via `tomllib` (Python 3.11+ stdlib).
- JSON-Schema validation against the cached schema file (see research R6).
- Each secret name MUST match `^[a-z][a-z0-9_]{0,63}$` (laptop-side pre-check; broker re-validates).

Lifecycle:
- Synthesized by `remo shell` if absent, with default `secrets = ["github_token"]` (FR-013) and a header comment explaining the file.
- Mutated by the developer (free-form TOML edits).
- Re-read by the broker on each devcontainer start (per Clarifications Q1: socket created per devcontainer-lifetime).

## Entity: ProjectSocket (ephemeral, broker-owned)

Unix domain socket at `/run/remo-broker/<project>-<pathhash>.sock` on the instance. Created when a devcontainer starts; removed when the devcontainer exits (per Clarifications Q1, FR-014, FR-016).

| Attribute | Value |
|---|---|
| Path | `/run/remo-broker/<project_name>-<sha256(abs_path)[:8]>.sock` (pathhash suffix avoids collisions per spec.md Edge Cases) |
| Mode | 0600, owned by the devcontainer user UID (mapped via `RemainAfterExit` + chown handler in `remo-broker.service`) |
| Mount into devcontainer | bind-mount as `/run/remo-broker/sock` (FR-015) |
| Lifetime | One devcontainer instance = one socket creation/removal cycle |

State transitions:
- `pre-mount` → `create on devcontainer-up hook` → `serve (allowlist enforced)` → `remove on devcontainer exit`.

Not persisted on the laptop side; the laptop only declares the mount in the synthesized/committed `devcontainer.json`.

## Entity: Broker (process, instance-resident)

The `remo-broker` daemon. Managed as a systemd unit (`remo-broker.service`). Remo's view of the broker is purely operational; its internal data structures live in the remo-broker repo.

Remo-side observable state:

| Property | Where checked |
|---|---|
| Process up | `systemctl is-active remo-broker.service` via SSH from `remo` |
| Audit log presence | `/var/log/remo-broker/audit.log` (see research R7) |
| Version | `remo-broker --version` (must satisfy the pinned-version range per Remo release) |
| Bootstrap token loaded | inferred from successful `remo audit` and absence of `interactive-required` / `backend-error` lines |

Not modeled as a laptop-side dataclass — the broker is a contract, not a record.

## Cross-entity invariants

- For any instance in a developer's `known_hosts`, exactly one bootstrap-token storage location (table above per provider) exists, and the developer's `nodes.yml` (for Incus/Proxmox) has the matching node entry with a valid `admin_sa_fnox_key`. Violation = `remo audit` warns; install/configure refuses to proceed.
- No `~/.config/remo/*` file persisted by this feature contains a secret value. Secrets live only in laptop fnox + on the instance under broker control.
- A project may have at most one active socket at a time per instance (re-entering a project that already has a devcontainer up reuses the socket rather than creating a parallel one).
