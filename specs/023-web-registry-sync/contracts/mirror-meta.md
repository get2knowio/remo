# Contract: mirror-meta.json (023 additive shape)

`web-identity/mirror-meta.json`, owned by `web/mirror_meta.py`
(`read_mirror_meta` / `record_change`) — the ONE writer shared by the setup
API (pushes) and the registry-admin API (console changes).

```json
{
  "generation": 9,
  "last_push":   { "at": "…", "workstation": "…" },
  "last_change": { "at": "…", "origin": "push" | "web", "workstation": "…" | null }
}
```

- `generation` bumps on **every** registry mutation (push or web).
- `last_push` is written only by push-origin changes and preserved verbatim
  otherwise — pre-023 `/setup/status` consumers see identical data.
- `last_change` is stamped on every mutation; it powers sync's informational
  "changed in the web console" line, push's flap wording, `GET /health`'s
  `registry_change` (generation + at + origin only — no names/hosts/keys),
  and the console's unsynced-changes badge (`origin === "web"`).
- Still advisory: reads degrade to absent, writes are best-effort and never
  fail the registry mutation they record. Configure jobs do NOT bump (no
  registry write).
