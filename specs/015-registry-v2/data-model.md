# Data Model: Versioned Structured Host Registry (Registry v2)

Companion to [research.md](research.md); the normative on-disk shape lives in [contracts/registry-file-v2.md](contracts/registry-file-v2.md).

## 1. RegistryDocument

The top-level content of `${REMO_HOME}/registry.json`.

| Field | Type | Rules |
|-------|------|-------|
| `version` | int | Required. `2` for this feature. Values > supported range → `RegistryNewerVersionError` on read (FR-023). |
| `hosts` | list[HostEntry] | Required (may be empty). Serialized sorted by `(type, name)` for deterministic diffs. |

## 2. HostEntry (on-disk)

Common fields — present on every entry, identical meaning for every type (FR-002):

| Field | Type | Rules |
|-------|------|-------|
| `type` | str | Required, non-empty. Known types: `incus`, `proxmox`, `aws`, `hetzner`, `ssh`. Unknown types are preserved on rewrite, skipped in listings (FR-014). |
| `name` | str | Required, non-empty. Unique within the registry (uniqueness key: `(type, name)`). Incus/proxmox names keep the existing `host/container` and `node/container` display convention — that is a *naming* convention, not field overloading. |
| `host` | str | Required, non-empty. Hostname, IPv4, or IPv6 literal — any legitimate value round-trips (FR-005). |
| `user` | str | Required, non-empty. SSH login user on the target. |
| `access` | str enum | Required: `"direct"` \| `"ssm"` (FR-004). `"ssm"` is only valid for `type: aws` (validation rule V6). |
| `<type>` | object | Optional nested per-type object; key MUST equal `type` (rule V4). Absent when the type has no extra fields for that entry. |

Per-type nested objects (FR-003):

| Type | Nested field | Type | Meaning (legacy slot it replaces) |
|------|--------------|------|-----------------------------------|
| `incus` | `host_user` | str | SSH user on the Incus host machine (legacy `instance_id`) |
| `proxmox` | `vmid` | str | Proxmox container VMID (legacy `instance_id`) |
| `proxmox` | `node_user` | str | SSH user on the Proxmox node (legacy `region`) |
| `aws` | `instance_id` | str | EC2 instance id (legacy `instance_id`) |
| `aws` | `region` | str | AWS region (legacy `region`) |
| `hetzner` | — | — | No type-specific fields today; nested object absent |
| `ssh` | `port` | int | SSH port (legacy `instance_id`; stored as an int in v2) |
| `ssh` | `identity_file` | str | Identity file path (legacy `region`) |

All nested fields are optional-per-entry unless the provider always writes them; validation enforces *shape* (right names, right types, right parent) rather than presence, matching today's tolerance for partially-filled entries.

## 3. KnownHost mapping (in-memory model — unchanged, FR-015)

`models/host.py:KnownHost` keeps its seven string slots. The accessor maps at the serialization boundary only:

| KnownHost slot | v2 source/target by type |
|----------------|--------------------------|
| `type`, `name`, `host`, `user` | Common fields, verbatim |
| `instance_id` | `incus.host_user` / `proxmox.vmid` / `aws.instance_id` / `str(ssh.port)` / `""` |
| `access_mode` | `access` — normalized: in-memory value is always `"direct"` or `"ssm"` after a v2 load (the legacy implicit-empty convention no longer leaks upward) |
| `region` | `aws.region` / `proxmox.node_user` / `ssh.identity_file` / `""` |

`KnownHost.from_line`/`to_line` survive strictly as the **legacy codec**, used for: legacy-file parsing, migration input, and payload-v1 compatibility on the service side. No new call sites may use them for the canonical store.

## 4. Migration mapping (legacy line → HostEntry)

Keyed on `type` FIRST (research R5 — `instance_id` is meaningless without it):

1. Fields 1–4 → `type`, `name`, `host`, `user` (verbatim).
2. `access` := `"ssm"` iff the legacy entry is classified SSM by the existing semantics (AWS with instance id and empty-or-`ssm` access mode); else `"direct"`.
3. Overloaded slots → nested object per the §2 table; empty legacy slots produce absent nested fields.
4. Unparseable lines (< 4 fields, empty required fields): NOT migrated — retained in the renamed backup and reported verbatim in the migration notice (FR-009).
5. Lines whose `type` is unknown: migrated as an unknown-type entry `{type, name, host, user, access: "direct", "_legacy_fields": [f5, f6, f7]}` so nothing is dropped (FR-014); listed as skipped in output.

## 5. Validation rules (write path, FR-016)

| # | Rule | Failure message names |
|---|------|----------------------|
| V1 | `version` == 2 on serialize | — (internal invariant) |
| V2 | `type`, `name`, `host`, `user` non-empty strings; no control characters or newlines | field + entry name |
| V3 | `(type, name)` unique across `hosts` | duplicate name + type |
| V4 | Nested object key equals `type`; no unrecognized nested keys for known types | field + entry |
| V5 | `ssh.port` integer in 1–65535 | port value |
| V6 | `access == "ssm"` only when `type == "aws"` | entry + explanation |
| V7 | Values are representable in JSON (always true for str/int) — no escaping caveats remain; colon-content checks are deleted, not relaxed | — |

Validation failure → `RegistryValidationError` before any disk write; file unchanged.

## 6. File-state model & transitions

States of `${REMO_HOME}` (drives FR-007/010/011/024 and web/state.py probes):

| State | `registry.json` | `known_hosts` | CLI read behavior | Web read behavior |
|-------|-----------------|---------------|-------------------|-------------------|
| S0 empty | absent | absent | empty registry, no writes, no backup | empty registry |
| S1 legacy | absent | present | parse legacy → **migrate** (→ S2) | parse legacy in place, log format |
| S2 migrated | present | absent (backup present) | parse v2 | parse v2 |
| S3 both (equivalent) | present | present, host-set equal¹ | complete rename silently (→ S2) | parse v2, log that legacy is ignored |
| S4 both (divergent) | present | present, differing | parse v2 + warning (never merge) | parse v2, log that legacy is ignored |
| S5 newer | present, version > 2 | any | `RegistryNewerVersionError` | maps to `broken` config state |

¹ *Equivalence* (S3 vs S4): the legacy entries after legacy→v2 mapping form the same set as the v2 file's entries, compared on all v2 fields (`type`, `name`, `host`, `user`, `access`, full per-type object); unparseable lines and warnings are excluded from the comparison (research R6.4).

Transitions: S1→S2 (CLI migration, under lock, atomic); S3→S2 (rename completion); S4 persists until the user resolves it manually. The web service never causes a transition except via the setup PUT apply (writes `registry.json`, removes legacy mirror file — service-owned state only, research R9).

## 7. PushCache v2 (`~/.config/remo/web-service.json`)

| Field | Type | Rules |
|-------|------|-------|
| `cache_version` | int | `2`. Any other value (or absence) → cache treated as empty (research R10), producing the one-time full re-verification push (FR-026). |
| `push_cache` | dict | `{deployment_id: {host_name: {fingerprint, host_keys}}}` — structure unchanged from today. |
| `fingerprint` | str | SHA-256 over the canonical sorted-key JSON serialization of the entry's v2 object (replaces the legacy 7-field concatenation). |

## 8. Error taxonomy (accessor — no `SystemExit`, FR-013)

| Exception | Raised when | Typical CLI handling |
|-----------|-------------|----------------------|
| `RegistryValidationError` | V1–V6 fail on write | print message, exit 1 at CLI boundary |
| `RegistryBusyError` | lock not acquired within 5 s | print "registry busy", exit 1 |
| `RegistryNewerVersionError` | file version > supported | print upgrade guidance, exit 1 |
| `RegistryReadError` | file unreadable / top-level JSON invalid | CLI: message + exit 1; web: `broken` state |

Per-entry problems on read are **warnings on the returned view, never exceptions** (tolerant read, FR-014).
