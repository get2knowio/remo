# Contract: CLI and Remote Helper Surface

**Date**: 2026-05-31

This feature intentionally keeps the laptop CLI shape stable. New behavior is introduced through existing commands plus new helper commands inside the `_remo-vault` sidecar.

## Local CLI surface

### Provider create/update

```text
remo <provider> create [existing flags...]
remo <provider> update [existing flags...]
```

Where `<provider>` is one of `aws`, `hetzner`, `incus`, or `proxmox`.

**Contract**
- No new required laptop-side flags are introduced.
- Successful create/update provisions or reconciles:
  - the `remo-broker` daemon on the host
  - the `_remo-vault` sidecar devcontainer
  - helper scripts and the project-side secrets feature assets
- Failures in broker/sidecar provisioning stop the command with explicit remediation text.

### Shell access

```text
remo shell
remo shell -p <project>
remo shell -p _remo-vault
```

**Contract**
- `_remo-vault` appears in the project picker as a reserved entry sorted with the managed/system entries.
- `remo shell -p _remo-vault` jumps directly into the sidecar shell.
- Existing `--exec` and `--detach` behavior remains unchanged for user projects.
- The sidecar target is treated as managed infrastructure, not a normal repo checkout.

## Remote helper commands (inside `_remo-vault`)

| Command | Arguments | Purpose | Success contract |
|---|---|---|---|
| `remo-list-creds` | none | List stored secret names and metadata only | Shows names plus non-secret metadata such as last updated or last pushed timestamp; never prints plaintext values |
| `remo-test-project` | `<project>` | Test whether a project's manifest-declared secrets can be fetched | Returns per-secret success/failure and non-zero exit on any missing required secret |
| `remo-vend-status` | none | Inspect broker memory state from the sidecar | Shows broker protocol version, `secret_count`, `secrets_loaded_at`, and key-source posture |
| `remo-reload` | `<project>` | Trigger broker manifest reload after an out-of-container manifest edit | Returns success only when the broker has revalidated and atomically reloaded the manifest |

## Project startup contract

The project devcontainer's startup path gains a remo-managed fetch step.

```text
devcontainer up
  -> remo/secrets-feature entrypoint
  -> broker per-project socket fetches
  -> render env vars and tmpfs files
  -> hand off to user's normal entrypoint
```

**Rules**
- Required manifest-declared secrets retry for up to 15 seconds, then startup exits non-zero.
- `fetch_as = "env"` exports env vars before the user's shell or command starts.
- `fetch_as = "file"` renders files into tmpfs only.
- Startup never mutates the manifest; manifest edits happen outside the project devcontainer.

## Test contract

The implementation should cover at least these user-visible scenarios:

| Scenario | Expected result |
|---|---|
| `remo shell` picker includes `_remo-vault` | Reserved sidecar entry is visible and distinguishable from user projects |
| `remo shell -p _remo-vault` | Lands in the sidecar shell |
| Provider `create` on a fresh instance | Broker + sidecar are provisioned and ready |
| Provider `update` on an existing instance | Reconciles broker + sidecar idempotently |
| Project startup with required secrets present | Startup succeeds and renders env/file outputs |
| Project startup with a missing required secret | Retries for 15 seconds, then fails closed |
| `remo-reload <project>` after manifest edit | Broker reloads manifest without recreating the project socket |
