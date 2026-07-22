# Phase 1 Data Model: Register an SSH-Reachable Host (`remo add`)

This feature introduces **no new persisted entity** — it adds a new `type` value
(`ssh`) to the existing `KnownHost` registry model and two type-gated accessors.
See [research.md](./research.md) D1–D2 for the rationale.

## Entity: `KnownHost` (existing) — new `ssh` type

`src/remo_cli/models/host.py`. Fields are unchanged; the `ssh` type reuses the
provider-specific slots for SSH coordinates.

| Field         | `ssh`-type meaning            | Notes |
|---------------|-------------------------------|-------|
| `type`        | `"ssh"`                       | New value. Distinguishes added hosts from providers. |
| `name`        | user-facing name              | Globally unique (enforced at add time, D6). Validated by `validate_name`. |
| `host`        | SSH host / address            | Hostname, IPv4, or `~/.ssh/config` alias. Un-bracketed IPv6 rejected (D4). |
| `user`        | effective SSH user            | From `user@` in target, or `--user`, else `DEFAULT_ADDED_HOST_USER` (`remo`). |
| `instance_id` | SSH **port** (string)         | e.g. `"22"`, `"2222"`. Numeric — no colon risk. |
| `access_mode` | `"direct"`                    | Routes through the existing direct-SSH connection path. |
| `region`      | **identity** path (optional)  | e.g. `"/home/dev/.ssh/box_ed25519"`. Colon rejected at add time (D5). |

### Serialized form (colon-delimited registry line)

No format change — the existing 4/6/7-field encoding covers every case:

```text
ssh:<name>:<host>:<user>[:<port>:direct[:<identity>]]
```

```text
ssh:box:1.2.3.4:remo:22:direct                              # default port, no identity
ssh:api:10.0.0.9:dev:2222:direct                            # custom port
ssh:api:10.0.0.9:dev:2222:direct:/home/dev/.ssh/box_ed25519 # custom port + identity
```

`to_line()`/`from_line()` are **unmodified**: setting `instance_id` (port)
triggers the 6-field form with `access_mode="direct"`; a non-empty `region`
appends the 7th (identity) field; missing trailing fields default on read
(SC-007 backward-compat).

### New accessors (type-gated)

```text
KnownHost.ssh_port     -> int          # int(instance_id) for type=="ssh" else DEFAULT_SSH_PORT
KnownHost.ssh_identity -> str | None   # region or None for type=="ssh" else None
```

Both return neutral values for non-`ssh` types, so no existing behavior changes.

### Display

`display_name` returns `name` unchanged for `ssh` (the `host/container` special
case is `incus`/`proxmox`-only), so added hosts appear by their plain name in the
picker (FR-006).

## Validation rules (at `remo add` time)

| Rule | Source | Enforcement |
|------|--------|-------------|
| Name matches registry name rules, ≤63 chars | FR-013 | `validate_name` (existing) |
| Name not already used by a **provider** entry | FR-010/SC-005 | whole-registry scan (D6) → refuse |
| Name already used by an existing **`ssh`** entry | FR-007/SC-003 | in-place update (confirm unless `--yes`) |
| Port is an int in `1..65535` | FR-013 | `validate_port` (existing) |
| Target is not an un-bracketed IPv6 literal | FR-013/Edge | colon-count parse (D4) → refuse |
| Bracketed `[::1]:22` form | Out of scope | rejected with "use hostname/alias" (D4) |
| Identity path contains no `:` | FR-013 | reject (D5); existence **not** required |

## State & lifecycle

An added host has **no remote/infrastructure lifecycle** owned by `remo`
(spec Out of Scope). Registry-entry lifecycle only:

```text
(absent) --remo add--> registered --remo add (same name, new target)--> updated-in-place
registered --remo remove--> (absent)          # local-only; no remote action (SC-004)
registered --remo shell/cp--> connected       # direct-mode SSH; port+identity applied
```

`remo remove` on an absent entry is a no-op (`remove_known_host` already tolerates
a missing entry) — idempotent (Constitution III).

## Relationships

- **Registry** (`core/known_hosts.py`): added hosts are ordinary lines; they
  participate in `get_known_hosts()`, `resolve_remo_host_by_name()`, and the
  picker exactly like provider entries. `save_known_host` dedupes `(type, name)`;
  cross-type uniqueness is enforced by the add-time scan (D6), not the store.
- **Connection** (`core/ssh.py`): `build_ssh_opts` reads `ssh_port`/`ssh_identity`
  for `type=="ssh"` only (D3).
- **Providers**: unrelated — provider groups filter by their own `type`; FR-012
  guard ensures a mis-targeted provider op on an `ssh` host fails clearly (D9).
