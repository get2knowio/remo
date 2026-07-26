# Phase 1 Data Model: Simplify Web Adoption & Close the Lifecycle

Entities are lightweight dataclasses (workstation side) and JSON documents (persisted state). No `registry.json` schema change. All persisted files remain non-secret except where noted.

## 1. PushCacheEntry (extended) — `core/web_adopt.py`

The per-instance delta-cache entry, extended for revocation reachability (R2/R7).

| Field | Type | Notes |
|-------|------|-------|
| `fingerprint` | str | SHA256 over canonical v2 hostEntry (unchanged). |
| `host_keys` | list[str] | Verified known_hosts lines (unchanged). |
| `host` | str | **New.** Connection host, for reconstructing a revocation SSH target. |
| `user` | str | **New.** Connection user. |
| `access` | str | **New.** `direct` / `ssm` — SSM never gets revocation attempts. |
| `type` | str | **New.** Provider type, for the SSH command builder / summary label. |
| `port` | int \| null | **New (optional).** Non-default SSH port when applicable. |

**Validation**: parsed leniently (like today's `_parse_instances`); a missing new field disables revocation for that instance (reported as could-not-be-performed), never an error.

## 2. PushCache (file) — `~/.config/remo/web-service.json`

```jsonc
{
  "cache_version": 3,                    // bumped 2 -> 3
  "push_cache": {
    "<deployment_id>": {
      "mirror_generation": 7,            // New: last generation this workstation wrote/observed
      "instances": {                     // (shape retains name -> PushCacheEntry)
        "<name>": { "fingerprint": "...", "host_keys": ["..."], "host": "...", "user": "...", "access": "direct", "type": "incus", "port": null }
      }
    }
  }
}
```

- Non-secret: no URL, no pairing code (unchanged invariant).
- `cache_version != 3` → treated as empty (forces one full re-verification push; existing behavior).
- Written 0600, atomically (temp-file + `os.replace`).

> Note: the current v2 file maps `<deployment_id> -> {name -> entry}` directly. v3 nests under `instances` and adds `mirror_generation` as a sibling. The v2→v3 read path treats the old shape as an empty (unversioned-mismatch) cache, so no in-place migration code is needed.

## 3. MirrorMeta (file, service side) — `<REMO_HOME>/web-identity/mirror-meta.json`

> Terminology: the spec's **"mirror-identity marker"** is this concept. Its persisted form is the `mirror-meta.json` file below; the value the workstation actually compares for flap detection is the `generation` field (surfaced on the wire as `mirror_generation`). "Mirror-identity marker" (spec) = MirrorMeta file (this section) = `mirror_generation` field (contracts) — three names, one concept at different layers.

Written only by `PUT /setup/registry` apply; read by `GET /setup/status`.

| Field | Type | Notes |
|-------|------|-------|
| `generation` | int | Monotonic; incremented on each successful mirror apply. Starts at 1. |
| `last_push.at` | str (ISO-8601 UTC) | Best-effort timestamp of the last apply. |
| `last_push.workstation` | str | Best-effort label (hostname + user) reported by the pushing CLI; informational only. |

- Lives on the writable state volume; atomic write (temp-file + `os.replace`).
- Absent file (pre-017 service, or never pushed) → status omits the marker → workstation shows no flap warning (safe default).
- Contains no secret and no instance content (FR-027).

## 4. DriftReport / InstanceDrift — `core/web_drift.py`

Pure offline comparison of the local registry against a deployment's cached instances.

| InstanceDrift field | Type | Notes |
|---------------------|------|-------|
| `name` | str | Registry entry name / cached name. |
| `state` | enum `new` \| `changed` \| `removed` \| `in_sync` | `changed` when fingerprints differ. |
| `type` | str | For the rendered table. |

`DriftReport`: `deployment_id: str`, `entries: list[InstanceDrift]`, plus convenience counts (`new`, `changed`, `removed`, `in_sync`) and `is_in_sync` (all `in_sync`).

## 5. RevocationOutcome — `core/web_adopt.py`

Per removed instance, surfaced in the push summary.

| Field | Type | Notes |
|-------|------|-------|
| `name` | str | Removed instance name. |
| `result` | enum `revoked` \| `could_not_revoke` | |
| `detail` | str | Reason when `could_not_revoke` (unreachable / SSM / no connection tuple / remote error). |
| `remediation` | str | Manual-removal guidance when `could_not_revoke`. |

## 6. ConfigurationState (unchanged enum, changed derivation) — `web/state.py`

Values unchanged (`unconfigured` / `adopted` / `mount_configured` / `broken`). Derivation changes (R5):

| Condition (in precedence order) | Resulting state |
|---------------------------------|-----------------|
| Any required artifact unreadable, or half-pair keypair, or registry-on-writable-volume with nothing to authenticate | `broken` |
| `REMO_WEB_MODE` set to a valid value (and not overridden by a `broken` guard) | that value |
| Registry present AND `REMO_HOME` not writable | `mount_configured` |
| Registry present AND `REMO_HOME` writable AND service keypair | `adopted` |
| No registry AND `REMO_HOME` writable | `unconfigured` |
| No registry AND `REMO_HOME` not writable | `broken` |

**Removed trigger**: the previous "`_user_identity_present()` → `mount_configured`" rule is deleted; a readable personal `~/.ssh/id_*` no longer influences the mode.

## 7. WebSettings (extended) — `web/config.py`

| New field | Env var | Notes |
|-----------|---------|-------|
| `mode_override` | `REMO_WEB_MODE` | `adopted` / `mount_configured` / unset. Invalid value → fail-fast config error (Constitution IV). |
| `mirror_meta_path` | (derived) | `web_identity_dir / "mirror-meta.json"`. |

## Relationships

- One **PushCache** (per workstation) → many deployment entries → each has one `mirror_generation` + many **PushCacheEntry**.
- One **MirrorMeta** per deployment (service side) is the source of truth the workstation's `mirror_generation` is compared against for flap detection.
- **DriftReport** is derived, never persisted (offline diff of registry vs. PushCache).
- **RevocationOutcome** and the adoption **InstanceOutcome** (existing) are both rendered in the single push summary.
