# Implementation Plan: Credential Broker (Laptop-Push Model)

**Spec**: [spec.md](./spec.md)
**Cross-repo**: [`remo-broker` spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets)
**Estimate**: ~3 weeks of focused work, parallelizable in places

## What stays from 005 (chassis — ~60% of laptop-side code)

**Laptop CLI**
- `core/fnox.py` (fnox subprocess wrapper)
- `core/known_hosts.py` (instance registry; extended with pinned-pubkey field)
- `cli/main.py` group structure + passive reminder pattern (reframed "overdue-push")
- `cli/audit.py` (unchanged)
- `cli/destroy.py` pre-deletion hook (issues `clear-creds` instead of revoke)
- All provider `create` / `destroy` plumbing (Hetzner, AWS, Incus, Proxmox)
- `core/broker_admin.py` NDJSON-over-SSH transport (admin-op opcodes change, transport stays)
- `_push_bootstrap_token_to_container` helpers repurposed as `_push_encrypted_secrets_blob`
- SSH host-key verification (Hetzner), `incus exec` bridge, `pct exec` bridge

**Ansible**
- `broker_install` role
- Devcontainer socket bind-mount role
- `tasks/configure_dev_tools.yml` + per-provider configure plays
- `scripts/grep-credential-leaks.sh` pre-commit gate

**Cross-repo**
- Published `remo-broker` binary + release workflow shape
- Systemd unit pattern (`LoadCredentialEncrypted=`, just renaming the artifact)
- Per-project manifest format + JSON Schema

## What gets ripped out (~30% of 005 code)

**Laptop CLI**
- `cli/init.py` — `--backend` flag, backend picker UI, `--admin-sa-fnox-key`, `--accept-downgrade` → replaced with no-arg `remo init`
- `core/broker_config.py` — `get_backend()`, `get_admin_sa_fnox_key()`
- `providers/broker.py` — all of `_1password_*`, `_vault_*`, `_aws_sm_*`, `_age_git_*` + dispatchers `mint_bootstrap_token` / `revoke_bootstrap_token`
- `cli/rotate.py` — `remo rotate-bootstrap` entirely
- `core/broker_revoke.py` — `TokenLookupError`, per-provider token-id lookups
- Per-provider cadence persistence (Hetzner labels, AWS tags, Incus `user.remo.*`, Proxmox `/etc/remo-broker/rotation_cadence_days`)

**Ansible**
- `bootstrap_token_file`, `bootstrap_token_mount`, `bootstrap_token_imds` assertion roles
- The bootstrap-token-specific bits of `broker_install` (the role survives; just stops dealing with a bootstrap token)

**Tests**
- `tests/unit/providers/test_broker_mint.py`, `test_broker_revoke.py`
- `tests/unit/providers/test_cadence_writes.py` (rotation-cadence-to-provider-native-metadata)
- `tests/unit/cli/test_rotate.py`
- `tests/unit/providers/test_*_token_push.py` (rewrite — push semantics change)

## What's new (~10% genuinely new code)

**Laptop CLI**
- `cli/push_creds.py` — new `remo push-creds <instance> [--project <p>]` command
- `core/encrypted_blob.py` — age encryption/decryption (`pyrage` lib)
- `core/instance_keys.py` — pin instance broker pubkey in `nodes.yml`; refuse to push if advertised key changes without re-pin
- `cli/add_creds.py` — optional ergonomic helper to register a secret in fnox
- `core/instance_publickey.py` — admin-socket op `get-public-key` for first-contact pinning

**On-instance broker** — see [remo-broker spec 002](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets). Net: ~80% chassis carries; `src/backend.rs` + `src/bootstrap.rs` deleted entirely; new `src/store.rs` for the in-memory map; new admin op `push-creds`; wire protocol v2.

**Ansible**
- `broker_setup_decryption_key` role — runs `systemd-creds setup` with the TPM2 → host-key → plaintext-0600 fallback ladder; idempotent; surfaces which tier was chosen

## Sequencing

### Phase 0: Foundation (1–2 days) — **in progress**

- [x] Write this spec (`specs/006-credential-broker-laptop-push/spec.md`)
- [x] Write this plan (`specs/006-credential-broker-laptop-push/plan.md`)
- [x] Write cross-repo spec (`remo-broker:specs/002-laptop-push-secrets/spec.md`)
- [x] Close PR #32 with a comment pointing at the new specs
- [ ] Decide on the laptop-side `age` Python binding (`pyrage` vs. `pgpy`-style shelling-out to `age` CLI)
- [ ] Decide on the on-instance `secrets.enc` path: `/var/lib/remo-broker/secrets.enc` (under `StateDirectory=`) confirmed per audit recommendation

### Phase 1: Strip the wrong design (2–3 days)

New branch `006-credential-broker-laptop-push`, branched from `main`.

- Delete `cli/rotate.py`, `core/broker_revoke.py`, `core/broker_config.py`, `providers/broker.py`
- Delete the four `bootstrap_token_*` Ansible roles
- Delete the `--backend` / `--admin-sa-fnox-key` / `--accept-downgrade` flags and backend-picker UI
- Delete the per-provider cadence read/write code
- Delete the corresponding tests
- Keep `core/broker_admin.py` (will be repurposed in Phase 2)
- Keep `broker_install` role (will be modified in Phase 2)

**Exit**: tests pass, no dead code, `remo init` is a no-op stub, no backend selection anywhere.

### Phase 2: New laptop side (3–5 days)

- Implement `core/encrypted_blob.py` (age encrypt/decrypt; `pyrage`)
- Implement `core/instance_keys.py` (pin pubkey in `nodes.yml`)
- Implement `cli/push_creds.py` (manifest read, fnox fetch, encrypt, push, admin-socket call)
- Wire `push-creds` into the create flow (auto-push after Ansible converge)
- Update destroy flow to call admin-socket `clear-creds`
- Implement passive overdue-push reminder
- Tests for each

**Exit**: `remo push-creds <instance>` works against a mock broker; full unit coverage.

### Phase 3: New broker side (cross-repo, 5–7 days)

See [remo-broker spec 002 §Sequencing](https://github.com/get2knowio/remo-broker/tree/main/specs/002-laptop-push-secrets). Net deliverable: signed `remo-broker v0.2.0` binary release with wire-protocol v2.

**Exit**: broker installs, reads encrypted blob at startup, serves via socket, accepts `push-creds` admin op. Update `BROKER_PINNED_VERSION` in remo to `0.2.0`.

### Phase 4: End-to-end validation + docs (2–3 days)

- Real e2e test on user's Proxmox lab (the loop started 2026-05-29)
- Update `docs/credential-broker.md` (now reflects the new model)
- Update README, getting-started, threat model
- Cut `2.2.0rc1` and announce

**Exit**: full lifecycle works on Proxmox; docs accurate; first non-pre-release tag possible.

## Open questions

| # | Question | Decided? |
|---|---|---|
| 1 | Encryption primitive | ✅ `age` |
| 2 | Decryption-key sourcing | ✅ Fallback ladder: TPM2 → host-key → plaintext-mode-0600 |
| 3 | `agentsh` integration scope | ✅ Out of scope for this redesign; separate future spec |
| 4 | Wire schema v2 as release artifact | ✅ Yes (publish `schema/remo-broker.v2.json` alongside binaries) |
| 5 | 005 spec disposition | ✅ Leave intact as historical reference; this spec links back |
| 6 | PR #32 disposition | ✅ Close with explanatory comment |
| 7 | remo-broker 001 spec | ✅ Superseded by remo-broker 002 |
| 8 | `age` library on laptop: `pyrage` (binding) vs. shelling out to `age` CLI | ✅ `pyrage` — no user-side `age` install required; in-process; typed errors; testable |
| 9 | `secrets.enc` path: under `StateDirectory=` (`/var/lib/remo-broker/secrets.enc`) | ✅ Per audit recommendation |
| 10 | First-contact pubkey trust model: TOFU + warn-on-change, or strict | ✅ TOFU. Pin silently on first contact (during `remo create`, SSH layer is already trusted); warn loudly on any subsequent change; provide `remo {provider} repin <instance>` to acknowledge legitimate rebuilds. Matches SSH host-key UX. Optional `--strict-pin` flag deferred until demand. |
| 11 | Per-project vs. single global encrypted blob on the instance | Open — Phase 2 decision (lean single blob; simpler swap semantics) |

## What happens to 005 artifacts

- **PR #32**: closed with link to this spec
- **`005-credential-broker` branch**: intact as historical reference; not deleted
- **`specs/005-credential-broker/`**: intact; this spec links back as "supersedes"
- **`docs/credential-broker.md`**: rewritten in Phase 4 (don't touch until then)
- **`remo-broker v0.1.0` release**: stays published; `v0.2.0` will supersede
