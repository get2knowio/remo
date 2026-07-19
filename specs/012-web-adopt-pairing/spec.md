# Feature Specification: Ephemeral Device-Pairing Adoption (Operator-Auth Gated)

**Feature Branch**: `012-web-adopt-pairing`

**Created**: 2026-07-19

**Status**: Draft

**Input**: User description: "Instead of a static setup token baked in at deploy
time, the awaiting-adoption web page mints a short-lived ephemeral pairing code
(streaming-service QR sign-on model). The setup API is dormant until an operator
browses to the adopt page and a pairing session is live. Future re-syncs use the
same flow. Access to the page — and thus the ability to mint a code — is gated by
operator authentication (forward auth OR OIDC — the app platform supports both),
so the service positively knows the operator is authenticated."

## Overview

Feature 011 (CLI-to-Web Adoption) gates the `/api/v1/setup/*` surface with a
single **static** bearer token supplied at deploy time (`REMO_WEB_API_TOKEN`).
That token is long-lived, must be configured out-of-band, and — if an operator
wants to lift it into the CLI without re-typing — can only be surfaced to the
browser by exposing the very secret the API is gated by.

This feature replaces the static token with an **ephemeral device-pairing**
model, patterned on how a streaming service signs a new TV in: the
awaiting-adoption page mints a short-lived **pairing code**, the operator carries
that code to their workstation CLI (`remo web adopt`), and the code authorizes
that one adoption session. There is no durable service credential anywhere.

Two properties make this safe where a naked "unauthenticated setup API" would
not be:

1. **The setup surface is dormant (just-in-time).** `/api/v1/setup/*` returns
   `404` — indistinguishable from an absent route — unless a pairing session is
   currently live. A pairing session exists only while an operator is actively
   on the adopt page (or the dashboard re-sync affordance). The attack surface
   does not exist at rest.
2. **Minting a pairing code is gated by operator authentication.** The service
   only mints a code for a request that carries proof the operator is
   authenticated. Two interchangeable providers are supported, both offered by
   the app platform:
   - **Forward auth** — a trusted, proxy-injected identity header (Traefik
     ForwardAuth / oauth2-proxy / Authelia / a hola app's SSO). Simplest;
     trust rests on the proxy being the only ingress.
   - **OIDC** — a token minted by the identity provider and presented to the
     service, which the service **cryptographically verifies** (signature via
     the IdP's JWKS, plus issuer/audience/expiry). Strongest, because trust
     does not depend on a network boundary — a spoofed header without a
     validly-signed token is rejected. Recommended where available.
   Either way the service has positive proof the operator is logged in — not
   merely that they can reach the page.

Future re-syncs (`remo web push`) use the identical flow: the operator opens a
re-sync affordance in the dashboard, the service mints a fresh code, the CLI
consumes it. Nothing is persisted between sessions.

The static `REMO_WEB_API_TOKEN` model is removed. QR display of the pairing
code, and multiple concurrent named deployments, are out of scope (see Out of
Scope) — clipboard copy of the code is the v1 delivery mechanism.

## Clarifications

### Session 2026-07-19

- Q: Static deploy-time token, or ephemeral page-minted pairing code? → A:
  Ephemeral. No static `REMO_WEB_API_TOKEN`. Both first adoption and every later
  re-sync obtain a fresh code from the page.
- Q: How is the pairing code's lifetime bounded, given adoption can pause on an
  interactive host-key fingerprint prompt? → A: Sliding TTL. Opening the page
  mints a fresh code (invalidating the prior); each successful setup call
  refreshes an idle window; best-effort invalidation when the page is hidden;
  a live code dies by idle TTL.
- Q: Should the setup API be reachable when nobody is on the adopt page? → A:
  No. `/api/v1/setup/*` is dormant (404) unless a pairing session is live.
- Q: What positively establishes that the person minting a code is the
  operator, rather than anyone who can reach the service? → A: Operator
  authentication, required to mint. Reachability alone is never sufficient in the
  gated posture.
- Q: Forward auth or OIDC for that operator authentication? → A: Support both
  (the app platform offers both); the operator picks one via configuration.
  Forward auth is simplest (trusted proxy-injected header); OIDC is strongest
  (the service verifies a signed token via JWKS + issuer/audience/expiry, so it
  does not rely on a network boundary for header trust) and is recommended where
  available.
- Q: Is the pairing code shown as a scannable QR? → A: Not in v1 — copy button
  only. QR (for cross-device pairing) is deferred.
- Q: Does the CLI change? → A: Minimally. The CLI still sends whatever code it is
  handed as the bearer credential; the "save credentials for later push" step is
  removed because there is nothing durable to save.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pair-and-adopt with an ephemeral code (Priority: P1)

An operator has deployed a fresh remo-web service behind a forward-auth reverse
proxy and signed in through that proxy. They open the service in a browser and
land on the awaiting-adoption page, which displays a **Copy pairing code**
button (the code itself is never shown). They click it, the code lands on their
clipboard, and on their workstation they run `remo web adopt <url>` and paste
the code when prompted. The CLI completes the existing adoption work (retrieve
service identity, push registry + verified host keys, authorize the service key
on each instance, run the verification pass). The browser flips to the
dashboard.

**Why this priority**: This is the headline capability — it replaces the static
token as the *only* way to perform first-time adoption. Without it there is no
adoption path at all.

**Independent Test**: Behind a stub forward-auth proxy that injects a valid
identity header, load the adopt page, mint + copy a code, run `remo web adopt`
with that code against a service managing ≥2 reachable instances, and confirm
the dashboard shows all instances with working terminals and no static token was
configured anywhere.

**Acceptance Scenarios**:

1. **Given** an unconfigured service behind forward auth and an authenticated
   operator on the adopt page, **When** the page mints a pairing code and the
   operator runs `remo web adopt` with it, **Then** adoption completes exactly as
   in 011 and the code authenticated every setup call.
2. **Given** a live pairing code, **When** the operator pauses on the CLI's
   interactive fingerprint prompt for several minutes while the CLI keeps making
   setup calls, **Then** the sliding TTL keeps the code valid and adoption
   succeeds.
3. **Given** a pairing code was minted, **When** the operator reloads/reopens the
   adopt page, **Then** a new code is minted and the prior code no longer
   authenticates (rotation on open).

### User Story 2 - Setup surface is dormant until pairing (Priority: P1)

Before anyone opens the adopt page, and after a pairing session ends, every
`/api/v1/setup/*` route answers `404`, byte-identical to an unknown route.
Public liveness/readiness (`/api/v1/health`, `/api/v1/ready`) and the SPA remain
available so the adopt page can render and the container passes healthchecks.

**Why this priority**: The dormant-by-default surface is the core security
property that makes an ephemeral-code model acceptable; it is inseparable from
US1.

**Independent Test**: With no pairing session live, assert `404` on every setup
route; mint a session and assert the routes respond to the live code; end the
session (expiry or completion) and assert `404` again.

**Acceptance Scenarios**:

1. **Given** a freshly booted unconfigured service with nobody on the adopt page,
   **When** any `/api/v1/setup/*` route is requested (with or without a bearer),
   **Then** the response is `404 {"detail":"Not Found"}`.
2. **Given** a live pairing session, **When** the pairing code expires by idle
   TTL, **Then** subsequent setup calls with that code return `404` and the
   surface is dormant again.
3. **Given** adoption completed, **When** the setup surface is probed, **Then**
   it is dormant (404) — the code that drove the adoption no longer works.

### User Story 3 - Operator authentication gates code minting (Priority: P1)

The service is configured with an operator-authentication provider — either a
trusted forward-auth identity header, or OIDC token verification. A request to
mint a pairing code that lacks a valid authenticated identity is refused; a
request carrying one succeeds. The operator's authenticated identity is recorded
in the pairing session and in audit logs (never the code).

**Why this priority**: Operator authentication is what turns "anyone who can
reach the page" into "an authenticated operator," and is the precondition that
makes minting safe. It is a P1 security control, not an enhancement.

**Independent Test**: For each provider, attempt to mint a code with no
credential (refused), with an invalid credential (a client-spoofed header the
proxy would have stripped / a token failing signature/issuer/audience/expiry —
refused), and with a valid credential (succeeds); confirm the audit log names the
identity and never the code.

**Acceptance Scenarios**:

1. **Given** forward auth is the configured provider, **When** a mint request
   arrives without a trusted identity header, **Then** minting is refused and no
   session is created.
2. **Given** OIDC is the configured provider, **When** a mint request presents a
   token that fails verification (bad signature, wrong issuer/audience, or
   expired), **Then** minting is refused and no session is created.
3. **Given** either provider, **When** a mint request carries a valid credential,
   **Then** a code is minted and the session records the verified authenticated
   identity.
4. **Given** a deployment in the network-restricted posture (no provider
   configured, explicitly opted into), **When** a mint request arrives, **Then**
   minting proceeds without a credential, and startup/logs clearly record that
   the weaker posture is active.

### User Story 4 - Re-sync after local changes uses the same flow (Priority: P2)

After adoption, the operator changes their local registry and wants the service
to reflect it. In the dashboard they open a **Pair CLI to sync** affordance,
which mints a fresh pairing code (same operator-auth gate, same TTL/rotation).
They run `remo web push <url>` on the workstation, paste the code, and the push
applies. When the affordance is closed or the code expires, the setup surface is
dormant again.

**Why this priority**: Ongoing sync is the steady-state use after first adoption;
reusing the identical mechanism keeps one security model rather than two.

**Independent Test**: On an adopted service, open the re-sync affordance, mint a
code, run `remo web push` with it against a changed local registry, and confirm
the service registry mirrors the change; then confirm the surface is dormant
after the affordance closes.

**Acceptance Scenarios**:

1. **Given** an adopted service, **When** the operator opens the re-sync
   affordance and mints a code, **Then** `remo web push` with that code applies
   the registry mirror.
2. **Given** a re-sync code, **When** the affordance is closed (or the code
   expires), **Then** `/api/v1/setup/*` is dormant again.

### Edge Cases

- **Reused/expired code**: a code presented after its session ended (TTL,
  rotation, page-hide beacon, or adoption completion) is treated as unknown →
  `404`, never a distinguishable `401`.
- **Concurrent pages/tabs**: opening a second adopt page mints a new code and
  invalidates the prior; an adoption in flight on the prior code will then fail
  its next call. Documented most-recent-wins behavior.
- **Restart mid-adoption**: pairing sessions are in-memory; a container restart
  drops them. The operator re-opens the page for a new code. (The service
  identity keypair persists, per 011 FR-002.)
- **Header spoofing**: in the forward-auth posture, the service trusts the
  identity header only from the proxy; a deployment MUST ensure clients cannot
  reach the app directly and inject the header. This is the standard forward-auth
  trust boundary and MUST be documented.
- **Clock/monotonic time**: TTL is measured against a monotonic source so a
  system clock change cannot extend or prematurely expire a session.
- **Code never logged**: the pairing code MUST NOT appear in logs, error
  messages, the DOM, or the SPA state beyond the transient clipboard write.
- **Health/readiness unaffected**: minting, rotation, and dormancy MUST NOT
  change `/api/v1/health` or `/api/v1/ready` behavior; an unconfigured service
  still reports the healthy "awaiting adoption" readiness of 011.

## Requirements *(mandatory)*

### Functional Requirements

#### Pairing session lifecycle (User Story 1, 2)

- **FR-001**: The service MUST mint an ephemeral **pairing code** on demand from
  the adopt page: a high-entropy random value held only in memory, associated
  with a single pairing **session**.
- **FR-002**: A pairing session MUST have a **sliding idle TTL**: it expires a
  configurable idle interval after the last successful authenticated setup call
  (default on the order of 15 minutes), measured against a monotonic clock.
- **FR-003**: Opening/mounting the adopt page (or the re-sync affordance) MUST
  mint a fresh code and invalidate any prior live session (rotation on open,
  most-recent-wins).
- **FR-004**: The page MUST best-effort invalidate the session when it is hidden
  or unloaded (e.g. a `sendBeacon`), with the idle TTL as the authoritative
  backstop; the service MUST NOT depend on the browser for correctness.
- **FR-005**: The `/api/v1/setup/*` surface MUST be **dormant** — every route
  answering `404` identical to an unknown route — whenever no pairing session is
  live. It becomes reachable only for the currently live code.
- **FR-006**: A setup request whose bearer is absent, unknown, or belonging to an
  expired/rotated session MUST receive the same dormant `404`, never a
  distinguishable `401` that would reveal a session exists. (Rationale: a dormant
  surface must be indistinguishable from an absent one, mirroring 011 FR-021.)
- **FR-007**: Successful adoption/push (a registry apply that transitions or
  refreshes configured state) MUST end the pairing session so the surface returns
  to dormant.
- **FR-008**: Pairing sessions and codes MUST NOT be persisted to disk; a process
  restart MUST drop all sessions.

#### Operator-authentication gating (User Story 3)

- **FR-009**: The service MUST support requiring operator authentication to mint
  a pairing code, via a configurable provider. At least two providers MUST be
  supported: **forward auth** (a trusted proxy-injected identity header, e.g.
  `X-Forwarded-User` / `Remote-User`, with a configurable header name) and
  **OIDC** (a token presented to the service).
- **FR-010**: For the OIDC provider, the service MUST **cryptographically
  verify** the presented token before minting: signature against the IdP's
  published keys (JWKS, discovered via the issuer's well-known configuration),
  plus issuer, audience, and expiry/not-before checks with a bounded clock-skew
  allowance. A token failing any check MUST be treated as no credential.
- **FR-011**: When operator authentication is required and no valid credential is
  present, the mint request MUST be refused and no session created; the refusal
  MUST be observable in logs with request context and MUST NOT create or reveal a
  code. Verification failures MUST NOT leak token contents into logs.
- **FR-012**: The service MUST record the verified authenticated identity that
  minted a session (in the session and in audit logs) and MUST associate
  adoption/push actions with it; the pairing code itself MUST never be logged.
- **FR-013**: The service MUST support an explicit, clearly-logged
  **network-restricted** posture in which no operator-authentication provider is
  configured (for loopback/private/dev deployments). This posture MUST be opt-in
  and MUST be surfaced at startup and in readiness/diagnostics as the weaker
  posture, so it is never entered silently.
- **FR-014**: The service MUST treat a forward-auth identity header as
  trustworthy only under the documented trust boundary (proxy in front, direct
  client access to the app prevented). Both providers' configuration — including
  the hola-app setup for each — MUST be documented for operators, noting OIDC as
  the stronger option where available.

#### Pairing code delivery (User Story 1, 4)

- **FR-015**: The awaiting-adoption page MUST offer a **copy pairing code**
  action that places the current code on the clipboard and MUST NOT display the
  code value on screen or retain it in page state beyond the copy.
- **FR-016**: The code MUST be fetched only on an explicit operator action
  (mint/copy), returned with cache-defeating headers, and never embedded in the
  initially served HTML/bundle.
- **FR-017**: The dashboard MUST provide an equivalent **pair-to-sync** affordance
  for adopted services (User Story 4) that mints and copies a code through the
  same lifecycle and gating.

#### CLI (User Story 1, 4)

- **FR-018**: `remo web adopt` / `remo web push` MUST accept the pairing code the
  same way they accept a token today (prompt, option, or `REMO_API_TOKEN`),
  sending it as the bearer credential for setup calls; no new CLI concept is
  required.
- **FR-019**: The CLI MUST NOT persist adoption credentials (the prior
  "save credentials for later push" behavior is removed); each adoption/push
  obtains a fresh code from the page.
- **FR-020**: When a setup call returns the dormant `404` (expired/rotated code),
  the CLI MUST surface an actionable message telling the operator to reopen the
  adopt page (or re-sync affordance) for a fresh code and retry.

#### Migration / removal of the static token

- **FR-021**: The static `REMO_WEB_API_TOKEN` gate MUST be removed; a value set
  for it MUST NOT grant setup access. Its removal MUST be documented as a
  breaking change from 011, with the pairing flow as the replacement.
- **FR-022**: The compose example and hola/app documentation MUST be updated to
  the pairing model (operator-auth front door — forward auth or OIDC; no static
  token secret to manage).

### Key Entities

- **Pairing session**: an in-memory record for one adoption/push handoff —
  opaque code, minting identity (from the operator-auth provider, when present),
  monotonic last-activity timestamp, idle TTL, and origin (adopt page vs
  re-sync). At most one live session (most-recent-wins). Never persisted.
- **Pairing code**: the high-entropy bearer the operator copies into the CLI.
  Exists only in memory and transiently on the operator's clipboard.
- **Operator-auth provider**: the configured mechanism that authenticates the
  minting request — either **forward auth** (a trusted proxy-injected identity
  header) or **OIDC** (a token the service verifies via JWKS +
  issuer/audience/expiry). Yields the **authenticated operator identity** stored
  on the session; establishes that the minting request is an authenticated
  operator, not merely a reachable client.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With no pairing session live, 100% of `/api/v1/setup/*` requests
  return `404` identical to an unknown route (dormant surface).
- **SC-002**: An operator can complete first-time adoption using only a
  page-minted code copied to the clipboard — no static token configured anywhere.
- **SC-003**: A pairing code survives an adoption that idles on an interactive
  prompt for at least the configured idle window, provided setup calls continue,
  and expires within that idle window after activity stops.
- **SC-004**: With an operator-auth provider required (forward auth or OIDC), a
  mint request without a valid credential never yields a working code (0%
  success), while one with a valid credential (trusted header / verified token)
  succeeds.
- **SC-005**: Reopening the adopt page invalidates the previously minted code in
  100% of cases (rotation on open).
- **SC-006**: The pairing code appears in zero log lines, zero error payloads,
  and is absent from the served HTML/JS bundle (only present transiently after an
  explicit copy).
- **SC-007**: A re-sync (`remo web push`) can be completed end-to-end using the
  dashboard affordance's code, and the setup surface is dormant before and after.
- **SC-008**: `/api/v1/health` and `/api/v1/ready` behavior is byte-unchanged
  from 011 across dormant, live-session, and post-adoption states.

## Assumptions

- The service is deployed with an operator-authentication provider in front:
  either a reverse proxy performing forward auth (injecting a trusted identity
  header, with direct client access to the app prevented so the header cannot be
  spoofed), or OIDC (the service verifies a signed token). This is the
  recommended posture and the one the security model relies on. The app platform
  (hola) supports both; OIDC is stronger where available because verification is
  cryptographic rather than boundary-dependent. (A network-restricted posture
  with no provider is supported but explicitly weaker — FR-013.)
- The operator's browser and workstation may be the same machine or different
  machines; v1 delivery is clipboard copy, so cross-device pairing (QR) is a
  later enhancement.
- The service identity keypair and its persistence (011 FR-002) are unchanged;
  this feature changes only how the setup surface is authorized, not what
  adoption does once authorized.
- One operator drives one adoption/push at a time (most-recent-wins rotation is
  acceptable; concurrent multi-operator pairing is not a goal).

## Out of Scope

- **QR display** of the pairing code for cross-device pairing (planned follow-up;
  copy-to-clipboard only in v1).
- **Multiple concurrent named deployments / multi-operator concurrent pairing.**
- **A durable service credential** of any kind (the removal of the static token
  is deliberate; there is no replacement long-lived secret).
- **Changes to what adoption/push do** once authorized (registry mirror, host-key
  verification, key authorization, verification pass) — all inherited unchanged
  from 011.
- **Building the reverse proxy / IdP / SSO itself** — the service *consumes* a
  forward-auth header or *verifies* an OIDC token; standing up the proxy or
  identity provider is the operator's/platform's job.
- **Acting as a full OIDC relying party** (interactive redirect/callback/cookie
  login flow implemented by the service). v1 verifies a presented token; it does
  not run the browser authorization-code dance itself (the platform/proxy does).
