# Implementation Plan: Credential Broker

**Branch**: `005-credential-broker` | **Date**: 2026-05-25 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/005-credential-broker/spec.md`

## Summary

Remove long-lived developer credentials from Remo instances. Replace ambient env vars and dotfile-resident secrets with an on-instance broker daemon (`remo-broker`, Remo-owned, Rust, lives in the separate `get2knowio/remo-broker` repo) that fetches narrowly-scoped, allowlisted secrets from a configurable backend (1Password / Vault / AWS SM / age + git) and serves them to each project's devcontainer over a per-project Unix socket.

This spec's deliverables in this repository are the **laptop CLI + Ansible** half of the system:

1. Switch all provisioning-credential lookups from `lookup('env', ...)` to `lookup('pipe', 'fnox get ...')` in `ansible/group_vars/`, so the Hetzner/AWS/Incus/Proxmox API tokens never reach the laptop shell environment, let alone the instance.
2. Add bootstrap-token minting + delivery into each provider's `*_configure.yml` Ansible flow, with provider-specific transport: AWS instance-profile (no on-disk token), Hetzner SSH-push, Incus/Proxmox node-bind-mount.
3. Install the broker binary + systemd unit during the same configure run; ship a default `[Service]` that reads `/etc/remo-broker/bootstrap-token` and exposes `/run/remo-broker/<project>.sock`.
4. Introduce a `Node` registry (Incus/Proxmox only) plus new commands: `remo incus add-node`, `remo proxmox add-node`, `remo rotate-bootstrap`, `remo audit`.
5. Extend `remo init` to choose + persist a backend, refuse interactive-auth identity types (per Clarifications Q2), and warn on backends lacking per-instance scoping (age + git).
6. Synthesize a `.remo/devcontainer.json` for projects without one so the project menu never falls back to the instance OS shell.
7. Wire `remo destroy` to revoke the bootstrap token at the backend before deleting the instance.

The broker daemon's own internals (wire protocol, fnox-core integration, in-process cache, audit-log shape) are owned by the `remo-broker` repo's `specs/001-broker-daemon/` and are out of scope here; this plan treats it as a versioned signed binary plus a JSON-Schema-versioned TOML manifest contract.

## Technical Context

**Language/Version**: Python 3.11+ (laptop CLI), Ansible 2.14+ (instance provisioning). Broker daemon is Rust + `fnox-core` — owned by `get2knowio/remo-broker`, consumed here as a signed binary release (cross-repo, version-pinned).
**Primary Dependencies**: Click 8.1+ (CLI), InquirerPy 0.3.4+ (interactive picker, existing), `tomllib` (stdlib — manifest read), `jsonschema` (new, for validating manifests against the schema published by remo-broker), `fnox` CLI on the laptop (subprocess via `lookup('pipe', ...)`), `boto3` (lazy, AWS instance-profile attach), `hcloud` (lazy, Hetzner). Ansible: `ansible.builtin`, `community.general` (existing).
**Storage**:
- Laptop: `~/.config/remo/known_hosts` (existing flat file, unchanged), `~/.config/remo/nodes.yml` (NEW — Node registry: name, host, provider, registered admin SAs are *not* stored here — only references to fnox keys), laptop-side `fnox` keystore (per-developer secret store).
- Instance: `/etc/remo-broker/bootstrap-token` (mode 0400 root; absent on AWS where IMDS is used), `/run/remo-broker/<project>.sock` (ephemeral), `/var/log/remo-broker/audit.log` (append-only, root-readable).
- Node (Incus/Proxmox only): `/var/lib/remo-broker/instance-tokens/<developer>/<instance>` (per-developer subdirectory, mode 0400; bind-mounted RO into the instance).
**Testing**: pytest 9.x + pytest-mock (existing). New unit tests under `tests/unit/cli/`, `tests/unit/providers/`, `tests/unit/core/`. Ansible role tests via `ansible-playbook --check` against a fixture inventory. Cross-repo round-trip CI test (Remo synthesizes manifest → remo-broker validates → project socket served) is owned by remo-broker's CI; this repo runs the laptop side and asserts on socket file presence + denial behavior.
**Target Platform**: Linux client (laptop runs `remo`); remote = Debian/Ubuntu LXC containers (Incus/Proxmox), Amazon Linux 2023 / Ubuntu 24.04 (AWS), Ubuntu 24.04 (Hetzner). Broker binary distributed as static-linked `x86_64-unknown-linux-gnu` and `aarch64-unknown-linux-gnu`.
**Project Type**: Single project (CLI tool) + cross-repo binary dependency. Laptop source under `src/remo_cli/`, Ansible under `ansible/`, tests under `tests/`.
**Performance Goals**: NFR-001 (≤50 ms warm-cache fetch through broker socket vs. direct env read); SC-004 (≤30 s added to provisioning + first-devcontainer flow on typical broadband); broker boot-to-serving ≤2 s after systemd start (so `remo shell` from a fresh reconnect doesn't visibly wait).
**Constraints**:
- No provisioning credentials on any instance, in any cloud-init user-data, or in the laptop's shell env (FR-004, FR-005).
- No user secrets at rest on any instance (FR-022).
- Autonomous overnight agent sessions MUST keep working — no interactive backend identities (Clarifications Q2, FR-003a).
- Bootstrap token revocation MUST precede instance deletion (FR-020); SC-005 budgets ≤60 s for backend propagation.
- Cache TTL behavior: honor backend leases; default 15 min for backends without native TTL (NFR-004).
- Laptop-side `fnox` is a required runtime dep; `remo init` MUST detect a missing `fnox` and refuse to proceed with a clear install pointer (FR-006 implication).
- The `nodes.yml` registry MUST NOT store any admin SA token directly — only the fnox key under which each developer's SA lives. Compromise of the laptop's `nodes.yml` MUST not yield credentials.
**Scale/Scope**: 1 broker per instance; 5–20 active projects per developer instance; 1–10 instances per developer; 1–50 developers sharing a single Incus/Proxmox node (per-developer admin SA isolation per Clarifications Q5). ~24 functional requirements + 4 non-functional + ~10 new/modified CLI commands + 2 cross-repo contracts (manifest schema, wire protocol).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution is Ansible-focused. This feature touches both Ansible (bootstrap delivery, broker install role) and Python (CLI surface, manifest synthesis, fnox subprocess shims).

| Principle | Applicability | How addressed |
|---|---|---|
| I. Defensive Variable Access (Ansible) | High — new tasks register `fnox get …` pipe results, secret-push results, IAM-role attach results. Every one of these can be skipped per-provider and read by a later task. | All registered vars accessed via `\| default(…)`; pre-commit grep gate on `.rc ==` and `.stdout` already in CLAUDE.md will catch slips. Ansible role tests cover both "fresh instance" and "broker already installed" paths. |
| II. Test All Conditional Paths | High — every provider has present/absent broker, present/absent bootstrap, fresh vs. rotation flow; every `remo init` permutation crosses 4 backends × 2 scoping support × 2 interactivity = many states; manifest synthesis branches on language detection. | Unit tests for each branch; integration fixture inventories per provider; explicit table of state combinations in `quickstart.md`. |
| III. Idempotent by Default | High — `remo incus add-node` must be re-runnable (already-registered developer = no-op + confirmation); broker install role must converge whether broker is absent, present-and-older, present-and-current; `remo rotate-bootstrap` against an instance that just rotated should detect freshness and refuse-with-message rather than over-rotate. | Encoded in FR + new FRs added in Phase 1 contracts; covered by unit + role tests. |
| IV. Fail Fast with Clear Messages | High — missing `fnox`, interactive backend identity at init, missing bootstrap-admin SA, backend rate-limit/revocation failures, instance with no broker socket when devcontainer expected. | Each surfaces a specific exception class + remediation pointer; user-facing strings audited in tests. |
| V. Documentation Reflects Reality | Required at PR time — README, threat-model doc, devcontainer guidance. | Tasks phase will include README + new `docs/credential-broker.md` (threat model + operator runbook). |

**No violations. Gates pass.**

**Post-design re-evaluation (after Phase 1)**: No new violations introduced. The Phase 1 artifacts confirm the gate analysis:
- `contracts/ansible-changes.md` shows the Principle I `| default()` pattern applied to every new `register:` block.
- `quickstart.md` enumerates per-provider state combinations, satisfying Principle II's conditional-coverage expectation.
- `contracts/cli-surface.md` encodes Principle III idempotency for `add-node` and `rotate-bootstrap` (re-runs detect prior state and no-op or refuse-with-message).
- `research.md` R1, R2, R6 and the cli-surface exit-code table give Principle IV the actionable-error contracts it requires.
- The `docs/credential-broker.md` placeholder under Project Structure is the Principle V hook; Phase 2 tasks will fill it.

## Project Structure

### Documentation (this feature)

```text
specs/005-credential-broker/
├── plan.md              # This file
├── research.md          # Phase 0 — provider primitives, fnox integration shape, cross-repo contract mechanics
├── data-model.md        # Phase 1 — Node, BootstrapToken, ProjectManifest, ProjectSocket, Broker
├── quickstart.md        # Phase 1 — end-to-end per-provider walkthrough
├── contracts/           # Phase 1
│   ├── cli-surface.md          # All new/changed CLI commands
│   ├── manifest-schema.md      # Remo-side consumer view (cross-repo source = remo-broker/docs/manifest-schema.md)
│   ├── bootstrap-delivery.md   # Per-provider bootstrap-token delivery contract (IMDS / SSH push / node bind-mount)
│   ├── ansible-changes.md      # Diff against current group_vars/all.yml + new broker-install role
│   └── nodes-registry.md       # ~/.config/remo/nodes.yml format
└── tasks.md             # Phase 2 — NOT created by /speckit.plan
```

### Source Code (repository root)

```text
src/remo_cli/
├── cli/
│   ├── init.py                 # +backend selection, +interactive-identity rejection, +fnox detection
│   ├── destroy.py              # +pre-delete bootstrap-token revoke
│   ├── shell.py                # (no change expected; broker is instance-resident)
│   ├── audit.py                # NEW — `remo audit <instance>` retrieves /var/log/remo-broker/audit.log
│   ├── rotate.py               # NEW — `remo rotate-bootstrap [instance]`
│   └── providers/
│       ├── incus.py            # +`add-node` subcommand
│       ├── proxmox.py          # +`add-node` subcommand
│       ├── hetzner.py          # +SSH-push bootstrap token in `create` flow
│       └── aws.py              # +IAM instance-profile attach in `create` flow
├── providers/
│   ├── incus.py                # +add_node, +mint_bootstrap_token, +bind_mount_token
│   ├── proxmox.py              # +add_node, +mint_bootstrap_token, +bind_mount_token
│   ├── hetzner.py              # +mint_bootstrap_token, +ssh_push_token
│   ├── aws.py                  # +ensure_instance_role, +attach_role
│   └── broker.py               # NEW — shared install / revoke / rotate helpers
├── core/
│   ├── fnox.py                 # NEW — laptop-side fnox subprocess wrapper (get, list, presence-check)
│   ├── broker_install.py       # NEW — invokes ansible broker-install role with right vars per provider
│   ├── manifest.py             # NEW — discover/synthesize/validate broker.toml; JSON-Schema validation
│   ├── devcontainer.py         # NEW — devcontainer.json auto-synthesis (language detection + base image map)
│   ├── nodes.py                # NEW — ~/.config/remo/nodes.yml read/write
│   ├── audit.py                # NEW — fetch + render broker audit log
│   └── (existing core/* unchanged: config, output, validation, known_hosts, ssh, ansible_runner, picker, rsync, version, init)
└── models/
    ├── node.py                 # NEW — Node dataclass
    ├── manifest.py             # NEW — ProjectManifest dataclass + schema version field
    └── (host.py unchanged)

ansible/
├── group_vars/
│   └── all.yml                 # MODIFIED — env lookups → fnox pipe lookups
├── roles/
│   ├── broker_install/         # NEW — installs broker binary + systemd unit + tmpfiles.d for /run/remo-broker
│   │   ├── tasks/main.yml
│   │   ├── handlers/main.yml
│   │   ├── templates/remo-broker.service.j2
│   │   └── files/              # (broker binary fetched by URL at install time; not checked in)
│   ├── bootstrap_token_imds/   # NEW — AWS: validate IAM role attach
│   ├── bootstrap_token_file/   # NEW — Hetzner: install pushed file at correct perms
│   ├── bootstrap_token_mount/  # NEW — Incus/Proxmox: configure bind-mount entry
│   └── incus_bootstrap/        # EXISTING — broker-install added as a role dep
├── incus_configure.yml         # +broker_install
├── proxmox_configure.yml       # +broker_install
├── hetzner_configure.yml       # +broker_install
└── aws_configure.yml           # +broker_install

tests/
└── unit/
    ├── cli/
    │   ├── test_init_backend.py
    │   ├── test_destroy_revoke.py
    │   ├── test_audit.py
    │   ├── test_rotate.py
    │   └── providers/
    │       ├── test_incus_add_node.py
    │       ├── test_proxmox_add_node.py
    │       ├── test_hetzner_create_ssh_push.py
    │       └── test_aws_create_iam_attach.py
    ├── providers/
    │   ├── test_broker.py
    │   ├── test_incus_bootstrap.py
    │   ├── test_proxmox_bootstrap.py
    │   ├── test_hetzner_bootstrap.py
    │   └── test_aws_bootstrap.py
    ├── core/
    │   ├── test_fnox.py
    │   ├── test_broker_install.py
    │   ├── test_manifest.py
    │   ├── test_devcontainer.py
    │   ├── test_nodes.py
    │   └── test_audit.py
    └── models/
        ├── test_node.py
        └── test_manifest.py

docs/
└── credential-broker.md        # NEW — threat model + operator runbook (referenced from README)
```

**Structure Decision**: Additive within the existing three-layer split (`cli/` → `providers/` → `core/`). The broker daemon itself lives in the `get2knowio/remo-broker` repo (Rust); this repo references it as a versioned binary release and validates the manifest schema published per release. No directory restructure. New `add-node` commands fit the existing `@click.group()` pattern on `cli/providers/<name>.py`. The new `broker_install` Ansible role is invoked by each provider's existing `*_configure.yml` so the broker is installed via the same flow that already runs after instance creation.

## Complexity Tracking

No constitution violations. One structural choice worth flagging — broker daemon in a separate repo — is justified in spec.md §Component Sourcing (language asymmetry Python↔Rust, distribution shape, release cadence, audit surface). The cross-repo contract risk is mitigated by:

| Risk | Mitigation |
|---|---|
| Manifest schema drift between repos | JSON Schema generated from Rust types in remo-broker, published per release, validated on the Remo side via `jsonschema`. `schema_version` integer in every manifest; broker refuses unknown versions. |
| Broker binary supply chain | Signed releases; install role verifies signature before placing binary; pinned version per Remo release. |
| Wire protocol drift | Owned by remo-broker's `docs/wire-protocol.md`; Remo only synthesizes manifests + reads audit logs, both versioned. End-to-end CI round-trip test in remo-broker exercises both repos. |
