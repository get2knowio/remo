# Implementation Plan: Credential Broker (Sidecar Devcontainer Model)

**Spec**: [spec.md](./spec.md)
**Cross-repo**: [`remo-broker` spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets)
**Estimate**: ~2 weeks of focused work (down from 3 — the sidecar pivot eliminates the encryption/pubkey machinery)

## What stays from 005 (chassis — ~50% of laptop-side code)

**Laptop CLI** — unchanged in shape. No new commands.

- `cli/init.py` (rewritten to drop `--backend`; otherwise simple)
- `cli/main.py` group structure (passive reminders may be removed entirely; nothing to remind about anymore)
- `cli/audit.py` (unchanged)
- `cli/destroy.py` (simpler — just destroys the LXC; no pre-revoke step needed)
- All provider `create` / `destroy` / `list` / `add-node` plumbing (Hetzner, AWS, Incus, Proxmox)
- `core/known_hosts.py`
- SSH host-key verification (Hetzner), `incus exec`, `pct exec` bridges — still used by provider commands

**Ansible**

- `broker_install` role (simplified — no encrypted-blob handling, just install the binary + systemd unit)
- `tasks/configure_dev_tools.yml` and per-provider configure plays
- `scripts/grep-credential-leaks.sh` pre-commit gate

**Cross-repo**

- Published `remo-broker` binary + release workflow shape (simplified per spec 002)
- Systemd unit pattern (minus the `LoadCredentialEncrypted=secrets-key` block — broker has no secrets to load)
- Per-project manifest format + JSON Schema (extended with `fetch_as`)

## What gets ripped out (~35% of 005 code)

**Laptop CLI**

- `cli/init.py` — `--backend`, `--admin-sa-fnox-key`, `--accept-downgrade` flags
- `core/broker_config.py` entirely
- `providers/broker.py` entirely (all four backend impls + dispatchers)
- `cli/rotate.py` entirely
- `core/broker_revoke.py` entirely
- `core/broker_admin.py` NDJSON-over-SSH transport (push is local now, not over SSH)
- Per-provider cadence persistence code (Hetzner labels, AWS tags, Incus `user.remo.*`, Proxmox `/etc/remo-broker/rotation_cadence_days`)
- Bootstrap-token plumbing in every provider's create/destroy

**Ansible**

- `bootstrap_token_file`, `bootstrap_token_mount`, `bootstrap_token_imds` roles

**Tests**

- `tests/unit/providers/test_broker_mint.py`, `test_broker_revoke.py`
- `tests/unit/providers/test_cadence_writes.py`
- `tests/unit/cli/test_rotate.py`
- `tests/unit/providers/test_*_token_push.py`
- `tests/unit/core/test_broker_admin.py`

## What's new (~15% genuinely new code, smaller than first 006 draft)

**Ansible**

- `vault_devcontainer_install` role — builds and starts the sidecar devcontainer; configures its fnox storage; sets up bind-mount of the broker admin socket; sets up `LoadCredential` of the fnox decryption key from systemd-credentials on the LXC host
- `vault_decryption_key_setup` role — runs `systemd-creds setup` on the LXC host with the TPM2 → host-key → plaintext-mode-0600 fallback ladder; idempotent; surfaces which tier was chosen

**Sidecar devcontainer image** (new artifact, lives under `ansible/roles/vault_devcontainer_install/files/` or a dedicated dir)

- `Dockerfile` — debian-slim base + gh + aws + claude + fnox + standard shell tooling
- `devcontainer.json` — defines mounts (broker admin socket from host, fnox-storage volume, decryption key as Docker secret), startup command, MOTD
- `/usr/local/bin/remo-vault-watcher` — inotify daemon (Python, small) that watches fnox storage and calls broker admin `push-creds` on changes
- `/usr/local/bin/remo-list-creds` — wrapper around `fnox list` with last-pushed metadata
- `/usr/local/bin/remo-test-project <name>` — reads project's manifest, asks the broker to fetch each declared secret as that project would, reports per-secret success
- `/usr/local/bin/remo-vend-status` — admin-socket call to broker for current in-memory state summary
- `/usr/local/bin/remo-reload <project>` — admin-socket call to broker's `reload` op
- Custom MOTD on shell entry

**Project devcontainer feature** (new artifact, `ansible/roles/secrets_feature/files/` or dedicated dir)

- Distributed as a devcontainer feature (https://containers.dev/features) so user projects can reference it from their own `devcontainer.json`
- `/usr/local/bin/remo-fetch-secrets` — runs at devcontainer startup; reads `.remo/manifest.toml`; per secret, calls broker per-project socket; per `fetch_as`, either exports env var or materializes file in tmpfs; hands control off to user's entrypoint
- Devcontainer-feature install script that adds the bind-mount for the read-only manifest and the per-project socket

**On-instance broker** — see [remo-broker spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets). Net: ~85% of the chassis carries; ripping the backend + bootstrap + secrets-blob + age-decrypt code is significantly more aggressive than the first 006 draft. New code is just the `InMemorySecretStore`, the `push-creds` admin op (plaintext input now), and the `clear-creds` admin op.

## Sequencing

### Phase 0: Foundation (complete)

- [x] Write this spec (`specs/006-credential-broker-laptop-push/spec.md`)
- [x] Write this plan (`specs/006-credential-broker-laptop-push/plan.md`)
- [x] Write cross-repo spec (`remo-broker:specs/002-laptop-push-secrets/spec.md`)
- [x] Close PR #32 with a comment pointing at the new specs
- [x] PR #34 (remo specs) + PR #10 (remo-broker specs) open, cross-linked

### Phase 1: Strip the wrong design (2–3 days)

New branch `006-credential-broker-sidecar`, branched from `main`.

- Delete `cli/rotate.py`, `core/broker_revoke.py`, `core/broker_config.py`, `core/broker_admin.py`, `providers/broker.py`
- Delete the four `bootstrap_token_*` Ansible roles
- Delete the `--backend` / `--admin-sa-fnox-key` / `--accept-downgrade` flags and backend-picker UI
- Delete the per-provider cadence read/write code
- Delete the corresponding tests
- Keep `broker_install` role (will be simplified in Phase 2)

**Exit**: tests pass, no dead code, `remo init` is a no-op stub.

### Phase 2: Sidecar provisioning + broker simplification (4–5 days)

Parallelizable with Phase 3 on the cross-repo side once the broker's new admin protocol is stable.

**Laptop / Ansible side**:

- Implement `vault_decryption_key_setup` Ansible role (TPM2 → host-key → plaintext fallback ladder)
- Implement `vault_devcontainer_install` Ansible role
- Build the sidecar Dockerfile + devcontainer.json
- Write `remo-vault-watcher` (Python, ~100 LOC)
- Write the four helper scripts (`remo-list-creds`, `remo-test-project`, `remo-vend-status`, `remo-reload`)
- Wire sidecar provisioning into all four `{provider}_site.yml` playbooks

**Exit**: `remo proxmox create` provisions an LXC with a working sidecar; user can SSH in, see `_remo-vault` in the picker, drop into the sidecar, run `gh auth login`, observe the broker pick up the credential.

### Phase 3: Project devcontainer feature (2–3 days)

- Define the manifest schema extension (`fetch_as` per secret)
- Build the `remo/secrets-feature` devcontainer feature
- Write `remo-fetch-secrets` (Python, ~150 LOC — manifest read, broker socket call, env/file injection)
- Document how a user adds the feature to their project's `devcontainer.json`
- Update `core/known_hosts.py` / project discovery to register the read-only manifest bind-mount

**Exit**: a user can clone a project, add the feature to their devcontainer config, declare secrets in `.remo/manifest.toml`, and the secrets appear as env vars / tmpfs files in their project devcontainer at startup.

### Phase 4: Broker side (cross-repo, 3–4 days)

See [remo-broker spec 002 §Sequencing](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets#sequencing). Net deliverable: signed `remo-broker v0.2.0` binary release with wire-protocol v2.

**Exit**: broker installs, starts empty, accepts plaintext `push-creds`, serves per-project sockets, audits correctly. Update `BROKER_PINNED_VERSION` in remo to `0.2.0`.

### Phase 5: End-to-end validation + docs (2 days)

- Real e2e test on user's Proxmox lab
- Update `docs/credential-broker.md` (now reflects the sidecar model)
- Update README, getting-started, threat model
- Cut `2.2.0rc1` and announce

**Exit**: full lifecycle works on Proxmox; docs accurate; first non-pre-release tag possible.

**Total**: ~13–17 days focused work. Parallelizable in Phase 2/3 (laptop side) vs Phase 4 (broker side).

## Open questions

| # | Question | Decided? |
|---|---|---|
| 1 | Encryption primitive for sidecar fnox storage | ✅ Standard fnox encrypted-file backend; key from systemd-credentials |
| 2 | Decryption-key sourcing for sidecar fnox | ✅ Fallback ladder: TPM2 → host-key → plaintext-mode-0600 |
| 3 | `agentsh` integration scope | ✅ Out of scope; separate future spec |
| 4 | Wire schema v2 as release artifact | ✅ Yes (publish `schema/remo-broker.v2.json` alongside binaries) |
| 5 | 005 spec disposition | ✅ Leave intact as historical reference |
| 6 | PR #32 disposition | ✅ Closed |
| 7 | remo-broker 001 spec | ✅ Superseded by remo-broker 002 |
| 8 | Sidecar appears in project picker as | ✅ `_remo-vault` (underscore prefix sorts first; visually distinct) |
| 9 | Manifest protection mechanism | ✅ Read-only bind-mount of `.remo/manifest.toml` into project devcontainer |
| 10 | Sidecar→broker push triggering | ✅ Inotify watcher in sidecar; auto-push on fnox storage change |
| 11 | Sidecar shell UX | ✅ Philosophy A: sidecar is just another picker entry; user runs upstream CLIs + helper scripts. TUI deferred. |
| 12 | OAuth UX for browser-callback CLIs (Claude) | ✅ User uses SSH port-forwarding (`ssh -L 8080:localhost:8080`); not remo's problem to make Claude's OAuth seamless |
| 13 | `fetch_as` schema extensions beyond `env` and `file` | Open — Phase 3. Possibly `socket` (FUSE-mounted Unix socket that lazy-fetches) for future, but YAGNI for v1. |
| 14 | Devcontainer feature distribution | Open — Phase 3. Hosted in this repo as `features/secrets/`, referenced from user devcontainer.json as `ghcr.io/get2knowio/remo/secrets:1`? |

## What happens to 005 artifacts

- **PR #32**: closed (done)
- **`005-credential-broker` branch**: intact as historical reference; not deleted
- **`specs/005-credential-broker/`**: intact; this spec links back as "supersedes"
- **`docs/credential-broker.md`**: rewritten in Phase 5
- **`remo-broker v0.1.0` release**: stays published; `v0.2.0` will supersede via `BROKER_PINNED_VERSION` bump

## What happens to the laptop-push 006 draft

- The first 006 draft (2026-05-30, laptop-push with age encryption) is captured in the PR #34 commit history (commits `aa9e91a` and `849250c`) for archival reference
- This rewrite (2026-05-31, sidecar) supersedes it within the same spec dir; no separate spec number used
