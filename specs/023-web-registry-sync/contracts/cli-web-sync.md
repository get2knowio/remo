# Contract: `remo web sync` (023)

```
remo web sync [URL] [--token CODE] [--via HOST] [--yes]
              [--prefer-local | --prefer-remote] [--allow-empty]
              [--dry-run] [--force]
```

URL/code resolution is identical to `remo web push` (argument → `$REMO_API_URL`
→ prompt; `--token` → `$REMO_API_TOKEN` → hidden prompt). Nothing durable is
saved.

## Merge

Entry-level three-way merge keyed by **name** (base = push-cache v4's stored
entries; local = the workstation registry; remote = `GET /setup/registry`).
Equality is the canonical sort-keys JSON of the v2 hostEntry
(`registry.canonical_entry`) — the same string `instance_fingerprint` hashes,
so cache, drift and merge agree by construction. A cross-type name collision
within either side aborts with exit 1 naming the entries. A missing base for
a name (older cache, malformed entry) degrades to a two-way compare:
identical entries adopt silently, divergent ones surface as conflicts.

Outcomes per name: push add/update, pull add/update, delete-local,
delete-remote (revokes the service key after the PUT), in-sync, both-deleted,
conflict. Field-level merging is deliberately rejected — connection-tuple
fields are mutually dependent; field granularity appears only in conflict
rendering.

## Conflicts and consent

Interactive: per-conflict field diff + `keep [l]ocal / keep [r]emote /
[s]kip`. Skip keeps the local copy locally, sends the remote copy (and its
key lines) in the PUT, and keeps the OLD base in the cache so the conflict
re-surfaces next sync. `--prefer-local`/`--prefer-remote` (mutually
exclusive) resolve all. Deletions in either direction get one consent prompt
listing both; `--yes` bypasses it.

Non-interactive with unresolved conflicts (no `--prefer-*`), or deletions
without `--yes` → **exit 3, nothing applied anywhere**.

## Flow properties

- The v3 capability gate (`3 in payload_versions`) runs before any mutation;
  an older service aborts cleanly naming the upgrade or one-way push.
- The local apply is one CAS-guarded `mutate_registry` write (a whole-registry
  canonical-set baseline); a concurrent local change aborts with exit 1
  before any PUT.
- Pulled entries are never keyscanned/authorized by the workstation
  (outcome `pulled`); their service-held key lines are round-tripped into the
  PUT so the wholesale trust-file write stays complete. Pulled keys are
  offered — never silently written — to `~/.ssh/known_hosts`; a pulled
  `identity_file` is kept verbatim with a warning when it doesn't resolve.
- `409 generation_conflict` → re-GET, re-merge, retry ≤ 3, then exit 1 naming
  the last change. The re-merge base is advanced by the attempt's own local
  apply: values this run pulled (and base-less in-sync adoptions) are already
  synchronized, so a deployment-side deletion landing between attempts is a
  consented `DELETE_LOCAL` — never a `PUSH_ADD` resurrection. Conflict
  resolutions are memoized by (name, local content, remote content): an
  identical conflict never re-prompts, one whose content changed between
  attempts does.
- Pulled (and newly base-less-adopted) entries are persisted to the push
  cache immediately after the local apply, best-effort, without advancing the
  cached generation — a run that dies before its PUT still leaves the next
  run a correct merge base. A successful PUT's step-14 write-back supersedes
  this.
- Cache v4 write-back after a successful PUT; verify + auth-failure self-heal
  + `POST /setup/end` reuse the push machinery.

## Exit codes

`0` merged and applied (or `--dry-run`, which is GETs-only); `1` hard
failure; `3` user-aborted / unresolved. Never 2.

## `remo web status` stays offline-only (decision)

A `--remote` mode would require minting a pairing code in a browser where the
console already displays the service state; `remo web sync --dry-run` is the
authoritative two-sided view.
