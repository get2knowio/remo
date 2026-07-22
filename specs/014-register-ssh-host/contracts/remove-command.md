# Contract: `remo remove`

Deregister a manually-added SSH host. Local-only — never contacts or mutates the
remote environment (FR-008, SC-004).

## Synopsis

```text
remo remove NAME [--yes]
```

- `NAME` (arg, required): the added host to deregister.
- `--yes`: bypass the confirmation prompt.

## Behavior

| # | Precondition | Action | Result | Exit |
|---|--------------|--------|--------|------|
| 1 | NAME is an existing **`ssh`** entry | confirm (unless `--yes`) → delete | `remove_known_host("ssh", NAME)`; entry gone from registry & picker; **no network call** (SC-004) | 0 |
| 2 | NAME is a **provider-managed** entry (`incus`/`proxmox`/`aws`/`hetzner`) | — | refuse with a message distinguishing deregister from the provider's `destroy` (FR-009) | ≠0 |
| 3 | NAME not found | — | clear "no such added host" message | ≠0 |
| 4 | NAME already absent after a prior remove | — | idempotent no-op path (Constitution III) | per #3 |

## Post-conditions

- Case 1: the `ssh:NAME:…` line is gone; `remo shell NAME` now reports "no
  environment named NAME"; the remote host is untouched (no SSH/API call occurred).
- Cases 2–3: the registry is **unchanged**.

## Guarantees

- **Never** performs destroy/stop/mutation of the remote environment (contrast
  with provider `destroy`). Removal is a registry delete only.
