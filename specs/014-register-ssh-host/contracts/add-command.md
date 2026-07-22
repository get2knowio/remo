# Contract: `remo add`

Register a single SSH-reachable environment into the registry. Provider-neutral;
requires only SSH reachability (FR-001).

## Synopsis

```text
remo add NAME TARGET [--user USER] [--port PORT] [--identity PATH] [--verify] [--yes]
```

- `NAME` (arg, required): user-facing name. Validated by `validate_name`
  (FR-013). Must not collide with a provider-managed entry (FR-010).
- `TARGET` (arg, required): `[user@]host[:port]`. Parsed per data-model D4.
- `--user USER`: SSH user; overrides any `user@` in TARGET. Default
  `DEFAULT_ADDED_HOST_USER` (`remo`), reported back (FR-003).
- `--port PORT`: SSH port (int); overrides any `:port` in TARGET. Default `22`.
- `--identity PATH`: SSH private key path, persisted and used via `ssh -i` on
  connect (FR-004). Must not contain `:` (D5).
- `--verify`: opt-in SSH reachability check before registering (FR-014).
- `--yes`: bypass the confirmation prompt on an in-place update (FR-007).

## Behavior

| # | Precondition | Action | Result | Exit |
|---|--------------|--------|--------|------|
| 1 | Name unused, valid target | register | `ssh` entry written; success names `remo shell <name>` and the effective user | 0 |
| 2 | Name held by a **provider** entry | — | refuse; message names the conflicting entry; **no write** (FR-010/SC-005) | ≠0 |
| 3 | Name held by an existing **`ssh`** entry, target changed | update in place | confirm (unless `--yes`) → replace line; no duplicate (FR-007/SC-003) | 0 |
| 4 | Target is an un-bracketed IPv6 literal, or bracketed `[::1]:…` | — | reject with "use a hostname or `~/.ssh/config` alias"; **no write** (D4) | ≠0 |
| 5 | Port out of `1..65535`, or invalid name | — | reject via `validate_port`/`validate_name`; **no write** | ≠0 |
| 6 | `--identity` path contains `:` | — | reject; **no write** (D5) | ≠0 |
| 7 | `--verify`, target reachable | probe then register | success line reports verified | 0 |
| 8 | `--verify`, target unreachable/auth-fails | probe | surface SSH error; **decline (no write)**; fail-closed (FR-014/US3.2) | ≠0 |
| 9 | no `--verify` | register | no network round-trip at all (FR-014/US3.3) | 0 |

## Post-conditions

- On success (non-verify or verified): exactly one `ssh:NAME:…` line exists;
  `remo shell NAME` and `remo cp` connect via the direct path with the recorded
  port/identity (FR-005, SC-001, SC-002).
- On any rejection/verify-failure: the registry is **unchanged**.

## Storage

Writes a `KnownHost(type="ssh", access_mode="direct")` via `save_known_host`.
Encoding: see [data-model.md](../data-model.md).
