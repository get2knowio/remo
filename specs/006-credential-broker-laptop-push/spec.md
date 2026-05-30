# Feature Specification: Credential Broker (Laptop-Push Model)

**Feature Branch**: `006-credential-broker-laptop-push`
**Created**: 2026-05-30
**Status**: Draft
**Supersedes**: [`005-credential-broker`](../005-credential-broker/) (laptop CLI + external-backend model — see [#32](https://github.com/get2knowio/remo/pull/32) for the closed PR)
**Cross-repo dependency**: [`remo-broker` spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets) (the on-instance daemon)

**Input**: Defend a remo dev instance and its devcontainers against AI agents and supply-chain attacks by ensuring no plaintext credentials exist anywhere an agent or malicious dependency can read them. Replace the 005 external-backend design with a model where the laptop pushes encrypted secrets to the instance at provision time, the broker decrypts in memory with a TPM-sealed key, and devcontainers fetch via a Unix socket gated by a per-project allowlist.

## Why the redesign

005 implemented the canonical "external secret manager + bootstrap-token-on-instance" pattern (Vault / AWS-SM / 1Password as backend; broker fetches on demand). End-to-end testing on 2026-05-29 surfaced a categorical mismatch with the actual threat model:

- The `/etc/remo-broker/bootstrap-token` file is itself a credential. An attacker who gets a shell on the instance — including via supply-chain attack inside the devcontainer that subsequently escalates — can exfiltrate it and pull every secret behind it from any attacker-controlled box, bypassing the broker's per-project manifest gate.
- The supply-chain protection story is degraded by this residual on-disk credential, in direct opposition to the [origin-story principle](https://x.com/nateberkopec/status/2048634637447201264): "scrub all credentials stored anywhere in plaintext on my system. No more `.env`, no more `~/.aws/credentials`."
- The model adds operational dependencies (Vault server, 1P SCIM Bridge, AWS-SM IAM dance) for a solo-dev/small-team audience that doesn't need centralized secret management.
- `age-git` was advertised as a downgrade path but never implemented past `init` (see closed PR #32 findings tally).

The PocketOS incident (an AI coding agent finding an unrelated API token in a file and using it to delete production data in a single API call) sharpened the requirement: an AI agent running in the devcontainer must not be able to find credentials by reading files, full stop.

## Threat model

The threat is **any code running inside the devcontainer with the developer's UID**:
- AI coding agents (Claude Code, Cursor, etc.) following injected or hallucinated instructions
- Malicious or compromised npm / pip / cargo / etc. dependencies (Shai-Hulud, the Axios-vector incidents)
- Misbehaving CLI tools that scan filesystem for "useful" credentials

Out of scope:
- A privileged attacker who has already obtained root on the instance host (Proxmox node, AWS hypervisor)
- Compromise of the developer's laptop itself
- OAuth-flow credentials the user obtains by running `<tool> login` *inside* the devcontainer — these are addressable only via execution-layer policy (see [§Future work](#future-work) on agentsh)

## Requirements

### Functional

| ID | Requirement |
|---|---|
| FR-001 | At `remo {provider} create`, the laptop encrypts a project-scoped set of secrets read from `fnox` and pushes the resulting blob to the instance over SSH. |
| FR-002 | The instance stores the encrypted blob at a single canonical path (`/var/lib/remo-broker/secrets.enc`, owned by the broker service user, mode 0600). |
| FR-003 | The instance broker decrypts the blob at startup using a key sourced via systemd `LoadCredentialEncrypted=` and holds the cleartext secrets only in process memory. |
| FR-004 | The decryption key is established at install time using a fallback ladder: TPM2-sealed (`systemd-creds setup --with-key=auto+tpm2`) where the host exposes `/dev/tpm0`; host-key (`auto`) otherwise; mode-0600 plaintext as a last-resort opt-in. |
| FR-005 | The encryption primitive is `age` (X25519 + ChaCha20-Poly1305). Each instance has its own age identity; the laptop encrypts to that instance's public recipient. |
| FR-006 | At first contact (during `remo {provider} add-node` or `create`), the laptop pins the instance's broker public key in `~/.config/remo/nodes.yml` and refuses to push to an instance whose advertised key changes without explicit re-pin. |
| FR-007 | A new CLI verb, `remo push-creds <instance> [--project <p>]`, performs an out-of-band push: reads the project manifest, encrypts the allowed subset from `fnox`, ships the blob, and triggers the broker's atomic in-memory swap. |
| FR-008 | The broker exposes a `push-creds` admin-socket operation (NDJSON) that accepts an inline base64-encoded ciphertext, atomically writes `secrets.enc.tmp` → fsync → rename → swaps the in-memory `Arc<HashMap>`, and emits an `AuditEvent::SecretsPushed`. |
| FR-009 | `remo destroy` issues a `clear-creds` admin op (atomic blank-store + `secrets.enc` zeroize) *before* deleting the instance. |
| FR-010 | A passive overdue-push reminder fires on every `remo` invocation if any registered instance has not received a `push-creds` within its configured cadence (default 7 days). |
| FR-011 | `fnox` remains the laptop's secret store. Required keys: project secrets (e.g. `github_pat`, `openai_api_key`), provisioning creds (e.g. `hetzner_api_token`). No "admin SA token for backend" key — that concept is gone. |
| FR-012 | The per-project manifest (`.remo/manifest.toml` or `.devcontainer/remo-broker.toml`) format from 005 carries forward unchanged. |
| FR-013 | The devcontainer-facing per-project socket protocol from 005 carries forward unchanged (`get` / `ping` / `info`, NDJSON, manifest allowlist enforcement). |
| FR-014 | The audit log format from 005 carries forward, with the addition of `AuditEvent::SecretsPushed`. |

### Non-functional

| ID | Requirement |
|---|---|
| NFR-001 | A devcontainer-side `find / -type f \( -name '*.env' -o -name 'credentials*' -o -name '.netrc' -o -path '*/.aws/*' -o -path '*/.config/gh/*' \) 2>/dev/null` returns no useful results on a freshly provisioned instance. |
| NFR-002 | After a destroy, an EBS-snapshot / disk-image exfiltration of the instance yields no recoverable plaintext secrets, *provided* the decryption key was TPM-sealed (FR-004 tier 1) or host-key-bound (tier 2). The plaintext-mode-0600 tier is best-effort and is documented as such. |
| NFR-003 | `remo push-creds` end-to-end latency is < 2s for a 10 KiB plaintext payload on a 50ms-RTT link. |
| NFR-004 | The redesign requires no external service dependency (no Vault, no AWS-SM, no 1Password SCIM). `fnox` on the laptop is the only secret-storage component. |
| NFR-005 | The published `remo-broker` binary (per cross-repo spec 002) is ≤ 15 MiB stripped (the original NFR target on remo-broker spec 001, missed at v0.1.0 due to `fnox-core` transitive deps). |

### Removed from 005

The following carry over from 005 conceptually but are eliminated in implementation:

- `remo init --backend {1password|vault|aws-sm|age-git}` → replaced with a no-arg `remo init` that only installs Ansible collections
- `remo rotate-bootstrap` → replaced by `remo push-creds` (no separate "rotate the bootstrap token" lifecycle; pushing fresh creds *is* the rotation)
- `--admin-sa-fnox-key` flag and the entire admin-SA-token concept
- `--accept-downgrade` flag (the warning it gated is gone)
- The four `bootstrap_token_{file,mount,imds}` Ansible assertion roles
- Per-instance rotation cadence persistence across provider-native metadata (Hetzner labels, AWS tags, Incus `user.remo.*`, Proxmox in-container files) → replaced by a single "last push" timestamp held by the broker
- `core/broker_revoke.py` (`TokenLookupError`, per-provider token-id lookup) — no token to revoke
- `providers/broker.py` mint/revoke dispatchers and all four backend implementations

## Architecture

```
laptop:
  fnox + OS keychain       (project secrets + provisioning creds)
                                │
                                │  remo push-creds <instance>
                                │  (encrypt with age to instance's pinned pubkey, ship via SSH)
                                ▼
instance:
  /var/lib/remo-broker/secrets.enc   (encrypted at rest, age ciphertext)
                                │
                                │  decryption key from $CREDENTIALS_DIRECTORY/secrets-key
                                │  (TPM-sealed → host-key → mode-0600 fallback ladder)
                                ▼
  remo-broker daemon       (in-memory Arc<HashMap<String, SecretString>>)
                                │
                                │  per-project Unix sockets, NDJSON, manifest allowlist
                                ▼
  devcontainer:
    no .env files, no ~/.aws/credentials, no on-disk creds
    requests via socket → broker checks manifest → returns secret as env var or stdin
```

## Cross-cutting decisions

1. **Encryption primitive: `age`.** Audited, multi-recipient native, mature Rust crate (`age`), mature Python bindings (`pyrage`), well-supported CLI (`age` / `age-keygen`) for ad-hoc operator use.
2. **Decryption-key sourcing: fallback ladder.** TPM2 > host-key > plaintext-mode-0600. TPM-required would fail on most Proxmox LXC guests, which is the primary test target. The Ansible install role picks the highest available tier and surfaces which one was chosen in a post-install message.
3. **Wire protocol bumps to v2** in remo-broker (removing `rotate-bootstrap` and `bootstrap_mode` are breaking per the project's own additive-only-within-major rule). A `schema/remo-broker.v2.json` artifact will ship alongside the v0.2.0 release.
4. **No `agentsh` in this spec.** The execution-layer policy gateway (agentsh.org) is a complementary defense that addresses the OAuth-flow-credential case this spec does not protect (FR-§Threat model). Deferred to a future spec; the broker redesign does not depend on it.
5. **No backward-compat shims with 005.** Anyone who installed `2.1.0rc1` from the closed PR #32 (likely nobody — no public release was cut) will see the broker mode change cleanly via `remo init`'s new no-arg behavior.

## Future work

- **agentsh integration** (separate spec): wrap the devcontainer's agent process under `agentsh wrap` with a default policy that denies reads of cred-shaped paths and whitelists env vars per command. Addresses the OAuth-cached-credential gap.
- **Headless / no-laptop refresh**: a future spec could re-introduce an optional backend mode for headless instances (CI runners, autoscaled fleets) that need fresh creds without a laptop in the loop. Out of scope here.
- **Per-secret TTL hints**: the encrypted-blob envelope could carry per-secret expiry metadata so the broker rotates individual secrets out of memory ahead of a full re-push. Not needed for v1.

## Terms

Carrying over from [`005-credential-broker/spec.md`](../005-credential-broker/spec.md#terms-and-definitions): Backend (L0) is no longer applicable; remaining definitions (Node, Instance, Devcontainer, Project, Project Manifest, Project Socket, Admin Socket) carry forward unchanged.

## See also

- [plan.md](./plan.md) — phased implementation plan (5 phases, ~3 weeks)
- [remo-broker spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets) — the on-instance daemon redesign
- [Closed PR #32](https://github.com/get2knowio/remo/pull/32) — the superseded 005 implementation
