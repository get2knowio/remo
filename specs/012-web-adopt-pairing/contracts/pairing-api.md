# Contract: Pairing API (`/api/v1/pairing/*`)

**New in 012.** These routes live **outside** the dormant `/api/v1/setup/*`
router so they are reachable while the setup surface is dormant. They are the
browser-facing control plane for the pairing lifecycle. Both are `POST` from the
SPA's own origin and are therefore subject to the existing Origin allowlist
(R11) — the CLI never calls them.

---

## `POST /api/v1/pairing/mint`

Mint a fresh pairing code, rotating (invalidating) any prior live session
(FR-003). **Forward-auth gated** (FR-009): the operator-auth provider must
authenticate the request.

**Request**: no body required. In the forward-auth posture the deployment's
proxy MUST have injected the trusted identity header (name per
`REMO_WEB_FORWARD_AUTH_HEADER`); the client never sets it.

**Responses**:

| Status | Body | When |
|---|---|---|
| `200` | `{ "expires_in": <int seconds> }` **+ code** — see below | Authenticated; a fresh session was minted (prior invalidated). |
| `403` | `{"detail": "operator authentication required"}` | Forward auth required and the trusted header is absent/empty (FR-011). No session created. |
| `403` | `{"detail": "operator authentication not configured"}` | No provider configured (minting disabled, R5). No session created. |

The **code** is returned in the 200 body (field `code`) with `Cache-Control:
no-store`. It is the only place the code is ever transmitted from the server; it
is never embedded in the served HTML/JS bundle (FR-016). The SPA holds it in a
non-rendered ref and copies it to the clipboard on the explicit Copy action
(FR-015); it never enters the DOM.

**Logging**: the authenticated `subject` and `origin` are logged; the `code` is
never logged (FR-012/FR-016). A refusal (403) is logged with request context.

## `POST /api/v1/pairing/end`

Best-effort end of the live pairing session for the page-hide beacon
(FR-004). Called via `navigator.sendBeacon` on `visibilitychange`→`hidden` /
`pagehide`. **Unauthenticated and idempotent** — it only ever *removes*
capability.

**Request**: no body (beacon).

**Responses**:

| Status | Body | When |
|---|---|---|
| `204` | _(empty)_ | Always — whether or not a session was live. |

The idle TTL remains the authoritative backstop; the service never depends on
this call for correctness (FR-004).

---

## Operator-auth configuration (mint gating)

| Env | Values | Effect |
|---|---|---|
| `REMO_WEB_OPERATOR_AUTH` | `forward` | Mint requires the trusted header. |
| | `none` | Network-restricted: mint proceeds without a credential; **loudly logged** at startup + flagged weaker in readiness (FR-013). |
| | _(unset)_ | Minting disabled; mint returns `403 not configured`. `remo web serve` sets `none` for loopback binds (still logged). |
| `REMO_WEB_FORWARD_AUTH_HEADER` | header name | **Required** when `OPERATOR_AUTH=forward`. No default. Enabling forward auth without it is a **startup fail-fast** (FR-009). |
| `REMO_WEB_PAIRING_TTL_S` | seconds | Sliding idle TTL, default `900` (15 min, FR-002). |

**Trust boundary (FR-014)**: `ForwardAuthProvider` trusts the identity header
only because the deployment guarantees the proxy sets/strips it and prevents
direct client access to the app. This is the standard forward-auth boundary and
MUST be documented (including the hola-app configuration). The proxy MUST gate
the **mint** path while passing `/api/v1/setup/*` through (FR-009).
