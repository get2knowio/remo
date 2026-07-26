# Contract: Setup-API mirror-identity marker (additive)

Additive, backward-compatible changes to the already pairing-gated setup surface (`web/api/setup.py`). A pre-017 workstation ignores the new fields; a 017 workstation talking to a pre-017 service sees them absent and simply shows no flap warning (safe default). Supports FR-022..FR-027.

## GET /api/v1/setup/status — response additions

Existing fields unchanged (`state`, `deployment_id`, `public_key_available`, `registry_instances`, `payload_versions`). Add:

```jsonc
{
  "mirror_generation": 7,          // int >= 1, or field omitted if no mirror has ever been applied
  "last_push": {                    // omitted when mirror_generation is absent
    "at": "2026-07-26T12:00:00Z",  // ISO-8601 UTC, best-effort
    "workstation": "hostA/paul"     // best-effort label; informational only; never authoritative
  }
}
```

- Served only over the pairing-gated router (no anonymous exposure).
- Contains no secret and no instance contents (FR-027).
- Sourced from `<REMO_HOME>/web-identity/mirror-meta.json`; a missing/unreadable file → both fields omitted.

## PUT /api/v1/setup/registry — behavior + response additions

Existing validation and ordered apply unchanged (validate-all-before-write; service known_hosts → registry.json → drop legacy mirror). Add to `_apply_payload`, as the final atomic step:

1. Read the current `generation` (default 0 if the file is absent/unreadable).
2. Write `mirror-meta.json` with `generation + 1`, `last_push.at` = now (UTC), `last_push.workstation` = the label the client sent (see request addition), via temp-file + `os.replace`.

**Request addition (optional)**: the client MAY include `"workstation": "<label>"` at the payload top level. Absent → the service records an empty/`"unknown"` label. The label is untrusted display text; the service stores it verbatim but never acts on it.

**Response additions**:

```jsonc
{
  "applied": true,
  "registry_instances": 3,
  "host_key_instances": 2,
  "mirror_generation": 8            // New: the generation just written; the CLI records this in its push cache
}
```

## Failure & precedence

- `mount_configured` deployments still reject `PUT` with 409 before any mirror-meta write (unchanged) — flap markers never apply to operator-provided mirrors (spec Edge Cases).
- A mirror-meta write failure after a successful registry apply is logged but does NOT fail the request (the marker is advisory; the mirror is already correct). The next successful push converges the generation.
- Unsupported `version` → 400 with the prior mirror AND prior mirror-meta left intact (unchanged fail-fast).

## Workstation-side flap logic (FR-024..FR-026)

Given `server_gen = status.mirror_generation` and `cached_gen = push_cache[deployment].mirror_generation`:

| Condition | Behavior |
|-----------|----------|
| `server_gen` absent (pre-017 service) OR no cache entry (first push) | No warning (FR-025). |
| `server_gen <= cached_gen` (this workstation is up to date) | No warning (FR-025 consecutive-same-workstation case). |
| `server_gen > cached_gen` (mirror advanced elsewhere) | Warn, naming `last_push.at`/`.workstation`; interactive → confirm/abort; `--yes` → proceed (FR-026 / Clarifications Q2). |

After a successful PUT, store `response.mirror_generation` as the new `cached_gen`.
