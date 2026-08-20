# Contract: `GET /api/v1/setup/registry` (023)

The sync source: the service's registry as a workstation-consumable document.
Pairing-gated and dormant-404 like every `/setup/*` route.

## Capability signal

Never probe this route. A service advertising payload version `3` in
`GET /setup/status`'s `payload_versions` implements it; an older service's 404
is indistinguishable from a dormant surface.

## Response (200)

```json
{
  "entry_version": 2,
  "registry": [ { "type": "ssh", "name": "mbp", "host": "10.0.0.9", "user": "paul", "access": "direct", "ssh": {"port": 2222} } ],
  "host_keys": { "mbp": ["[10.0.0.9]:2222 ssh-ed25519 AAAA…"] },
  "mirror_generation": 7,
  "last_change": { "at": "…", "origin": "push" | "web", "workstation": "…" | null }
}
```

- `registry`: known-type entries in the registry-file-v2.md hostEntry shape
  (`core.registry.known_host_to_entry`). **Unknown-type raw entries are
  omitted** — they are opaque to sync; each side preserves its own verbatim.
- `host_keys`: the flat service trust file (`web-identity/known_hosts`)
  regrouped by registered instance **name**. Each line's hosts field is
  matched against the entry's OpenSSH lookup key (bare host for port 22,
  `[host]:port` otherwise). Sound because every stored line is
  `ssh-keyscan`-sourced (plain, never hashed). Unmatched lines and SSM
  entries' keys are omitted.
- `mirror_generation`: `0` when no marker exists yet. This is the value a
  sync must echo back as PUT v3's `base_generation`.
- `last_change`: omitted for pre-023 markers (exclude_none).

## Errors

- Mount-configured deployment → `409 {"reason": "mount_configured"}`.
- Empty registry → `200` with `registry: []` (not an error).
