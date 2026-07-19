# Phase 0 Research: Ephemeral Device-Pairing Adoption

All Technical Context unknowns are resolved below. Each decision is framed as
Decision / Rationale / Alternatives so downstream tasks have a single source of
truth. The feature adds **no new runtime dependency** — everything is stdlib
(`secrets`, `time.monotonic`, a `threading.Lock`) plus the existing
FastAPI/React stack.

---

## R1 — Pairing session store & lifecycle

**Decision**: A single in-memory `PairingSessionManager` held on `app.state`
(constructed in `create_app`). It holds **at most one** live `PairingSession`
(most-recent-wins, FR-003). A `threading.Lock` guards all mutations because
FastAPI runs the sync setup/mint routes in a threadpool (concurrent workers).
The manager exposes:

- `mint(identity, origin) -> code` — generate a fresh code, replace/evict any
  prior session (rotation), stamp `last_activity = monotonic()`, return the raw
  code once.
- `authenticate(presented_code) -> PairingSession | None` — constant-time
  compare against the live session's code; return the session (and `touch()`
  it) on match, else `None`. Expired/rotated/absent all yield `None`.
- `is_live() -> bool` — a non-expired session exists (drives dormancy).
- `end()` — drop the live session (idempotent; used by completion + beacon).

**Rationale**: One live session matches the spec's "one operator drives one
adoption at a time" assumption and makes rotation trivial (replace the slot).
In-memory-only satisfies FR-008 (restart drops sessions) with zero persistence
code. A lock is required for correctness under uvicorn's threadpool.

**Alternatives considered**: (a) A dict of concurrent sessions — rejected,
multi-operator concurrency is explicitly out of scope and it complicates
dormancy. (b) `asyncio`-native store with an async lock — rejected, the setup
routes are deliberately sync (they do blocking file I/O and the verify
round-trips), so a threading lock is the honest primitive.

## R2 — Setup dormancy: replacing the static-token gate

**Decision**: Rename/replace `require_setup_token` with `require_pairing_code`
(same router-level `Depends`). Logic: read the manager from `request.app.state`;
if `not manager.is_live()` → `raise HTTPException(404, "Not Found")`. Else read
the bearer, `authenticate()` it; on `None` → the **same** `404` (never `401`);
on success the dependency returns and the session has been touched. Remove the
`api_token` config field and every reference to it.

**Rationale**: FR-005/FR-006 — the dormant surface must be byte-identical to an
absent route whether the code is missing, wrong, expired, or rotated. Reusing
the router-level dependency keeps all four setup routes covered by construction
and preserves 011's atomic-apply bodies untouched. Touch-on-success (not
touch-on-arrival) implements the sliding idle TTL against real progress
(FR-002).

**Alternatives considered**: Returning `401` for a wrong-but-present code —
rejected, it reveals a session exists (mirrors 011 FR-021's fail-closed
posture). Middleware-level gate — rejected, the per-route dependency is already
the established pattern and keeps the health/SPA routes untouched.

## R3 — Mint endpoint topology & the always-on requirement

**Decision**: A new router `web/api/pairing.py` mounted at `/api/v1/pairing`,
**outside** the dormant setup router so it is reachable while the setup surface
is dormant:

- `POST /api/v1/pairing/mint` — forward-auth gated (R4). Rotates + creates the
  live session, returns `{ "expires_in": <seconds> }` and the **code**. The
  code is returned only in this response body with `Cache-Control: no-store`
  (FR-016); it is never embedded in the served HTML/JS bundle.
- `POST /api/v1/pairing/end` — best-effort session end for the page-hide beacon
  (R8). Unauthenticated and idempotent (it only ever *removes* capability);
  returns `204` regardless.

**Rationale**: The setup surface must be dormant at rest, but the browser needs
a live route to *create* a session — so the mint path cannot live under the
dormant `/setup` prefix. A dedicated `/pairing` router is the clean seam and
keeps the forward-auth dependency off the setup routes (FR-009: the CLI-facing
setup routes must not require forward auth).

**Alternatives considered**: Minting via `GET /setup/status` side effects —
rejected, conflates dormancy with creation and would make a probe mint a
session. A single toggle endpoint — rejected, mint and end have different auth
(mint gated, end open) and different idempotency.

## R4 — Operator-auth provider seam & forward-auth gate

**Decision**: `web/operator_auth.py` defines a small seam:

```text
class OperatorIdentity: subject: str, provider: str
class OperatorAuthProvider(Protocol): authenticate(request) -> OperatorIdentity | None
class ForwardAuthProvider:      # reads a configured trusted header
class NetworkRestrictedProvider: # returns a fixed anonymous identity (opt-in)
```

The mint route depends on the configured provider; `authenticate()` returning
`None` → mint refused (`403`, logged with request context, no session created,
FR-011). `ForwardAuthProvider` reads exactly the configured header name and
treats its presence as proof (the proxy is trusted to set/strip it, FR-014).
Selection is explicit config (R5). The seam is where a future
`OidcVerifierProvider` (JWKS + iss/aud/exp) slots in without touching pairing
(FR-010).

**Rationale**: FR-009/FR-010 — v1 forward-auth as a thin header-consumer, with
a documented seam for OIDC-in-app later. Keeping the provider a `Protocol` with
one method makes the mint route provider-agnostic and the future OIDC addition a
pure add (no changes to pairing/dormancy).

**Alternatives considered**: Baking forward-auth reading directly into the mint
route — rejected, it would force a rewrite when OIDC lands and blurs the
deferred-enhancement boundary the spec is explicit about.

## R5 — Operator-auth configuration & fail-fast

**Decision**: Two new `REMO_WEB_` settings resolved in `WebSettings`:

- `REMO_WEB_OPERATOR_AUTH` — `"forward"` | `"none"` (network-restricted).
  There is **no silent default that permits minting**: if unset, minting is
  refused with an actionable diagnostic and readiness flags "operator auth not
  configured" (fail-closed, never silently insecure).
- `REMO_WEB_FORWARD_AUTH_HEADER` — the trusted header name (e.g.
  `X-Forwarded-User`, `Remote-User`). **No baked-in default.**

Construction is fail-fast at startup (FR-009): `operator_auth="forward"` with an
empty header name raises a startup error; `operator_auth="none"` logs a loud
warning and sets a readiness "weaker posture" flag (FR-013). `remo web serve`
convenience: when binding a loopback interface it defaults
`REMO_WEB_OPERATOR_AUTH=none` for the child process (still loudly logged) so
local single-machine runs work without a proxy while the *service contract*
still requires the explicit opt-in for any non-loopback bind.

**Rationale**: Threads FR-009 (explicit header, fail-fast) and FR-013 (network-
restricted is an explicit, loud, non-silent opt-in) while keeping `remo web
serve` usable on a laptop. Fail-closed-when-unconfigured means a real deployment
that forgets to configure auth cannot mint at all rather than minting
unguarded.

**Alternatives considered**: Defaulting to network-restricted globally —
rejected, that is exactly the silent-insecurity FR-013 forbids. Defaulting the
header name to `X-Forwarded-User` — rejected by clarification (proxies differ;
a wrong assumed name trusts a header the proxy does not strip).

## R6 — Pairing-code entropy & format

**Decision**: `secrets.token_urlsafe(24)` — 24 random bytes → ~192 bits,
~32 URL-safe characters. Compared with `hmac.compare_digest` (constant time).

**Rationale**: Delivery is clipboard copy (FR-015), never hand-typed, so length
has no UX cost and we favor generous entropy against online guessing (further
bounded by dormancy + rotation + 15-min TTL). `token_urlsafe` is stdlib,
URL/clipboard-safe, and needs no alphabet bookkeeping.

**Alternatives considered**: A short human-typable numeric code (streaming-TV
style) — rejected for v1 because there is no display and no typing; a long
opaque code is strictly safer. Revisit only if QR/short-code cross-device
pairing lands (Out of Scope).

## R7 — SPA: signaling "on the adopt page" & mint trigger (FR-003 ↔ FR-016)

**Decision**: On mount of the awaiting-adoption page (and the dashboard re-sync
affordance), the SPA `POST`s `/pairing/mint` **once** — this is the "opening the
page mints + rotates" action (FR-003). The response code is stored only in a
non-rendered React `ref`, never in state that reaches the DOM. The **Copy
pairing code** button copies from that ref to the clipboard and clears the
transient string; the value is never inserted into the DOM or logged
(FR-015/FR-016). The bundle contains no code — it is fetched at runtime on page
open, which is the operator's explicit act of opening the pairing page.

**Rationale**: Reconciles FR-003 (open = mint + rotate) with FR-016 (code
fetched only on explicit action, never embedded in the served bundle): opening
the pairing page *is* the explicit mint action, the fetch is at runtime, and the
value never renders. Rotation-on-open falls out for free — remounting/reopening
re-mints and the prior code stops authenticating.

**Alternatives considered**: Mint only on Copy click (no mount mint) — rejected,
it weakens FR-003 rotation-on-open (a stale session from a prior visit could
linger until TTL). Rendering the code behind a reveal/eyeball — rejected by the
user (copy-only, never displayed).

## R8 — Best-effort page-hide invalidation

**Decision**: On `visibilitychange`→`hidden` and `pagehide`, the SPA calls
`navigator.sendBeacon('/api/v1/pairing/end')`. The server ends the live session
best-effort. The monotonic idle TTL is the authoritative backstop (FR-004): the
server never depends on the beacon for correctness.

**Rationale**: FR-004 — hidden/unloaded pages should promptly drop the surface,
but browsers do not guarantee beacon delivery, so TTL remains the source of
truth. `sendBeacon` is the standard fire-and-forget primitive that survives
unload.

**Alternatives considered**: A WebSocket heartbeat driving liveness — rejected
as over-engineered for a single short-lived handoff; the sliding TTL already
bounds abandonment.

## R9 — Monotonic TTL & clock safety

**Decision**: `PairingSession.last_activity` is a `time.monotonic()` reading;
expiry is `monotonic() - last_activity > ttl_s`. `ttl_s` from
`REMO_WEB_PAIRING_TTL_S` (default **900** = 15 min, FR-002). Tests inject a fake
monotonic callable so TTL branches are deterministic (no `sleep`).

**Rationale**: Edge Cases — a wall-clock change (NTP step, DST) must not extend
or prematurely expire a session. `monotonic()` is immune to wall-clock jumps.
Injecting the clock keeps Constitution II (test all conditional paths) cheap.

**Alternatives considered**: `datetime.now()` deltas — rejected (clock-jump
unsafe). A background reaper task — unnecessary; lazy expiry on `is_live()` /
`authenticate()` is sufficient and simpler.

## R10 — CLI changes: pairing code in, saved credentials out

**Decision**: In `cli/web.py`, `adopt`/`push` rename the credential prompt from
"API token" to "pairing code" (still accepted via option, `$REMO_API_TOKEN` for
back-compat, or a hidden prompt — FR-018) and send it as the bearer unchanged.
Remove the `--save` flag, the saved-credentials fallback in `push`, and the
"offer to save" path. `push` now resolves URL + code the same way `adopt` does
(arg/env/prompt) every time (FR-019). In `core/web_adopt.py`, delete
`SavedCredentials`, `save_credentials`, `load_saved_credentials`,
`credentials_path`'s token/url usage, and `NoSavedCredentialsError`; **retain**
an optional non-secret push cache (deployment id + per-instance fingerprints
only, no url/token) so the push skip-optimization survives, keyed by the
deployment id fetched from `/status` at push time. Map the dormant `404` from
setup calls to an actionable "reopen the adopt page (or re-sync affordance) for
a fresh code and retry" message (FR-020).

**Rationale**: FR-018/FR-019 — no new CLI concept, and nothing durable to save
because codes are ephemeral. Keeping only the non-secret fingerprint cache
preserves the 011 push UX (unchanged instances skip re-authorization) without
persisting any credential.

**Alternatives considered**: Dropping the push cache entirely — rejected, it
would re-authorize every instance on every push (a UX regression) for no
security benefit, since fingerprints are not secrets. Keeping saved URL/token —
rejected, directly violates FR-019.

## R11 — Origin allowlist interaction

**Decision**: The `/pairing/mint` and `/pairing/end` routes are browser-facing
and carry an `Origin`, so they stay **subject to** the existing Origin
allowlist middleware (state-changing `POST` from the SPA's own origin). The
011 origin-less exemption for `/api/v1/setup/*` (the CLI has no Origin) is
unchanged. No middleware edit is required — the exemption is path-scoped to
`/setup` and the pairing routes are correctly *not* exempt.

**Rationale**: Mint is only ever called by the SPA (same-origin), so it should
be held to the browser-CSRF Origin check; the CLI never calls mint. This keeps
the CSRF surface tight without touching the setup exemption the CLI relies on.

**Alternatives considered**: Exempting pairing routes too — rejected, they are
browser-only and exempting them would drop a real CSRF defense.

## R12 — Readiness / diagnostics posture surfacing (FR-013)

**Decision**: `GET /api/v1/ready` and `remo web check` report the operator-auth
posture: `forward` (with header name echoed, not its value), `network-restricted`
(flagged as the weaker mode), or `unconfigured` (minting disabled). The
"unconfigured / awaiting adoption" readiness *status* and the health/ready
*behavior* are byte-unchanged from 011 across dormant/live/post-adoption states
(SC-008) — the posture is additive diagnostic detail, not a status change.

**Rationale**: FR-013 requires the weaker posture to be surfaced in
readiness/diagnostics and at startup; SC-008 requires health/ready behavior to
stay byte-identical. Additive detail in the `checks`/diagnostics payload
satisfies both.

**Alternatives considered**: Changing the `ready` status value when
network-restricted — rejected, it would break SC-008's byte-unchanged
guarantee and the SPA's `unconfigured` gate.

## R13 — Removing `REMO_WEB_API_TOKEN` (breaking change)

**Decision**: Delete the `api_token` field from `WebSettings` and every
reference (`setup.py`, tests, docs, compose). A value set for the old env var is
simply ignored (no gate consults it). Document the removal as a breaking change
from 011 with the pairing flow as the replacement (FR-021), and rewrite the
compose example + hola docs to the forward-auth front door with the
mint-gated / setup-passthrough split (FR-022).

**Rationale**: FR-021/FR-022 — the static token is deliberately removed with no
long-lived replacement. Silently ignoring a stale env var (rather than erroring)
avoids breaking a container that still has it set in its environment while making
it inert.

**Alternatives considered**: Fail-fast if `REMO_WEB_API_TOKEN` is still set —
rejected as hostile to upgraders; a redacted "this variable is now ignored"
info log at startup is the friendlier signal and is enough.
