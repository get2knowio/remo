# Phase 0 Research: Register an SSH-Reachable Host (`remo add`)

All four spec ambiguities were resolved in `/speckit-clarify` (see spec
`## Clarifications`, Session 2026-07-22). This document records the remaining
technical decisions, each grounded in the current code.

---

## D1. Registry encoding for an added host

**Decision**: Introduce a new registry `type = "ssh"` and reuse the existing
`KnownHost` positional serialization with **no format change**:

```text
ssh:<name>:<host>:<user>[:<port>:direct[:<identity>]]
```

- `type`        = `"ssh"`
- `name`        = user-facing name
- `host`        = SSH host/address
- `user`        = SSH user (effective, after default/override)
- `instance_id` = SSH port (e.g. `22`, `2222`)
- `access_mode` = `"direct"`  (drives the existing connection dispatch)
- `region`      = identity path (optional; 7th field)

Examples:
```text
ssh:box:1.2.3.4:remo:22:direct
ssh:box:1.2.3.4:dev:2222:direct:/home/dev/.ssh/box_ed25519
```

**Rationale**:
- `KnownHost.to_line()` already emits the 6-field form whenever `instance_id`
  is set (appending `instance_id` + an effective access mode) and appends
  `region` as the 7th field only when non-empty. Setting `access_mode="direct"`
  makes it emit `direct` (not the `ssm` default), so port + identity round-trip
  through the **unmodified** serializer. `from_line()` already parses 4/6/7
  fields and ignores extras → **SC-007 backward-compat is free**.
- Port is numeric → never introduces a stray colon. Identity is the only
  colon-risk; guarded by validation (D5).
- A brand-new `type` keeps provider command groups operating only on their own
  inventory (`get_known_hosts(type_filter=...)`), so an added host is visibly
  *not* a provider instance (spec Assumptions, FR-012).

**Alternatives considered**:
- *New trailing 8th/9th fields for port/identity*: clearer field semantics but
  extends the serialized format for **all** types and touches `to_line` globally
  (AWS already uses all 7 fields). Higher blast radius for zero functional gain;
  rejected. The spec explicitly blesses "fit the existing serialization."
- *Bracketed IPv6 storage / URL-style target*: deferred — see D4.

## D2. Type-gated model helpers (`ssh_port`, `ssh_identity`)

**Decision**: Add two read-only properties to `KnownHost`, meaningful only for
`type=="ssh"`:
- `ssh_port -> int` → `int(instance_id)` when set & type is `ssh`, else
  `DEFAULT_SSH_PORT` (22).
- `ssh_identity -> str | None` → `region or None` when type is `ssh`, else
  `None`.

**Rationale**: Localizes the D1 field-overloading to one place instead of
scattering `instance_id`/`region` reinterpretation across the connection layer.
Non-`ssh` types return the neutral defaults, so nothing else changes.

**Alternatives**: raw `instance_id`/`region` access at every call site (leaky,
error-prone); real new dataclass fields (see D1 rejection).

## D3. Wiring port + identity into the connection path

**Decision**: Extend `core/ssh.py::build_ssh_opts()` — the single shared
SSH-argv builder used by both `shell_connect` (CLI) and the web terminal
service — to, **only when `host.type == "ssh"`**:
- append `-o Port=<host.ssh_port>` when the port differs from 22, and
- use `host.ssh_identity` as the identity when the caller passed no explicit
  `identity_file` (explicit param keeps precedence — web-adopt R6).

**Rationale**:
- `build_ssh_opts` is already the one place that turns a `KnownHost` into SSH
  argv (SSM ProxyCommand, direct target, timezone, ControlMaster, and the
  existing `identity_file`/`known_hosts_file` params). Adding port/identity here
  means `remo shell`, `remo cp`, **and** the `--verify` check (which also builds
  via `build_ssh_opts`) all honor them with no per-call-site duplication.
- The `type=="ssh"` gate is essential: proxmox stores a **numeric vmid** in
  `instance_id` and incus stores the host user there — reading `instance_id` as a
  port unconditionally would corrupt their argv. Gating on the new type keeps
  every existing provider's argv byte-identical (verified against `build_ssh_opts`
  branches).
- `-o Port=` (not `-p`) matches the file's `-o`-style option convention and
  composes cleanly with the existing option list.

**Alternatives**: a separate ssh-only builder (duplicates SSM/timezone/control
logic); passing port/identity as `shell_connect` kwargs threaded from the CLI
(misses `remo cp` and `--verify`, and re-duplicates the read).

## D4. IPv6 literal handling (fail-closed parse)

**Decision**: Reject un-bracketed IPv6 literals at parse time with guidance to
use a hostname or an `~/.ssh/config` alias; the bracketed `[::1]:22` form is
**out of scope** for this feature (documented as a possible future enhancement).

Parsing `[user@]host[:port]`:
1. Split one optional leading `user@` (first `@`).
2. On the remainder: a leading `[` (bracketed) → reject (unsupported this
   release); `>1` colon → IPv6 literal → reject with guidance; exactly `1`
   colon → `host:port` (port must be a valid int, FR-013/`validate_port`);
   `0` colons → host only, default port.
3. `--user`/`--port` options override the parsed values (FR-002).

**Rationale**: Matches the clarified decision and FR-013's "reject rather than
persist a broken line." Detecting IPv6 by colon-count on the post-`user@`
remainder is unambiguous because a legal `host:port` has exactly one colon.

**Alternatives**: full bracketed-IPv6 support (more parser + encoding surface
now; deferred by clarification).

## D5. Identity path validation

**Decision**: Reject an `--identity` value containing a colon (it would corrupt
the colon-delimited `region` slot) with a clear message. Do **not** require the
key file to exist at add time (the path may be valid only at connect time / on
another machine profile); existence is SSH's concern at connect.

**Rationale**: Upholds FR-013 (no broken registry line) with the minimum
constraint. Tilde/relative paths pass through unchanged and are expanded by SSH.

## D6. Name-collision policy (FR-007 / FR-010)

**Decision**: In `providers/added.add()`, scan the **entire** registry
(`get_known_hosts()`, no type filter) for an entry whose `name` equals the
requested name:
- match is a **provider-managed** type (`incus`/`proxmox`/`aws`/`hetzner`) →
  **refuse**, message naming the conflicting entry (FR-010/SC-005).
- match is an existing **`ssh`** entry → **in-place update** path (FR-007): prompt
  to confirm the change unless `--yes`, then `save_known_host` (which replaces the
  `(ssh, name)` line — no duplicate, SC-003).
- no match → create.

**Rationale**: `save_known_host` only dedupes within `(type, name)`, so an
`ssh` add with a name already held by a *different* type would silently create a
second line that `resolve_remo_host_by_name` could then shadow. The explicit
whole-registry pre-check is required for FR-010 and keeps names globally unique.

## D7. Unmanaged shell degradation (FR-011)

**Decision**: In `cli/shell.py`, gate the pre-connect tools/version check with
`host.type != "ssh"` (i.e., skip it for added hosts). The connection itself is
unchanged.

**Rationale**: For an added host, `check_remote_version` returning "no marker"
currently triggers a `confirm("...has no version info. Update tools?")` whose
"yes" calls `_run_provider_update`, which is a no-op for an unknown type — a
confusing prompt offering an action that does nothing. Treating `ssh` as
"unknown/unmanaged" and skipping the check lands the user straight in a plain
login shell (FR-011/SC-006). `remo-host`-dependent server-side features simply
aren't invoked by a plain `remo shell` (no `-p`), so a plain login shell is the
natural result.

## D8. `remo remove` semantics (FR-008 / FR-009)

**Decision**: New top-level `remo remove <name>` → resolve the entry by name;
if its type is not `ssh`, **refuse** with a message pointing at the provider's
`destroy` (FR-009); otherwise confirm (unless `--yes`) and call
`remove_known_host("ssh", name)`. **No** network/SSH call is made (SC-004).

**Rationale**: Deregistration is local-only by definition (`remo` owns no
infrastructure for an added host). Refusing non-`ssh` names prevents mistaking
"deregister" for "destroy my provider instance."

## D9. Provider lifecycle ops against an added host (FR-012)

**Decision**: Provider command groups already resolve within their own type
(`remo incus …` operates on incus inventory), so an added host is not in scope
for them. Where a provider path resolves purely by name, add a guard that the
resolved `host.type` matches the provider and otherwise emits the FR-012 message
("manually-registered SSH host with no managed infrastructure"). Audited and
covered as a dedicated task rather than a broad refactor.

**Rationale**: Keeps the fix at the right altitude — a targeted, clear-message
guard, not special-casing sprinkled through every provider.

## D10. Default SSH user

**Decision**: Default the SSH user to `remo` (via a new
`DEFAULT_ADDED_HOST_USER` constant in `core/config.py`) when neither the target's
`user@` nor `--user` supplies one; report the effective user back at add time so
the user can re-add with an override (FR-003).

**Rationale**: `remo` is the user convention across managed providers
(incus/proxmox/aws/hetzner all register `user="remo"`); matching it keeps the
mental model consistent, and the spec's Assumptions already name `remo` as the
natural candidate. Centralizing it as a constant (like the 013 markers) keeps a
single definition site.
