# Phase 0 Research: Versioned Structured Host Registry (Registry v2)

All Technical Context unknowns are resolved below. Each entry records the decision, rationale, and alternatives considered. Line references describe the codebase as of branch point (`main` @ 5eb6848).

## R1. File format and naming

**Decision**: JSON. Canonical file `${REMO_HOME}/registry.json` — a single object `{"version": 2, "hosts": [...]}` — pretty-printed with 2-space indent, `ensure_ascii=False`, entries sorted by `(type, name)`, trailing newline.

**Rationale**:
- Stdlib `json` reads AND writes — no new runtime dependency (project convention: "No new runtime deps", see 005-provider-snapshots precedent; pyproject keeps deps minimal).
- Pretty-printed JSON with one key per line is line-diffable (SC-007): a changed host attribute is a one-line diff; sorted entry order makes diffs deterministic and prevents spurious reorder noise.
- The web setup API already exchanges registry entries as JSON (Pydantic models in `web/api/setup.py`), so file format and wire format share one representation.

**Alternatives considered**:
- **TOML**: stdlib `tomllib` is read-only (Python 3.11); writing needs `tomli-w` — a new dependency. Rejected.
- **YAML**: needs PyYAML (new dep), ambiguous scalar typing (the Norway problem) in a file where values are security-relevant hostnames. Rejected.
- **JSON Lines**: no natural place for a top-level version field; per-entry version repeats. Rejected.
- **Keep colon format with escaping**: preserves the corrupting format's core problem (positional overloading) and adds an escaping scheme no other tool understands. Rejected.

## R2. Backup naming and non-clobbering

**Decision**: At migration, the legacy file is renamed in place: `known_hosts` → `known_hosts.v1.bak` (same directory, `os.rename`). If `known_hosts.v1.bak` already exists, the new backup gets a numeric suffix (`known_hosts.v1.bak.1`, `.2`, …) — never overwrite (FR-009).

**Rationale**: Same-directory rename is atomic on POSIX and preserves the file byte-for-byte, including lines migration could not interpret. The `.v1.bak` name states what it is; numeric suffixes handle the re-migration edge case (rollback → downgrade writes a fresh legacy file → re-upgrade migrates again).

**Alternatives**: leave legacy untouched at original path (rejected in clarification — makes both-present the permanent state and neuters FR-024); move to a `backups/` subdir (more moving parts, breaks the "everything in one config dir" convention).

## R3. Advisory locking mechanism

**Decision**: `fcntl.flock(LOCK_EX | LOCK_NB)` on a dedicated sidecar file `${REMO_HOME}/registry.lock`, acquired in a retry loop (50 ms interval) with a 5-second total budget (FR-017, clarification #3), then `RegistryBusyError` with the holder-agnostic message "registry is busy — another remo process is writing; retry in a moment". Lock wraps the whole read-modify-write; plain reads do not take the lock (atomic `os.replace` writes make un-locked reads always see a complete file, FR-018).

**Rationale**:
- The lock must live on a file that is never replaced — `registry.json` itself is swapped by `os.replace` on every write, which would make a lock on it meaningless across writers. A sidecar avoids that trap.
- `flock` is available on Linux and macOS (both supported platforms; Windows is not supported today) and is advisory, matching FR-017's scope (same-machine cooperation, not enforcement).
- Read paths staying lock-free keeps readonly mode truly side-effect-free (FR-013): a readonly consumer on a read-only volume could not create/open a lock file anyway.

**Degradation (FR-019)**: if `flock` raises `OSError` (`ENOLCK`, `EOPNOTSUPP` — e.g. some network filesystems), emit a one-time warning ("registry locking unavailable on this filesystem; concurrent writes may race") and proceed unlocked — current behavior, no regression.

**Alternatives**: `os.open(O_CREAT|O_EXCL)` lockfile protocol (needs stale-lock reaping after crashes — flock releases automatically on process death); `lockf`/`fcntl` byte-range locks (same availability, more foot-guns with fd inheritance); third-party `filelock` (new dependency). Rejected.

## R4. Entry schema shape: flat common fields + per-type object

**Decision**: Each host entry has common fields (`type`, `name`, `host`, `user`, `access`) plus at most one nested object named after the type carrying type-specific fields (see data-model.md for the full field tables):

```json
{
  "type": "proxmox",
  "name": "pve1/dev1",
  "host": "pve1.lan",
  "user": "remo",
  "access": "direct",
  "proxmox": { "vmid": "104", "node_user": "root" }
}
```

**Rationale**: The nested per-type object makes field ownership self-describing (a `vmid` can only appear under `proxmox`), gives validation a natural shape ("the nested key must equal `type`; its fields must match that type's table"), and lets unknown-type entries round-trip wholesale (FR-014): the serializer re-emits the raw object untouched.

**Alternatives**: fully flat entries with type-prefixed field names (`proxmox_vmid`) — noisier, weaker validation story; a generic `extra: {}` bag — reintroduces "meaning depends on type" ambiguity the feature exists to kill. Rejected.

## R5. `access` attribute semantics and legacy derivation

**Decision**: `access` is a required enum: `"direct"` or `"ssm"` (FR-004). Migration derives it by reusing the exact existing semantics — an entry is SSM iff the current `is_direct_access`/`to_line` logic classifies it as SSM (today that is only AWS-with-instance-id-and-empty-or-ssm-access-mode) — so migration is a pure re-encoding of current behavior, never a behavior change.

**Verified during analysis (2026-07-25)**: every *current* provider save path sets `access_mode` explicitly — incus `"direct"` (providers/incus.py:285, :649), proxmox `"direct"` (proxmox.py:372, :807), added-ssh `"direct"` (added.py:277), aws `"ssm"` or tag-derived (aws.py:464, :621, :752) — so freshly written legacy lines always carry a literal access mode. The `to_line` back-fill (`access_mode="ssm"` whenever `instance_id` is set and `access_mode` is empty, models/host.py) is therefore a *latent* quirk affecting only files written by older remo versions or by hand. The migration mapper keys on `type` FIRST, and the test matrix (T015/T016, quickstart §2 `old/…` fixture lines) must include both legacy variants: a non-AWS line with literal `ssm`, and a 7-field line with an empty access-mode slot — both map to `access: "direct"`.

## R6. Migration flow, crash-safety, and both-present resolution

**Decision** (CLI only — clarification #1):

1. Read path resolution order: `registry.json` exists → parse v2 (done). Else `known_hosts` exists → parse legacy tolerantly; if invoked via the CLI (write-capable accessor default), migrate.
2. Migration, under the registry lock: re-check state (another process may have migrated during the lock wait) → write `registry.json` atomically (temp file + `os.replace`) → rename `known_hosts` → `known_hosts.v1.bak` → print notice (entries migrated, backup name, any skipped lines verbatim, and the one-time "next `remo web push` will re-verify all instances" note per FR-026).
3. **Ordering rationale**: write-new-then-rename-old means a crash at any point leaves data reachable: crash before the write → legacy still authoritative; crash between write and rename → both files present.
4. **Both-present resolution (FR-024)**: `registry.json` always wins. On CLI read with both present: if the legacy file's parsed host set is **equivalent** to the v2 host set, this is an interrupted migration — silently complete the rename. Equivalence is defined as: the set of legacy entries after legacy→v2 mapping equals the v2 file's entry set, compared on ALL v2 fields (`type`, `name`, `host`, `user`, `access`, and the full per-type object); unparseable legacy lines and read warnings are excluded from the comparison. Any inequality (rollback-then-re-upgrade divergence) → warn: which file is in use, that the legacy file is being ignored, and how to resolve (delete or re-add hosts) — never merge (edge case list).
5. The web service never migrates and never resolves both-present — it reads `registry.json` if present, else legacy, and logs which format it used (FR-011, FR-025).

**Idempotency (FR-010)**: step 1 makes completed migrations no-ops; step 2's re-check under lock makes concurrent first-runs safe; step 4 converges the interrupted case.

## R7. Newer-version file handling

**Decision**: `version` field greater than 2 → `RegistryNewerVersionError` ("this registry was written by a newer remo (format N); upgrade remo or restore the backup"), file untouched (FR-023). CLI surfaces it and exits non-zero at the CLI boundary (the accessor itself never calls `sys.exit` — FR-013). Web service maps it to the `broken` configuration-state path so readiness/`remo web check` report it with the same remediation text.

## R8. Single accessor and call-site strategy

**Decision**: New module `src/remo_cli/core/registry.py` owns everything (contract: `contracts/registry-accessor-api.md`). Existing public functions in `core/known_hosts.py` (`get_known_hosts`, `save_known_host`, `remove_known_host`, `clear_known_hosts_by_type`, `clear_known_hosts_by_prefix`, resolver/guard helpers) become thin delegates so the ~30 existing call sites in `providers/*` and `cli/*` keep working unchanged (FR-015). The three parser sites collapse:
- `core/known_hosts.py` internal parsing → delegates to `registry`
- `web/discovery.py:68-103` private parser → `registry.read_registry(readonly=True)`
- `web/api/setup.py:165-180` private parser → same
`web/state.py` and `web/check.py` registry probes accept either file (R6.5).

**Rationale**: delegation keeps this feature's diff mechanical at call sites, deferring the larger write-API rework (clear-then-resave → reconcile) to roadmap Spec 2, which is explicitly out of scope here. The one new write primitive `mutate_registry(fn)` (lock → read → apply → validate → atomic write) is introduced and used by the delegates internally so every existing write becomes lost-update-safe (FR-017) without changing provider code semantics.

## R9. Mirror payload v2 and version negotiation

**Decision** (contract: `contracts/mirror-payload-v2.md`):
- Payload v2: `{"version": 2, "registry": [<v2 entry objects>], "host_keys": {...}}` — same entry schema as the file (R4).
- `GET /api/v1/setup/status` gains `"payload_versions": [1, 2]`. The workstation reads it before pushing; a service response without the field implies `[1]` (older service) → the CLI aborts before any mutation with "the remo-web deployment only speaks registry format 1 — upgrade the container image, then re-run push" (FR-021).
- The upgraded service's `PUT /setup/registry` accepts **both** v1 and v2 payloads; v1 entries are mapped through the same legacy→v2 mapper used by file migration, and the service always stores v2 (FR-022). Unknown versions → 400 with a body naming supported versions.
- On a successful apply, the service writes `registry.json` and removes any legacy `known_hosts` mirror file in the same apply sequence (the mirror is service-owned replaceable state, not user data; ordering: service known_hosts trust file first, `registry.json` second, legacy-file removal last — a crash mid-sequence still leaves a readable superset, converging on re-push).

**Rationale**: capability advertisement via status (which the push flow already fetches first — `core/web_adopt.py:1106`) gives fail-fast skew detection with zero extra round trips; accept-old-payload on the service preserves the "upgrade service first" direction from the spec's assumptions.

## R10. Push delta-cache reset

**Decision**: `~/.config/remo/web-service.json` gets `"cache_version": 2` and fingerprints computed over the canonical v2 entry serialization (sorted-key JSON of the entry object). The loader discards any cache whose `cache_version` != 2 — same discard-on-unknown-shape behavior the file already has for the obsolete 011 format (`core/web_adopt.py:761-786`). Result: first push after upgrade sees an empty cache, treats all instances as changed, re-keyscans and re-authorizes (idempotent — the `remo-web@` marker replacement makes re-authorization a byte-level no-op), per clarification #4 and FR-026.

## R11. Performance (SC-008)

**Decision**: no special engineering needed — parsing a 200-entry JSON file is single-digit milliseconds with stdlib `json`. Add one regression test that generates a 200-entry registry and asserts read+write round-trip completes under the budget, to catch accidentally quadratic validation.

## R12. What is explicitly NOT changing (scope guards)

- Provider sync/write *semantics* (clear-then-resave patterns) — Spec 2 (sync-as-reconcile) territory; this feature only makes them lost-update-safe via the shared mutate primitive.
- `KnownHost` in-memory field layout and its overloaded-slot accessors — call sites keep working; per-type meaning now lives in the (de)serialization mapping tables (FR-015).
- The adopt/push workflow shape (verbs, keyscan, authorized_keys management) — Spec 3 territory; only the payload schema and cache format change here.
- `remo-host` protocol, SSH option building, discovery/terminal flows — untouched.
