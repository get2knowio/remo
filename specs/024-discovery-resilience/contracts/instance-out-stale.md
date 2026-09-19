# Contract: Instance staleness advisory fields (024)

The FastAPI app is the source of truth (Constitution IV); this file describes
the intended shape — the generated `openapi.json`/`schema.d.ts` are the
authoritative artifacts and are regenerated, never hand-edited.

## `InstanceOut` (REST, `GET /api/v1/hosts`) — additive fields

```jsonc
{
  "instance_id": "hetzner:dev1",
  "instance_type": "hetzner",
  "instance_name": "dev1",
  "status": "ok",                  // UNCHANGED enum; stays "ok" during grace
  "region": "fsn1",
  "capability": { /* unchanged */ },
  "error": {                       // during grace: the LAST probe failure
    "code": "timeout",
    "message": "...",
    "retryable": true,
    "remediation": "..."
  },
  "refreshed_at": "2026-09-19T12:00:30Z",
  // NEW — all additive with defaults (old clients unaffected):
  "stale": true,                   // default false
  "last_ok_at": "2026-09-19T11:58:00Z",  // default null
  "consecutive_failures": 2        // default 0
}
```

Invariants:

- `stale=true` ⇒ `status="ok"` (retained snapshot). Never true otherwise.
- Post-grace / non-retryable failure snapshots: `stale=false`, `last_ok_at`
  carried forward (observability), `error` set, and the host's targets are
  absent from `GET /sessions` — exactly today's error contract.
  `consecutive_failures` counts *consecutive retryable* failures, so it keeps
  incrementing on a post-grace retryable failure and resets to `0` on a
  non-retryable one (matching `data-model.md`).
- Fresh success: `stale=false`, `consecutive_failures=0`, `last_ok_at` =
  that success's timestamp.
- A stale instance's targets REMAIN in `GET /api/v1/sessions` and remain
  valid for `POST /api/v1/terminals` and WS reattach (unchanged routes).
- `InstanceStatus` enum: NO new members.

## Configuration surface

`REMO_WEB_DISCOVERY_OFFLINE_GRACE_S` (float seconds, default `120`, `0`
disables retention; negative → startup `WebConfigError`). Documented alongside
the other `REMO_WEB_*` vars in `docs/web-session-interface.md`.

## Regeneration procedure (gates must pass)

1. `uv run python scripts/export_openapi.py`
2. `cd frontend && npm run generate:types`
3. `uv run pytest tests/unit/test_schema_drift.py`
4. `cd frontend && npm run check:types-fresh`

No WebSocket frame changes (`terminal-frames.*` unchanged by content).
