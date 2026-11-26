# Contract: registry entry for the local host

Registry format stays **v2**. One new pseudo-type, `local`, alongside the
existing `ssh` pseudo-type. Neither is a registered provider.

```json
{
  "type": "local",
  "name": "local",
  "host": "localhost",
  "user": "paul",
  "access": "direct",
  "local": { "projects_dir": "/Users/paul/projects" }
}
```

- The nested `local` object is written only when `projects_dir` is set.
- `KnownHost` mapping: `type="local"`, `region=projects_dir` (exposed as
  `KnownHost.projects_dir`), `instance_id=""`, `access_mode="direct"`.
- Parse rejects (tolerant-read warning, entry skipped) an entry whose
  `access` is not `direct` or whose `projects_dir` is not absolute.
- `is_known_type("local") is True`; `is_provider_type("local") is False`.
- `canonicalize_entry` leaves it untouched.
- At most one `local` entry is created by remo; if a hand-edited file holds
  more, the first wins and the rest are warned about and preserved.

## Compatibility

- An older remo (pre-029) reads `"type": "local"` as an unknown type and
  preserves it verbatim (spec 027 FR-009).
- `remo web push`/`remo web sync` filter it out in both directions (FR-004a).

## Tests

Round-trip (write → read → write is byte-stable), with and without
`projects_dir`; legacy-reader preservation; rejection cases; the web filters.
