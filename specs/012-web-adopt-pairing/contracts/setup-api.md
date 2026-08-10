# Contract: Setup API (`/api/v1/setup/*`) — 012 delta

The setup routes' **bodies** (`GET /status`, `GET /identity`, `PUT /registry`,
`POST /verify`) and their request/response shapes are **inherited unchanged from
011** (see `specs/011-web-adopt/contracts/setup-api.md`). 012 changes only the
**authentication/dormancy gate** on the router.

---

## What changes: the router gate

011's `require_setup_token` (static `REMO_WEB_API_TOKEN` bearer) is replaced by
`require_pairing_code`, backed by the in-memory `PairingSessionManager`:

| Condition | Response | FR |
|---|---|---|
| No live pairing session | `404 {"detail": "Not Found"}` — byte-identical to an unknown route | FR-005 |
| Live session, bearer matches the live code | route handles the request; session is **touched** (sliding TTL reset) | FR-002 |
| Live session, bearer absent / wrong / from an expired-or-rotated session | `404 {"detail": "Not Found"}` — **never** a distinguishable `401` | FR-006 |

Notes:
- The comparison against the live code is constant-time (`hmac.compare_digest`).
- Dormancy is **the default state**: the surface exists only while an operator
  is on the adopt page / re-sync affordance (a live session). SC-001: with no
  session live, 100% of setup requests return the dormant `404`.
- Completing the flow **ends the session** (FR-007) via an explicit
  **`POST /setup/end`** (pairing-code-gated like every other setup route, and
  inside the Origin-exempt `/api/v1/setup/*` prefix an Origin-less CLI request
  needs). The CLI calls it once its flow has succeeded; a failed or aborted flow
  deliberately leaves the session live so a retry can reuse the same code.
  `POST /verify` does **not** end the session and may be called more than once:
  the push flow's self-heal pass re-PUTs the mirror and re-verifies *after* the
  first verify, and ending there made both of those calls hit the dormant `404`,
  stranding the repair and the workstation's push cache (#158). The surface
  returns to dormant immediately after `/setup/end` (US2 scenario 3, US4
  scenario 2); a client that never calls it falls back to the idle-TTL /
  page-hide backstop.
- `POST /setup/end` -> `200 {"ended": true}`. Idempotent in effect, though a
  second call from the same client sees the dormant `404` (its code is no longer
  live) — the CLI treats that, and a `404` from a service predating the route,
  as success.
- The presented code is **never logged** (FR-016); an auth failure is logged
  with route/method context only, exactly as 011 logged its failures minus the
  credential.

## What does NOT change

- Route paths, methods, request bodies, success/response bodies, the
  mount-configured `409`, the empty-registry `422`, the atomic two-file apply,
  and the origin-less exemption for `/api/v1/setup/*` (the CLI has no `Origin`)
  are all unchanged from 011.
- Forward auth does **not** apply to these routes (FR-009): the CLI cannot
  complete an SSO challenge, so `/api/v1/setup/*` is authenticated *solely* by
  the live pairing code. The deployment's proxy passes the setup path through
  while gating only the browser-facing `/api/v1/pairing/mint`.

## Removed

- `REMO_WEB_API_TOKEN` and the `WebSettings.api_token` field are removed
  (FR-021). A value set for the old variable is ignored (inert); it no longer
  grants any setup access.
