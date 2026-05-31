# Feature Specification: Credential Broker (Sidecar Devcontainer Model)

**Feature Branch**: `006-credential-broker-laptop-push` *(branch name retained for PR continuity; the model has since been simplified — see [§Why the design pivoted twice](#why-the-design-pivoted-twice))*
**Created**: 2026-05-30 (laptop-push model)
**Pivoted**: 2026-05-31 (sidecar devcontainer model)
**Status**: Draft
**Supersedes**: [`005-credential-broker`](../005-credential-broker/) (external-backend / bootstrap-token model — see [closed PR #32](https://github.com/get2knowio/remo/pull/32))
**Cross-repo dependency**: [`remo-broker` spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets) (the on-instance daemon)

**Input**: Defend a remo dev instance and the project devcontainers running on it against AI agents and supply-chain attacks by ensuring no plaintext credentials exist at rest anywhere an agent or malicious dependency can read them. Achieve this by introducing a dedicated *credential vault devcontainer* (the "sidecar") on every remo instance, separate from the user's project devcontainers, that owns the OAuth flows, holds the source-of-truth for the user's credentials, and pushes them locally to the broker daemon for in-memory vending to project devcontainers via per-project Unix sockets with manifest-gated allowlists.

## Clarifications

### Session 2026-05-31

- Q: When a project devcontainer starts and a manifest-declared secret is unavailable, what should startup do? → A: Retry briefly, then fail startup.
- Q: For manifest entries rendered to files, should a secret name map to a structured credential bundle or only a single scalar value? → A: A secret name may map to a structured credential bundle.
- Q: When the sidecar pushes credentials to the broker, should the broker atomically replace its full in-memory store or merge updates into existing state? → A: Atomically replace the full in-memory store.
- Q: After a successful `push-creds` update, what should happen to existing per-project broker cache entries? → A: Invalidate them immediately.
- Q: What should the bounded startup retry window be when required secrets are unavailable? → A: 15 seconds.

## Why the design pivoted twice

005 implemented an external-backend (Vault / AWS-SM / 1Password / age-git) model with a bootstrap-token-on-instance. End-to-end testing on 2026-05-29 surfaced that the bootstrap token at `/etc/remo-broker/bootstrap-token` is itself an on-disk credential — exactly the kind of artifact the [origin-story principle](https://x.com/nateberkopec/status/2048634637447201264) was scrubbing. 005 was closed in [#32](https://github.com/get2knowio/remo/pull/32).

The first 006 redesign (2026-05-30) replaced the external backend with a "laptop pushes age-encrypted blob to the instance over SSH" model. Cleaner — no bootstrap token, no external service — but still keyed on the laptop being the source-of-truth. During design review on 2026-05-31, that assumption was challenged: remo's actual deployment is *single-instance*, not fleet. There's no "spread credentials across 20 instances" benefit; the laptop-as-source adds complexity (encrypt-on-laptop, decrypt-on-instance, age dependency, pubkey pinning) for no real-world gain. Moving the source-of-truth onto the instance itself, in a dedicated and isolated container, removes the entire SSH-transit / encryption-at-rest / pubkey-trust apparatus while improving the multi-device-access story.

The result — the design captured in this revision — is materially simpler than either 005 or the first 006: no `age` dependency, no on-disk encrypted blob, no pubkey to pin, no new laptop CLI commands.

## Threat model

The threat is **any code running inside a *project* devcontainer with the developer's UID**:

- AI coding agents (Claude Code, Cursor, etc.) following injected or hallucinated instructions
- Malicious or compromised npm / pip / cargo / etc. dependencies (Shai-Hulud, the Axios-vector incidents)
- Misbehaving CLI tools that scan the filesystem for "useful" credentials

Out of scope:

- A privileged attacker who has already obtained root on the LXC host (Proxmox node, AWS hypervisor)
- Compromise of the developer's laptop itself
- Compromise of the *sidecar* devcontainer (different container boundary; if the sidecar is compromised, the user has bigger problems and remo's threat model assumes the sidecar is trusted)
- OAuth tokens cached inside the project devcontainer by tools the user ran *there* (addressable only via execution-layer policy — see [§Future work](#future-work) on agentsh)

## Architecture

```
LXC instance (one per developer; e.g. lab1/dev1):
  ├─ remo-broker daemon                         [systemd service on the LXC host]
  │   ├─ in-memory Arc<HashMap<...>>            (no on-disk secrets blob)
  │   ├─ per-project Unix sockets               (NDJSON, manifest-gated)
  │   ├─ admin socket /run/remo-broker/admin.sock
  │   └─ audit log /var/log/remo-broker/audit.log
  │
  ├─ _remo-vault devcontainer                   [the sidecar — Docker]
  │   ├─ user runs gh / aws / claude login flows here
  │   ├─ fnox-local storage (encrypted at rest with TPM-sealed key)
  │   ├─ inotify watcher → calls broker admin "push-creds" on changes
  │   └─ helper scripts: remo-list-creds, remo-test-project, remo-reload
  │
  ├─ project-a devcontainer                     [user project — Docker]
  │   ├─ entrypoint helper fetches manifest-declared secrets from broker
  │   ├─ secrets injected as env vars / materialized in tmpfs per manifest
  │   ├─ .remo/manifest.toml mounted read-only
  │   └─ user's work: editor, npm install, claude code, etc.
  │
  └─ project-b devcontainer                     [user project — Docker]
      └─ same shape; isolated from project-a and from the sidecar
```

Key properties:

- **Source-of-truth lives in the sidecar**, encrypted at rest in its container filesystem (TPM-sealed key via systemd-creds at LXC level, passed in via Docker secret/bind-mount)
- **Broker is purely in-memory** — populated by the sidecar at instance startup and on credential changes; no on-disk persistence in the broker itself
- **Push from sidecar to broker is plaintext over a local Unix socket** — no network, no MITM threat, no encryption needed in transit
- **Project devcontainers cannot reach the sidecar's filesystem** — different Docker containers, isolated namespaces
- **Project devcontainers cannot modify their own manifest** — bind-mounted read-only; the gate that says "this project can read these secrets" is not mutable from inside the gate
- **Laptop is optional after initial provisioning** — all credential management happens by SSH-ing in and running familiar upstream CLI tools in the sidecar

## The sidecar devcontainer

A remo-managed devcontainer that exists on every remo instance, dedicated to credential management.

**Provisioning**: Created during `remo {provider} create` by an Ansible role (`vault_devcontainer_install`). Auto-started by the existing devcontainer-cli infrastructure when the LXC boots.

**Naming**: Appears in the project picker with a reserved underscore-prefixed name (`_remo-vault`) so it sorts before user projects and is visually distinct.

**Contents**:

- Base image: debian-slim (or similar small image)
- Pre-installed CLIs: `gh`, `aws`, `claude`, `fnox`, plus standard shell tooling
- `fnox` configured to use a local encrypted-file backend at `/var/lib/remo-vault/fnox.enc`
- Decryption key for the fnox store loaded at sidecar startup via Docker secret, sourced from systemd-credentials on the LXC host (TPM-sealed if available — see [Cross-cutting decisions §2](#cross-cutting-decisions))
- Helper scripts in `/usr/local/bin/`:
  - `remo-list-creds` — what's stored, when set, last pushed
  - `remo-test-project <name>` — fetch the project's manifest-declared secrets and report success/failure per secret
  - `remo-vend-status` — what's loaded in broker memory right now
  - `remo-reload <project>` — trigger broker to re-read a project's manifest after an edit
- A small inotify-based daemon (`remo-vault-watcher`) that detects fnox storage changes and triggers `push-creds` to the broker
- Custom MOTD on shell entry explaining where the user is and what to do
- The broker's admin socket bind-mounted from the LXC host (e.g., `/run/remo-broker/admin.sock` → same path in container, with appropriate UID/GID for access)

**Persistence**:

- The fnox storage (`/var/lib/remo-vault/fnox.enc`) lives on a Docker volume that survives sidecar container restart
- Survives LXC reboot
- Destroyed only by `remo destroy` (which tears down the entire LXC)

**OAuth flow UX**:

- For CLIs supporting **device-code flow** (gh, aws sso, most modern OAuth providers): trivial — `gh auth login --web` shows a code, user pastes it into a browser on any device, done. No port forwarding, no callback URL gymnastics.
- For CLIs requiring **browser callback** (Claude CLI today, some others): user runs the login command with SSH local-port-forwarding (`ssh -L 8080:localhost:8080 <instance>` before entering the sidecar). Less seamless but functional.
- For services with **no interactive flow** (just API keys / PATs): user generates the token on the provider's web UI, pastes into the sidecar via `fnox set <name>`.

## The project devcontainer experience

A project devcontainer is provisioned by the user's normal devcontainer-cli flow (`devcontainer up`), modified by a remo-managed devcontainer feature (`remo/secrets-feature`) that:

1. Reads the project's manifest (mounted read-only into the container)
2. Connects to the broker's per-project socket
3. Fetches each manifest-declared secret
4. Injects it according to the manifest's `fetch_as` directive
5. Hands control off to the user's normal entrypoint / shell

Two injection modes, per-secret:

### Mode 1: Environment variable (default)

```toml
[secrets.gh]
fetch_as = "env"
env_var  = "GH_TOKEN"      # default: secret_name.upper()
```

The secret is exported into the devcontainer's environment before the user's shell starts. Tools that accept env-var auth (`gh`, `npm`, `pip`, `aws`, `openai`, `anthropic`, most modern CLIs) just work without any per-tool shimming.

### Mode 2: Tmpfs file (opt-in)

```toml
[secrets.aws]
fetch_as  = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template  = """
[default]
aws_access_key_id={{aws_access_key_id}}
aws_secret_access_key={{aws_secret_access_key}}
"""
```

For CLIs that really only read from a file at a known path. The file is materialized in a **memory-backed tmpfs mount** — never touches persistent disk inside the container, vanishes on container restart.

For file rendering, a manifest secret name may refer to a **structured credential bundle** rather than only a scalar string. In that case, `template` placeholders resolve against bundle field names (for example `{{aws_access_key_id}}` and `{{aws_secret_access_key}}`).

### Realistic limits

Once a credential is reachable by a tool inside the devcontainer (env var or tmpfs file), **anything else running in that devcontainer as the same UID can read it.** The broker can't change that; the OS can't change that without execution-layer policy (`agentsh`, deferred).

What the design *does* provide:

- **Per-project isolation**: project A's compromise can't read project B's creds (separate containers)
- **Manifest gating**: project A's compromise can't fetch a secret that isn't in its manifest (broker enforces)
- **No persistence in the project container**: env vars and tmpfs files die on container restart; nothing on the LXC's persistent disk through this path
- **Sidecar protection**: the source-of-truth in the sidecar is unreachable from any project devcontainer regardless of compromise

## The manifest

### Location and protection

The manifest lives at `~/projects/<project-name>/.remo/manifest.toml` on the LXC host filesystem (version-controlled with the project repo). The project devcontainer mounts the project repo read-write (so the user can edit code), and **separately bind-mounts the manifest file read-only** on top:

- Code files: writable from the devcontainer (normal dev workflow)
- `.remo/manifest.toml`: read-only from the devcontainer; a malicious dep cannot rewrite it to add new entries

The broker reads the manifest from the LXC host's view of the path (not from inside any container), via the existing per-project manifest discovery in 005.

### Updating the manifest

Manifest changes are deliberate operations that **cannot happen from inside the project devcontainer**. The user updates a manifest from the sidecar (or from a host-side shell) and then runs `remo-reload <project>` to trigger the broker's reload op.

This is friction by design: changing what a project can read is a security-sensitive action that should not be possible by an editor save or a malicious dep inside the project's blast radius.

### Schema (extends 005)

```toml
schema_version = 1
project        = "project-a"   # must match the parent dir basename

[secrets.gh]
fetch_as = "env"
env_var  = "GH_TOKEN"

[secrets.openai_api_key]
fetch_as = "env"               # env_var defaults to OPENAI_API_KEY

[secrets.aws]
fetch_as  = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template  = "..."

[cache]
default_ttl_seconds = 900      # carries over from 005
default_max_entries = 50
```

## Requirements

### Functional

| ID | Requirement |
|---|---|
| FR-001 | At `remo {provider} create`, Ansible provisions both the broker daemon (systemd service on the LXC host) and the `_remo-vault` sidecar devcontainer. |
| FR-002 | The sidecar devcontainer starts automatically on LXC boot and exposes the broker's admin socket as `/run/remo-broker/admin.sock` (bind-mount from LXC host, mode 0660 with sidecar UID in the broker's allowed group). |
| FR-003 | The sidecar runs an inotify watcher that detects fnox storage changes and calls the broker's `push-creds` admin op with the fresh plaintext secret map. |
| FR-004 | The sidecar's fnox storage is encrypted at rest using a key sourced from systemd-credentials at LXC level (TPM2-sealed → host-key → mode-0600 plaintext fallback ladder), bind-mounted into the sidecar container as a Docker secret. |
| FR-005 | The broker daemon holds secrets only in process memory. There is no on-disk secrets blob. On broker restart, the in-memory store is empty until the sidecar re-pushes (which it does automatically as part of its own startup). |
| FR-005a | Each successful `push-creds` call atomically replaces the broker's entire in-memory credential store with the sidecar's current source-of-truth. Secrets omitted from a newer push are removed from broker memory as part of the same atomic swap. |
| FR-006 | Push from sidecar to broker is plaintext over the LXC-local admin socket (Unix domain socket, kernel-mediated). No encryption-in-transit; no pubkey trust. |
| FR-007 | The project devcontainer's entrypoint runs `remo-fetch-secrets` (shipped via the `remo/secrets-feature` devcontainer feature), which reads the project's manifest, fetches each declared secret from the broker's per-project socket, and injects per the manifest's `fetch_as` directive (env var or tmpfs file). If any manifest-declared secret is unavailable, `remo-fetch-secrets` retries for up to 15 seconds and then exits non-zero without starting the user's normal entrypoint. |
| FR-008 | The manifest at `.remo/manifest.toml` is bind-mounted read-only into the project devcontainer; the parent `.remo/` directory may or may not be writable depending on how the user organizes other project-level config. |
| FR-009 | The manifest schema supports per-secret `fetch_as = "env"` (default) and `fetch_as = "file"` (with `file_path`, `file_mode`, `template` fields). File-rendered entries may reference structured credential bundles, and template placeholders resolve against bundle field names. |
| FR-010 | `remo shell` shows the sidecar as `_remo-vault` in the project picker. `remo shell -p _remo-vault` jumps straight into the sidecar's shell. |
| FR-011 | The sidecar ships helper scripts: `remo-list-creds`, `remo-test-project <name>`, `remo-vend-status`, `remo-reload <project>`. |
| FR-012 | `remo destroy` tears down the entire LXC including the sidecar; no separate teardown step is needed. |
| FR-013 | The per-project socket protocol (`get` / `ping` / `info`), per-project manifest enforcement, per-project bounded cache, and audit log format from 005 carry forward unchanged, except that a successful `push-creds` invalidates all existing per-project cache entries immediately so subsequent reads observe the new snapshot. |
| FR-014 | A new audit event `AuditEvent::SecretsPushed { timestamp, secret_count }` is emitted on successful `push-creds`. Values are not logged; only counts. |
| FR-015 | The wire protocol bumps to v2 in remo-broker (removing `rotate-bootstrap` and `bootstrap_mode` are breaking per the project's own additive-only-within-major rule). |

### Non-functional

| ID | Requirement |
|---|---|
| NFR-001 | A project-devcontainer-side `find / -type f \( -name '*.env' -o -name 'credentials*' -o -name '.netrc' -o -path '*/.aws/*' -o -path '*/.config/gh/*' \) 2>/dev/null` returns no useful results on a freshly provisioned instance, before the user has run any tool that creates such files. (Tools the user runs in-container may still create such files; the protection is against credentials *at provisioning rest*, not against the user's own actions.) |
| NFR-002 | After `remo destroy`, an EBS-snapshot / disk-image exfiltration of the LXC yields no recoverable plaintext secrets, *provided* the sidecar's fnox-storage decryption key was TPM-sealed (FR-004 tier 1) or host-key-bound (tier 2). The plaintext-mode-0600 tier is best-effort and is documented as such. |
| NFR-003 | The published `remo-broker` binary (per cross-repo spec 002) is ≤ 15 MiB stripped (the original NFR target on remo-broker spec 001, missed at v0.1.0 due to `fnox-core` transitive deps). |
| NFR-004 | The redesign requires no external service dependency. `fnox` inside the sidecar is the only secret-storage component. |
| NFR-005 | The push from sidecar to broker (full plaintext map of ~10 secrets) completes in < 50 ms on stock Debian LXC. |
| NFR-006 | The laptop CLI requires no new commands compared to today's `remo` (which already has `init`, `{provider} {create,destroy,list,add-node}`, `shell`, `cp`, `audit`). |

### Removed (from 005 *and* the laptop-push 006 draft)

Compared to **005**:

- `remo init --backend {1password|vault|aws-sm|age-git}` → `remo init` has no backend flag
- `remo rotate-bootstrap` → does not exist (no bootstrap token concept)
- `--admin-sa-fnox-key`, `--accept-downgrade` flags
- Four `bootstrap_token_{file,mount,imds}` Ansible roles
- All four backend implementations in `providers/broker.py` (1P, Vault, AWS-SM, age-git mint/revoke)
- `core/broker_revoke.py` and the entire revoke/`TokenLookupError` machinery
- Per-instance rotation cadence persistence across provider-native metadata

Compared to **the first 006 draft (2026-05-30 laptop-push)**:

- `remo push-creds` CLI command (push happens locally from sidecar, not from laptop)
- `remo {provider} repin` CLI command (no pubkey to pin — there's no encryption-in-transit)
- `core/encrypted_blob.py` (no encryption needed for sidecar→broker push)
- `core/instance_keys.py` (no pubkey trust model needed)
- `core/broker_admin.py` NDJSON-over-SSH transport (push is now local, not over SSH)
- The `age` / `pyrage` dependency
- `/var/lib/remo-broker/secrets.enc` on-disk encrypted blob (broker is purely in-memory; sidecar holds the encrypted at-rest source)
- The TOFU pubkey trust model decision (moot — no pubkey)

## Cross-cutting decisions

1. **No `age` encryption anywhere in the push path.** Sidecar→broker is plaintext over local Unix socket. Encryption-at-rest applies only to the sidecar's own storage (separate from any push-in-transit consideration).
2. **Decryption-key sourcing for the sidecar's storage**: fallback ladder via systemd-credentials at LXC level. TPM2 (`systemd-creds setup --with-key=auto+tpm2`) where the LXC host exposes `/dev/tpm0`, host-key (`--with-key=auto`) otherwise, plaintext-mode-0600 as a last-resort opt-in. The chosen tier is surfaced in `remo-vend-status` so operators can audit posture.
3. **Wire protocol v2** in remo-broker. New artifact `schema/remo-broker.v2.json` ships as a release artifact.
4. **No `agentsh` in this spec.** The execution-layer policy gateway (agentsh.org) is a complementary defense that addresses the case where the user runs an OAuth-flow CLI directly inside a project devcontainer. Deferred to a future spec; the broker redesign does not depend on it.
5. **Manifest is bind-mounted read-only** into project devcontainers; updates happen from the sidecar followed by `remo-reload`. This is friction by design.
6. **The laptop CLI is unchanged.** No new commands. All credential management happens via SSH into the instance.
7. **Sidecar appears in the project picker** with a reserved name (`_remo-vault`). No new shell UI primitives needed; the picker gains one entry.

## Future work

- **agentsh integration** (separate spec): wrap the *project* devcontainer's agent processes under `agentsh wrap` with a default policy that denies reads of cred-shaped paths and whitelists env vars per command. Addresses the OAuth-cached-credential case where the user runs `gh auth login` directly inside a project devcontainer instead of the sidecar.
- **Sidecar-managed interactive TUI** (separate spec): replace the helper scripts with a more polished management UI (`remo-vault-manage` or similar) once Philosophy A is validated in real use.
- **Per-secret TTL hints**: the fnox storage envelope could carry per-secret expiry metadata so the sidecar rotates individual secrets out of memory ahead of a full re-push. Not needed for v1.
- **Multi-user shared sidecar**: a single sidecar serving multiple developers' project devcontainers, with per-user authentication. Out of scope for v1.

## Terms

Carrying over from [`005-credential-broker/spec.md`](../005-credential-broker/spec.md#terms-and-definitions): Backend (L0) is no longer applicable; remaining definitions (Node, Instance, Devcontainer, Project, Project Manifest, Project Socket, Admin Socket) carry forward unchanged.

New terms:

| Term | Definition |
|---|---|
| **Sidecar / Vault devcontainer** | A remo-managed devcontainer that exists on every remo instance, dedicated to credential management. Appears in the project picker as `_remo-vault`. Owns the source-of-truth for the user's credentials on this instance. |
| **Project devcontainer** | A user-managed devcontainer for a specific project, where the user's code and AI agents run. Receives credentials from the broker as env vars or tmpfs files at startup. |
| **Push** | The action by which the sidecar sends its current credential set to the broker's admin socket. Triggered automatically by an inotify watcher on the sidecar's fnox storage. |
| **Atomic full replace** | The broker applies a `push-creds` update by swapping its whole in-memory credential store to the newly pushed snapshot in one step, so additions, updates, and deletions become visible together. |
| **Cache invalidation on push** | After the broker accepts a new pushed snapshot, it clears all per-project cached secret values immediately rather than serving them until TTL expiry. |
| **Structured credential bundle** | A single named secret value composed of multiple keyed fields, intended for template-based file rendering (for example an AWS credentials file assembled from `aws_access_key_id` and `aws_secret_access_key`). |

## See also

- [plan.md](./plan.md) — phased implementation plan
- [`remo-broker` spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets) — the on-instance daemon redesign
- [Closed PR #32](https://github.com/get2knowio/remo/pull/32) — the superseded 005 implementation
