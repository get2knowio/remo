# Quickstart: Ephemeral Device-Pairing Adoption

Runnable validation scenarios proving the pairing model end-to-end. These map to
the spec's Success Criteria (SC-00x) and the contracts in `contracts/`. Details
live in `data-model.md` / `contracts/` — this file is the run guide.

## Prerequisites

```bash
uv sync --extra web            # FastAPI/Uvicorn service side
cd frontend && npm ci && npm run build && cd ..   # SPA (served same-origin)
```

- A workstation `remo` CLI with ≥2 bootstrapped, reachable instances in the
  registry (for the full adopt path).
- For forward-auth scenarios: any way to inject a header (curl `-H`, or a stub
  proxy). No real IdP is required to validate the gate.

## Scenario A — Setup surface is dormant at rest (SC-001, US2)

```bash
# Start unconfigured, network-restricted posture (loopback dev):
REMO_WEB_OPERATOR_AUTH=none uv run remo web serve &     # loudly logs weaker posture

# With nobody on the adopt page (no session minted yet):
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/setup/status
```

**Expected**: `404`. Every `/api/v1/setup/*` route returns `404 {"detail":"Not
Found"}`, byte-identical to an unknown route — with or without a bearer.

## Scenario B — Mint, adopt, auto-dormant (SC-002, SC-005, SC-007, US1)

1. Open `http://127.0.0.1:8080/` in a browser → the awaiting-adoption page.
   On mount the SPA mints a session (rotation-on-open); the code lives only in a
   ref. Click **Copy pairing code** — the code is on your clipboard, never shown.
2. On the workstation:
   ```bash
   remo web adopt http://127.0.0.1:8080     # paste the code at "Pairing code:"
   ```
   **Expected**: adoption completes exactly as in 011 (identity → registry →
   verify), the browser flips to the dashboard, and no static token was
   configured anywhere (SC-002).
3. Re-probe the setup surface:
   ```bash
   curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/api/v1/setup/status
   ```
   **Expected**: `404` — the successful apply ended the session (FR-007).
4. **Rotation (SC-005)**: reopen/reload the adopt page, copy a code, then reload
   again and copy a second code. The first code no longer authenticates
   (`404`); only the most-recent code works.

## Scenario C — Sliding TTL survives the fingerprint pause (SC-003, US1 scenario 2)

Adopt a service with an unverified instance so the CLI pauses on the interactive
host-key fingerprint prompt. With the default 900 s idle TTL, leave the prompt
for several minutes while the CLI continues making setup calls, then confirm.

**Expected**: the code stays valid across the pause (each successful setup call
touches the sliding window); after activity stops, the session expires within
the idle window and further calls return the dormant `404`. For a fast test, run
the service with `REMO_WEB_PAIRING_TTL_S=5` and assert a setup call succeeds
before 5 s of idle and `404`s after.

## Scenario D — Forward auth gates minting (SC-004, US3)

```bash
# Forward auth required; trust the header X-Forwarded-User:
REMO_WEB_OPERATOR_AUTH=forward REMO_WEB_FORWARD_AUTH_HEADER=X-Forwarded-User \
  uv run remo web serve &

# Mint WITHOUT the trusted header → refused, no session created:
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8080/api/v1/pairing/mint
# Expected: 403

# Mint WITH the proxy-injected header → succeeds:
curl -s -X POST -H 'X-Forwarded-User: alice' http://127.0.0.1:8080/api/v1/pairing/mint
# Expected: 200 {"expires_in":900,"code":"..."} and the audit log names "alice", never the code.
```

**Fail-fast check (FR-009)**:
```bash
REMO_WEB_OPERATOR_AUTH=forward uv run remo web serve   # no header configured
```
**Expected**: startup **fails fast** with a clear "forward auth enabled but
`REMO_WEB_FORWARD_AUTH_HEADER` is not set" error — the service never trusts an
unnamed header.

## Scenario E — Re-sync uses the same flow (SC-007, US4)

On the adopted service, open the dashboard's **Pair CLI to sync** affordance
(mints a fresh code, same gate/TTL/rotation), copy the code, then:

```bash
remo web push http://127.0.0.1:8080      # paste the code
```

**Expected**: the service registry mirrors the local change; before and after
the affordance is open, `/api/v1/setup/*` is dormant (`404`). No credentials
were saved between adopt and push (FR-019).

## Scenario F — Code never leaks (SC-006)

- `grep -ri "<the copied code>" server-logs` → **zero** matches.
- View source / the built JS bundle → the code string is **absent** (it is
  fetched at runtime on page open, never embedded).
- Inspect the DOM while on the adopt page → the code is **not present** (only a
  Copy button); it exists only transiently on the clipboard after an explicit
  copy.

## Scenario G — Health/readiness unchanged (SC-008)

```bash
curl -s http://127.0.0.1:8080/api/v1/ready     # dormant, live-session, and post-adoption
```

**Expected**: `/api/v1/health` and `/api/v1/ready` behavior is byte-unchanged
from 011 across all three states; an unconfigured service still reports the
"awaiting adoption" readiness. The operator-auth posture appears only as
additive diagnostic detail (forward / network-restricted / unconfigured), not as
a change to the `ready` status value.

## Automated coverage (pytest)

- `tests/unit/web/` — pairing lifecycle with an injected fake monotonic clock
  (mint/rotate/touch/expire/end), dormancy `404` on every setup route, mint
  forward-auth allow/refuse, `forward`-without-header startup fail-fast,
  network-restricted loud opt-in + readiness posture.
- `tests/unit/core/` — `web_adopt` credential removal, dormant-`404` → "reopen
  page" mapping, push resolves URL+code every time.
- `tests/integration/` — adopt + push E2E against a live `remo web serve` behind
  a stub forward-auth header.
