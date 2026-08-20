# Contract: push cache v4 (023)

`~/.config/remo/web-service.json` bumps `cache_version` 3 → 4. The v3 shape
is unchanged; each instance entry additionally stores the full v2 hostEntry:

```json
{ "cache_version": 4, "push_cache": { "<deployment_id>": {
    "mirror_generation": 7,
    "instances": { "<name>": {
        "fingerprint": "<sha256>", "host_keys": ["…"],
        "host": "…", "user": "…", "access": "…", "type": "…",
        "port": null, "identity": "",
        "entry": { "…full hostEntry v2…" } } } } } }
```

- `entry` is `remo web sync`'s merge **base**. Malformed → `None` → that name
  merges base-less (safe degradation), never an error.
- Any non-v4 file loads as empty (lenient posture, unchanged): an old CLI
  reading a v4 file re-verifies fully; a new CLI reading a v3 file does a
  base-less first sync. One-time, safe, both directions.
- 023 refinement (applies to push too): a direct-access instance that ended
  `skipped_unreachable`/`skipped_no_trust` IS cached — its entry was mirrored
  by the PUT, so it is a correct merge base — but with **empty** `host_keys`,
  which the push fast-path gates on, so it is still retried in full.
- Invariant: no URL and no pairing code are ever persisted.
