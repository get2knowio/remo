# Feature Specification: Credential Broker

**Feature Branch**: `005-credential-broker`
**Created**: 2026-05-23
**Status**: Draft
**Input**: User description: "Defend against malicious-dependency supply-chain attacks by removing long-lived developer credentials from Remo instances. Replace ambient environment variables and dotfile-resident secrets with an on-instance broker process that surfaces narrowly-scoped, allowlisted credentials into each devcontainer via per-project Unix sockets."

## Background and Motivation

Recent supply-chain attacks against npm (Shai-Hulud, Mini Shai-Hulud against `@antv`), PyPI, and other ecosystems share a pattern: a malicious dependency's install or postinstall script runs with the developer's full ambient environment and reads `GITHUB_TOKEN`, `NPM_TOKEN`, `AWS_*`, `~/.aws/credentials`, `~/.netrc`, `~/.npmrc`, SSH private keys, and similar. The Remo instance — being where most active dev credentials accumulate — is a high-value target.

Remo's existing isolation (one instance per developer, separate from the laptop) bounds blast radius, but the *contents* of the instance are exactly the secrets the attacker wants. Every `npm install`, `cargo build`, `pip install` of an untrusted package today runs with full credential access.

The credential broker design closes this gap by ensuring:

1. Long-lived developer credentials never live on the Remo instance at rest.
2. The instance fetches credentials on demand from an external backend (1Password, Vault, AWS Secrets Manager, etc.).
3. Each devcontainer sees only the credentials the project it hosts has explicitly declared a need for, enforced by kernel-level namespace separation, not just policy.

## Terms and Definitions

These terms are used consistently throughout this spec and should be adopted in all related code, CLI surface, and documentation.

| Term | Definition |
|---|---|
| **Backend** (L0) | The external secret store of record — 1Password, HashiCorp Vault, AWS Secrets Manager, Azure Key Vault, GCP Secret Manager, age-encrypted file in a private repo, or an OS keychain. Holds the user's actual credential values. Never directly accessed by devcontainers or untrusted code. |
| **Node** (L1) | A bare-metal or VM-based machine that hosts one or more Remo *instances*. Only applicable to self-hosted providers (Incus, Proxmox). For AWS and Hetzner, the cloud provider owns this layer and Remo does not address it. A node may host multiple instances belonging to one or many users. |
| **Instance** (L2) | The compute resource Remo creates per `remo <provider> create`. The SSH target tracked in `known_hosts.yml`. Called *instance* (AWS), *server* (Hetzner), or *container* (Incus, Proxmox) in per-provider CLI labels — but structurally a single concept. The *broker* runs here. |
| **Devcontainer** (L3) | A Docker container launched inside an *instance* by `devcontainer up`, one per *project*, where untrusted dependency code (npm install, etc.) actually executes. Consumes one *project socket*. |
| **Project** (L4) | A directory under `~/projects/` on an instance, usually a git repo, typically containing a `.devcontainer/` config and a *project manifest*. The unit selected in the project menu. |
| **Backend** identity | A credential the *broker* uses to authenticate upward to the backend (e.g., a 1Password Service Account token, Vault AppRole, AWS IAM instance profile). Scoped narrowly to a single *instance*'s allowed secrets. Not the same as the secrets it fetches. |
| **Bootstrap token** | The on-disk form of the backend identity stored on an instance (file at `/etc/remo-broker/bootstrap-token` on the instance). For self-hosted providers, originates on the *node* and is bind-mounted in. For Hetzner, SSH-pushed at create time. For AWS, replaced by an instance profile (no on-disk token). |
| **Broker** | The Remo-owned daemon (`remo-broker`) running on the instance as a systemd unit. Holds the bootstrap token, fetches credentials from the backend (using `fnox` internally as its multi-backend retrieval library), enforces per-project allowlists, and serves *project sockets* to devcontainers. One broker per instance. See "Component Sourcing" below for why this is built rather than adopted. |
| **Project socket** | A Unix domain socket at `/run/remo-broker/<project>.sock` on the instance, one per active project, bind-mounted as `/run/remo-broker/sock` into the project's devcontainer. The broker enforces a per-project allowlist on each. |
| **Project manifest** | `.remo/broker.toml` (auto-synthesized by Remo) or `.devcontainer/remo-broker.toml` (committed to the repo) declaring the set of backend-resolvable secret *names* this project is permitted to fetch. The broker reads this when creating the project socket and uses it as the per-project allowlist. Backend-side mappings (which credential store each name lives in, what backend identity to use) are configured separately at the instance level via the embedded fnox layer's own configuration. |
| **Provisioning credential** | A credential Remo uses to call cloud APIs (HETZNER_API_TOKEN, AWS_ACCESS_KEY_ID, Incus/Proxmox API tokens, 1Password SA admin token). Lives only in the laptop's fnox configuration, fetched on demand per `remo` invocation, never persisted to an instance or node. |
| **User secret** | A credential a project needs at runtime (GITHUB_TOKEN, NPM_TOKEN, runtime AWS keys, ANTHROPIC_API_KEY). Fetched on demand by the broker via the bootstrap token, held in broker memory only, exposed to devcontainers via project sockets. Never written to disk on the instance. |

### Layer diagram

```
L0  Backend
    └── 1Password / Vault / AWS Secrets Manager / age+git / keychain
                          ▲
                          │  broker authenticates with bootstrap token
                          │
L1  Node (Incus/Proxmox only; absent on AWS/Hetzner)
    ├── /var/lib/remo-broker/instance-tokens/<instance>
    └── bind-mounted read-only into the instance
        │
        ▼
L2  Instance
    ├── /etc/remo-broker/bootstrap-token    (file or IMDS-derived)
    ├── remo-broker daemon  (systemd unit; embeds fnox for backend retrieval)
    ├── /run/remo-broker/projA.sock         (allowlist: GITHUB_TOKEN, …)
    └── /run/remo-broker/projB.sock         (allowlist: NPM_TOKEN, …)
        │
        ▼  (bind-mounted as /run/remo-broker/sock)
L3  Devcontainer (one per project)
    └── tools fetch secrets via the mounted project socket only
        │
        ▼
L4  Project workspace (mounted from ~/projects/<name>)
```

For AWS/Hetzner, L1 collapses: there is no Remo-addressable node, and the bootstrap is delivered either via cloud workload identity (AWS instance profile) or SSH push (Hetzner).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Project creds available in devcontainer, never on the instance OS (Priority: P1)

A developer SSHes into a Remo instance, picks a project from the menu, and lands in a devcontainer. Inside the devcontainer, `gh auth status` shows them authenticated, `npm publish` finds an NPM_TOKEN, `aws sts get-caller-identity` works. None of those credentials existed on the instance OS before the devcontainer started, and none persist after it stops. A worm in `npm install` running inside that devcontainer can only see the credentials the project's manifest declared a need for — not credentials from other projects, not the bootstrap token, not the developer's full secret backend.

**Why this priority**: This is the entire point of the feature. Without it, no other behavior matters.

**Independent Test**: Provision an instance, configure a project manifest declaring `GITHUB_TOKEN`, launch the devcontainer, verify `gh auth status` reports authenticated *and* `cat ~/.config/gh/hosts.yml` shows no on-disk token. From outside the devcontainer (on the instance OS), verify `printenv` shows no GITHUB_TOKEN and `~/.config/gh/` does not exist for the project's user.

**Acceptance Scenarios**:

1. **Given** an instance with the broker installed and a project with a manifest declaring `GITHUB_TOKEN`, **When** the user selects that project from the menu, **Then** the launched devcontainer can use `gh` against authenticated GitHub APIs.
2. **Given** the same setup, **When** the user runs `printenv` or inspects `~/.aws/`, `~/.npmrc`, `~/.netrc` on the instance OS (outside any devcontainer), **Then** none of the project's credentials appear.
3. **Given** two projects A and B where A's manifest declares `GITHUB_TOKEN` and B's declares `NPM_TOKEN`, **When** project A's devcontainer requests `NPM_TOKEN` via the broker, **Then** the request is denied.
4. **Given** a devcontainer is running with a project socket mounted, **When** the devcontainer process exits, **Then** the project socket is removed from `/run/remo-broker/` and the broker drops the project's allowlist from memory.

---

### User Story 2 - Multi-device access to the same instance (Priority: P1)

A developer creates an instance from their laptop, does some work, then later runs `remo shell` from a different machine (a second laptop, a web session, a phone-tethered tablet). The instance is reachable and project credentials work in devcontainers from any device, without re-bootstrapping the instance or re-unlocking anything device-specific.

**Why this priority**: A broker design that only worked from the laptop that created the instance would fail Remo's core "remote dev environment" promise. The broker lives on the instance precisely to make this work.

**Independent Test**: Create an instance from device A, run a devcontainer with broker creds successfully. Without reconnecting from device A, run `remo shell` from device B against the same instance, launch the same devcontainer, verify credentials still work.

**Acceptance Scenarios**:

1. **Given** an instance was created from device A, **When** the user runs `remo shell` from device B, **Then** the broker is already running and serves credentials to launched devcontainers without intervention.
2. **Given** the instance has been rebooted, **When** the broker comes back up, **Then** it re-reads its bootstrap token, re-authenticates to the backend, and resumes serving without any device needing to connect.
3. **Given** an autonomous Claude Code session was started in a devcontainer before the user disconnected, **When** the developer is offline overnight, **Then** the session continues to access its allowlisted credentials.

---

### User Story 3 - Provisioning credentials never reach the instance (Priority: P1)

A developer runs `remo hetzner create myproject`. Remo reads the Hetzner API token from the laptop's fnox (not from `HETZNER_API_TOKEN` in the laptop's shell env), uses it to provision the VM, and then forgets it. After provisioning, the Hetzner API token has never been written to the instance, never appeared in cloud-init user-data visible in the Hetzner console, and is not present in the laptop's process environment.

**Why this priority**: Provisioning credentials are typically the most powerful (can create/destroy any resource in the account) and the easiest to leak. The instance has no need for them.

**Independent Test**: With `HETZNER_API_TOKEN` *unset* in the laptop shell but stored in the laptop's fnox config, run `remo hetzner create test`, then `ssh remo@test "env | grep -i hetzner"` returns nothing and the Hetzner console's user-data field for the VM contains no token.

**Acceptance Scenarios**:

1. **Given** Hetzner API token is stored in the laptop's fnox and *not* exported in the laptop's environment, **When** `remo hetzner create` runs, **Then** provisioning succeeds.
2. **Given** an instance has been created, **When** the user inspects cloud provider metadata/user-data via the provider's console or API, **Then** no provisioning credentials are visible.
3. **Given** the same setup, **When** the user inspects the instance OS, **Then** no provisioning credentials are present in environment, dotfiles, or any disk location.

---

### User Story 4 - Per-project credential allowlist via the manifest (Priority: P2)

A developer clones a new repo into `~/projects/foo`. Selecting it from the menu either reads the committed `.devcontainer/remo-broker.toml`, or — if absent — synthesizes a default `.remo/broker.toml` declaring only `github_token` (enough for `git push`). The developer can broaden the allowlist by editing the manifest; secrets not in the manifest cannot be fetched, even by tools that know their names.

**Why this priority**: The allowlist is the policy mechanism that makes the kernel-enforced isolation actually meaningful. Without it, every devcontainer would see every secret.

**Independent Test**: Place a project with a manifest declaring only `github_token`. Inside the devcontainer, attempt to fetch `npm_token` via the broker socket (`remo-broker get npm_token`) — expect refusal. Add `npm_token` to the manifest, restart the devcontainer, retry — expect success.

**Acceptance Scenarios**:

1. **Given** a project with no manifest, **When** the user selects it from the menu, **Then** Remo synthesizes `.remo/broker.toml` with a minimal default allowlist (gitignored) and proceeds.
2. **Given** a project with `.devcontainer/remo-broker.toml` declaring `[mcp] secrets = ["x", "y"]`, **When** the devcontainer requests secret `z`, **Then** the broker returns a denial and logs the attempt.
3. **Given** the manifest is updated, **When** the devcontainer is rebuilt or restarted, **Then** the new allowlist takes effect.

---

### User Story 5 - Bootstrap token rotation and instance destruction revoke access (Priority: P2)

When `remo destroy` runs against any instance, Remo revokes the bootstrap token at the backend *before* destroying the instance. A long-running rotation policy also periodically replaces each instance's bootstrap token. A token leaked from a destroyed or rotated-away instance has no remaining backend access.

**Why this priority**: Without revocation, the broker design just relocates the attack surface from "credentials on disk" to "bootstrap tokens that live until manually rotated." Lifecycle integration is what makes the threat model honest.

**Independent Test**: Create an instance, save a copy of its bootstrap token externally. Destroy the instance. Use the saved token to attempt to fetch a secret — expect failure within the backend's revocation propagation window (typically seconds).

**Acceptance Scenarios**:

1. **Given** an instance with an active bootstrap token, **When** `remo destroy` runs, **Then** the token is revoked at the backend before any instance-deletion API call.
2. **Given** a rotation policy is configured, **When** the rotation interval elapses, **Then** a fresh bootstrap token is provisioned to the instance and the previous one is revoked.
3. **Given** rotation fails for any reason, **When** the broker detects auth failures against the backend, **Then** it surfaces an actionable error and continues serving in-memory-cached credentials until they expire.

---

### User Story 6 - Devcontainer auto-synthesis for projects without one (Priority: P2)

The current project menu launches `devcontainer up` only when a project contains `.devcontainer/devcontainer.json`; otherwise it falls back to a plain shell on the instance OS, defeating the credential boundary. With this feature, projects without a committed devcontainer get an auto-synthesized `.remo/devcontainer.json` based on simple language detection, so *every* project the user selects from the menu lands them in a devcontainer with a broker socket.

**Why this priority**: The instance-OS fallback is the leak that defeats the rest of the design. Required for correctness.

**Independent Test**: Clone a repo with no `.devcontainer/` and a `package.json`. Select it from the menu. Expect to land in a Node-based devcontainer with a project socket mounted, not in a shell on the instance OS.

**Acceptance Scenarios**:

1. **Given** a project with no devcontainer config and a recognized language marker (`package.json`, `Cargo.toml`, `pyproject.toml`, etc.), **When** the user selects it, **Then** Remo writes `.remo/devcontainer.json` matching the language and launches it.
2. **Given** a project with no language marker, **When** the user selects it, **Then** Remo uses a generic base image and proceeds.
3. **Given** the user explicitly wants a shell on the instance OS, **When** they pick the "exit to host shell" menu option, **Then** they get one — with a one-time warning that no broker is available there.

---

### Edge Cases

- **Backend unavailable**: broker holds in-memory-cached secrets until they expire, then surfaces clear errors to devcontainers. Does not silently return stale values past expiry.
- **Backend unreachable from instance (network issue)**: broker reports the error via the project socket; tools using the broker see a clear "credential unavailable" error rather than a generic auth failure.
- **Two projects with the same name in different parent dirs**: project socket naming uses the absolute project path's hash (truncated) as suffix to avoid collisions.
- **A devcontainer escape into the instance OS**: attacker gains access to every project socket currently mounted in the instance plus the bootstrap token. Rotation interval and per-instance scoping bound the damage; full-backend access is not possible.
- **A node-level compromise on Incus/Proxmox**: attacker gains every instance's bootstrap token on that node. Threat model treats nodes as critical assets requiring OS hardening separate from instances.
- **A user runs untrusted code on the instance OS rather than in a devcontainer** (via "exit to host shell"): no broker available, so user secrets are simply not present. The escape hatch is safe by absence.
- **A project manifest declares a secret that doesn't exist in the backend**: broker returns a "not found" error to the devcontainer; tool's behavior depends on the tool, but the broker itself does not crash or fall back to other secrets.
- **The user has chosen `age + git` as backend** (no per-instance scoping primitive): `remo init` warns that this backend does not support narrowly-scoped bootstrap tokens; either reject the combination or fall back to laptop-unlock-per-session for Hetzner/Incus/Proxmox.
- **AWS SSM access mode**: bootstrap (instance profile) is metadata-based, no SSH push needed; broker installation still proceeds via the standard `*_configure.yml` flow which already works over SSM.

## Requirements *(mandatory)*

### Functional Requirements

**Backend integration**
- **FR-001**: System MUST support pluggable backends for the credential store, with first-class support for 1Password, HashiCorp Vault, AWS Secrets Manager, and age-encrypted git for the v1 cut.
- **FR-002**: System MUST allow per-installation backend choice via `remo init`, persisted to laptop-side fnox configuration.
- **FR-003**: System MUST warn the user at `init` time when their selected backend lacks per-instance scoping primitives (age + git) and offer a more secure alternative or a clearly-described downgrade.

**Provisioning credentials**
- **FR-004**: System MUST read provisioning credentials (HETZNER_API_TOKEN, AWS access keys, Incus/Proxmox API tokens) from the laptop's fnox rather than the laptop's shell environment.
- **FR-005**: System MUST NOT write provisioning credentials to any instance's disk, environment, or cloud-init user-data.
- **FR-006**: System MUST replace `lookup('env', '<TOKEN>')` patterns in `ansible/group_vars/all.yml` with `lookup('pipe', 'fnox get ...')` invocations against the laptop's fnox.

**Bootstrap & broker installation**
- **FR-007**: System MUST install the broker (`remo-broker` daemon + systemd unit; Remo-owned, embeds fnox for backend retrieval) on every Remo instance as part of the standard `*_configure.yml` Ansible flow.
- **FR-008**: For AWS, system MUST attach an instance-scoped IAM role at create time and configure the broker to use IMDS for credentials. No bootstrap token shall be written to disk.
- **FR-009**: For Hetzner, system MUST mint an instance-scoped bootstrap token on the laptop, SSH-push it to `/etc/remo-broker/bootstrap-token` (mode 0400, root) after first boot, and not include the token in any cloud-init user-data.
- **FR-010**: For Incus and Proxmox, system MUST mint the bootstrap token on the laptop, place it under `/var/lib/remo-broker/instance-tokens/<instance>` on the *node* (not in any instance), and bind-mount it read-only into the instance at `/etc/remo-broker/bootstrap-token`. The node itself MUST NOT inspect or store project information.
- **FR-011**: System MUST register the node (one-time per Incus/Proxmox node) via a new `remo incus add-node` and `remo proxmox add-node` command, installing the token-manager helper.

**Project sockets and manifests**
- **FR-012**: System MUST discover project manifests in priority order: `.devcontainer/remo-broker.toml` (committed) → `.remo/broker.toml` (auto-synthesized, gitignored).
- **FR-013**: System MUST auto-synthesize a minimal default manifest declaring `github_token` for projects with no existing manifest.
- **FR-014**: System MUST create one project socket per active project at `/run/remo-broker/<project>.sock` on the instance, enforcing the project's manifest allowlist.
- **FR-015**: System MUST mount the appropriate project socket into the project's devcontainer at `/run/remo-broker/sock` via devcontainer bind-mount configuration.
- **FR-016**: System MUST remove a project socket when its associated devcontainer exits.

**Devcontainer enforcement**
- **FR-017**: The project menu MUST launch every selected project inside a devcontainer (committed or auto-synthesized), with no fallback to running the project on the instance OS.
- **FR-018**: The project menu MUST provide an explicit "exit to instance shell" option that surfaces a one-time warning explaining the broker is not available outside a devcontainer.
- **FR-019**: The instance OS shell MUST NOT have a broker socket available by default.

**Lifecycle**
- **FR-020**: `remo destroy` MUST revoke an instance's bootstrap token at the backend *before* destroying the instance.
- **FR-021**: System MUST support periodic rotation of bootstrap tokens via a `remo rotate-bootstrap [instance]` command, configurable to run automatically on a cadence.
- **FR-022**: The broker MUST hold user-secret values in memory only, never writing them to disk on the instance.

**Auditability**
- **FR-023**: The broker MUST log every secret-access request (project, secret name, allowed/denied, timestamp) to a local log file readable only by root on the instance.
- **FR-024**: System MUST provide a `remo audit <instance>` command that retrieves and displays the broker's access log.

### Non-Functional Requirements

- **NFR-001**: A broker-mediated secret fetch in the steady state (warm cache) MUST add no more than 50 ms latency over a direct env-var read.
- **NFR-002**: The broker MUST survive `systemd` restarts and instance reboots without manual reconfiguration.
- **NFR-003**: An instance's broker MUST function for all configured backends across a backend network outage by serving the last in-memory-cached value until that value's TTL expires.

### Key Entities

- **Node** (new model): represents an Incus or Proxmox node registered with Remo. Fields include `name`, `host` (SSH target), `provider`, `bootstrap_admin_identity` (an SA admin token capable of minting per-instance sub-tokens, stored in laptop's fnox). Not used for AWS or Hetzner.
- **Bootstrap token** (no Remo model — opaque string): the per-instance backend identity. Located at `/etc/remo-broker/bootstrap-token` on instance, `/var/lib/remo-broker/instance-tokens/<name>` on node (self-hosted only). On instances with a TPM, the token SHOULD be sealed at rest via systemd's `LoadCredentialEncrypted` / TPM2 binding so that an offline disk read does not yield a usable token.
- **Project manifest** (TOML file in repo or `.remo/`): declares the set of backend secret names this project requires. Read by the broker when minting a project socket.
- **Project socket** (Unix domain socket): per-project, per-instance, ephemeral, enforces the manifest's allowlist.
- **Broker** (process): one per instance, runs as a systemd unit (`remo-broker.service`), Remo-owned code that holds the bootstrap token, embeds fnox as its multi-backend retrieval library, holds a memory-only TTL cache of recently-resolved user secrets, enforces per-project allowlists, and writes an append-only audit log.
- **KnownHost** (existing model, unchanged): continues to represent L2 instances and their SSH-target metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After feature is fully adopted, **zero long-lived user secrets** are written to any Remo instance's disk. Verified by auditing `~/.aws/`, `~/.config/gh/`, `~/.npmrc`, `~/.netrc`, and `env` across a representative sample of instances.
- **SC-002**: A simulated supply-chain attack — a malicious `postinstall` script running `printenv`, `cat ~/.aws/credentials`, `cat ~/.config/gh/hosts.yml`, and an outbound HTTP exfil call from inside a devcontainer — recovers **only** the secrets declared in that project's manifest, and no others.
- **SC-003**: `remo shell` works from any device the user has authenticated to the backend from, with **no per-device instance reconfiguration** required.
- **SC-004**: Provisioning a new instance and launching a devcontainer adds **no more than 30 seconds** to today's flow on a typical broadband connection (warm laptop fnox cache, warm backend).
- **SC-005**: After `remo destroy`, a copy of the destroyed instance's bootstrap token is **rejected by the backend within 60 seconds**.
- **SC-006**: When the user adds a secret to a project manifest, it becomes available inside that project's devcontainer **on the next devcontainer restart**, with no instance-level configuration changes.

## Component Sourcing

This section records why the broker is built rather than adopted, and which external components are leveraged.

### Adopted: fnox (laptop CLI + on-instance retrieval library)

[`fnox`](https://github.com/jdx/fnox) is a mature (v1.25.1, ~39 releases, post-1.0) multi-backend secret-fetching CLI that already abstracts 1Password, HashiCorp Vault, AWS Secrets Manager, age, and the OS keychain behind a single `fnox get <name>` interface. Remo adopts fnox in two roles:

1. **Laptop-side provisioning credential lookup** — `lookup('pipe', 'fnox get …')` invocations in Ansible replace `lookup('env', …)` patterns (FR-006). The laptop already typically has fnox configured for the developer's day-to-day secret access.
2. **On-instance backend retrieval inside the broker** — the `remo-broker` daemon embeds fnox (as a subprocess or library, design-deferred) to resolve a name like `GITHUB_TOKEN` to a fetched value via whichever backend is configured for that instance.

This sidesteps the largest single body of work in the spec — writing and maintaining adapters for five different secret stores, each with its own auth model and SDK.

### Built: the `remo-broker` daemon

A broader survey of credential-broker-adjacent tooling (Vault Agent, OpenBao Agent, SPIFFE/SPIRE, Teleport Machine ID, Infisical Agent, systemd credentials, 1Password Connect, EKS Pod Identity Agent, Doppler, sops, Bitwarden Secrets Manager, aws-vault) found **no single existing tool that combines** the four properties this spec requires:

1. Multi-backend retrieval (1Password + Vault + AWS SM + age + keychain in one process).
2. Per-project Unix-socket serving (one socket per project, distinct allowlists).
3. Manifest-driven allowlists that the broker itself enforces.
4. Bootstrap modes spanning IMDS (AWS), token-file (Hetzner), and node-bind-mount (Incus/Proxmox).

The closest single-tool fit is **Vault Agent / OpenBao Agent**, but it is single-backend (Vault-only) and its per-listener `role` / `require_request_header` knobs are anti-SSRF controls rather than per-project secret allowlists — true allowlisting would require one agent process per project, which defeats the operational model.

The strongest patterns to *borrow* (not adopt wholesale) come from:

- **EKS Pod Identity Agent** — proves the "token-file mounted into client → daemon returns only that client's allowed credentials" pattern in production.
- **SPIFFE/SPIRE Workload API** — proves that kernel-attested per-caller scoping over a Unix socket is practical.

The broker therefore is Remo-owned code, with these influences guiding its IPC and attestation design. It is small (estimated low single thousands of lines), policy-driven, and depends on fnox-core for the genuinely complex backend-integration work.

#### Repository layout and implementation language

The `remo-broker` daemon lives in a separate repository (`get2knowio/remo-broker`) for reasons of language asymmetry (Rust vs. Remo's Python), distribution shape (signed binary releases vs. PyPI), release cadence (the daemon is install-once, the laptop CLI iterates), and audit surface. Implementation language: **Rust**, with [`fnox-core`](https://crates.io/crates/fnox-core) (MIT, published by the fnox author) as a Cargo dependency — closes OQ-7 in the "library" direction by avoiding subprocess fork/exec per uncached fetch and keeping secret values inside fnox-core's typed wrappers end-to-end.

The new repo owns the broker's internal design and the two cross-repo contracts:

- `specs/001-broker-daemon/spec.md` — full feature spec for the daemon
- `docs/manifest-schema.md` — versioned TOML schema for `remo-broker.toml` (cross-repo contract; source of truth here, JSON Schema published per release and consumed by Remo for laptop-side validation)
- `docs/wire-protocol.md` — project-socket and admin-socket wire protocol (cross-repo contract)

Schema-drift mitigations between the two repos: (1) JSON Schema generated from Rust types and validated on the Remo side, (2) `schema_version` integer in every manifest with broker-side refusal of unknown versions, (3) end-to-end CI test exercising both repos against a real manifest + socket round-trip.

### Future escape hatches

- If fnox becomes unmaintained or unsuitable, the natural swap is **OpenBao Agent** (MPL-2.0) running one agent per project behind a thin Remo supervisor. Trade-off: loss of native 1Password / OS-keychain support unless those are proxied via OpenBao secret engines.
- If a generic broker daemon emerges upstream that meets all four requirements above, Remo's broker can be retired in favor of it.

### Complementary, used underneath the broker

- **systemd `LoadCredentialEncrypted` + TPM2** — used to seal the bootstrap-token file at rest on TPM-equipped instances (typically Incus / Proxmox nodes), so an offline disk read does not yield a usable token. Does not replace any broker function — it hardens token storage.

## Out of Scope

- **Sandboxing untrusted code beyond the devcontainer boundary**: this spec does not require additional in-devcontainer sandboxing (gVisor, firejail, network egress restrictions per-install-script). Those are complementary defenses worth considering separately.
- **Replacing existing SSH key management**: the broker handles user secrets fetched at runtime by devcontainer tooling. SSH key material for `remo shell` itself remains handled by the existing flow (laptop ssh-agent forwarding or instance-resident keys, depending on access mode).
- **Backend selection UI improvements** beyond the minimum needed at `remo init`. A full backend management TUI is future work.
- **Secret rotation at the user-secret level**: this spec rotates *bootstrap tokens* (the broker's identity). Rotation of the actual GitHub PAT, NPM token, etc. is the backend's concern and the user's policy choice.
- **Multi-user instances**: this spec assumes each instance has one developer user. Multi-tenant instances would need per-user project-socket isolation, deferred to future work.

## Open Questions

- **OQ-1**: Should the project socket be created per-devcontainer-lifetime or per-project-lifetime? The former gives strict ephemerality but may complicate background tasks like `cargo build` running across `devcontainer exec` invocations. The latter is operationally simpler.
- **OQ-2**: For Incus/Proxmox nodes that host instances from multiple developers, how is the node's admin identity (used to mint sub-tokens for each developer's instances) bootstrapped? Per-developer admin SAs, or a single node admin SA with logical sub-scoping?
- **OQ-3**: Should the broker also serve the `gh` git credential helper protocol natively, or is `gh auth login --with-token` against a broker-fetched token sufficient?
- **OQ-4**: Default rotation cadence for bootstrap tokens — 24h, 7d, or "never (revoke only on destroy)"? Trade-off is operational noise vs. exposure window.
- **OQ-5**: How should the broker behave when a backend requires interactive auth (e.g., 1Password biometric prompt) and the requesting context is non-interactive (autonomous AI agent at 3am)? Refuse and surface the requirement, or rely entirely on non-interactive backend identities (SA tokens) and forbid interactive ones for instance use?
- **OQ-6**: Should the broker make the bootstrap-token file's TPM2 sealing mandatory on instances where a TPM is available, or remain opt-in via configuration? Mandatory closes the offline-disk-read attack reliably; opt-in avoids surprises for users on older hardware or with custom systemd setups.
- **OQ-7**: Should the broker embed fnox as a Rust library (tight coupling, one process, faster) or shell out to the `fnox` binary as a subprocess (loose coupling, version-independent, slower)? Subprocess is simpler to ship and lets users upgrade fnox independently; library is faster and avoids a fork+exec per uncached fetch but locks the broker to a specific fnox version.
- **OQ-8**: What's the wire protocol on the project socket — a simple line-based `GET <name>\n` / `<value>\n` shape, a fnox-CLI-compatible protocol (so tools that already speak fnox work unchanged), or something richer (gRPC, JSON-RPC) that supports streaming, watches, and structured errors? Simpler is easier to audit; richer enables future features like credential-change notifications.
