# Phase 1 Data Model: Ephemeral Device-Pairing Adoption

All entities are **in-memory only** (FR-008) except where noted as inherited-
unchanged from 011. Nothing here is persisted to disk.

---

## PairingSession (in-memory, `web/pairing.py`)

One live record for a single adoption/push handoff. At most one exists at a time
(most-recent-wins, FR-003).

| Field | Type | Notes |
|---|---|---|
| `code` | `str` | High-entropy bearer, `secrets.token_urlsafe(24)` (R6). Compared with `hmac.compare_digest`. Never logged, never rendered (FR-016). |
| `identity` | `OperatorIdentity \| None` | The authenticated operator that minted it (FR-012). `None` only in network-restricted posture (anonymous). |
| `origin` | `Literal["adopt", "resync"]` | Which affordance minted it (adopt page vs dashboard re-sync). Diagnostic only. |
| `last_activity` | `float` | `time.monotonic()` reading; updated on each successful setup call (`touch()`). Basis for the sliding idle TTL (R9). |
| `ttl_s` | `float` | Idle interval; `REMO_WEB_PAIRING_TTL_S`, default 900 (FR-002). |

**Derived**: `is_expired(now) -> bool` ≡ `now - last_activity > ttl_s`.

**Lifecycle / state transitions**:

```text
(none) --mint()--> LIVE
LIVE   --successful setup call (touch)--> LIVE  (last_activity reset)
LIVE   --mint() again (rotation)--> LIVE (new code; prior code invalid)   [FR-003]
LIVE   --idle > ttl_s--> (none)  (lazy expiry on is_live/authenticate)     [FR-002]
LIVE   --successful adoption/push apply--> (none)  (end)                   [FR-007]
LIVE   --pagehide sendBeacon /pairing/end--> (none)  (best-effort)         [FR-004]
LIVE   --process restart--> (none)  (never persisted)                      [FR-008]
```

**Validation / invariants**:
- At most one live session (the manager slot). Minting replaces it.
- An expired/rotated/absent session is indistinguishable to callers: every
  setup call in those states yields the dormant `404` (FR-005/FR-006).
- `code` exists only in memory + transiently on the operator's clipboard.

## PairingSessionManager (in-memory singleton, `app.state`)

| Member | Signature | Behavior |
|---|---|---|
| `mint` | `(identity, origin) -> str` | Create fresh code, evict prior session (rotation), stamp `last_activity`, return raw code once. Lock-guarded. |
| `authenticate` | `(presented: str) -> PairingSession \| None` | Constant-time match against the live, non-expired session; `touch()` + return it, else `None`. |
| `is_live` | `() -> bool` | A non-expired session exists (drives dormancy). Lazily drops an expired one. |
| `end` | `() -> None` | Drop the live session. Idempotent (completion, beacon, rotation all use it). |

Injected clock: `now: Callable[[], float] = time.monotonic` for deterministic
TTL tests (R9).

## OperatorIdentity (`web/operator_auth.py`)

| Field | Type | Notes |
|---|---|---|
| `subject` | `str` | Operator identifier from the auth provider (the forward-auth header value, or `"network-restricted"` for the anonymous posture). Recorded on the session + in audit logs (FR-012). |
| `provider` | `str` | `"forward"` \| `"network-restricted"` (future: `"oidc"`). |

## OperatorAuthProvider (seam, `web/operator_auth.py`)

`Protocol` with one method — the pluggable gate (FR-010).

```text
class OperatorAuthProvider(Protocol):
    def authenticate(self, request: Request) -> OperatorIdentity | None: ...
```

| Implementation | Config | authenticate() |
|---|---|---|
| `ForwardAuthProvider(header_name)` | `REMO_WEB_OPERATOR_AUTH=forward` + `REMO_WEB_FORWARD_AUTH_HEADER=<name>` (required; fail-fast if missing, FR-009) | Returns `OperatorIdentity(subject=<header value>, "forward")` when the trusted header is present & non-empty; else `None` (mint refused, FR-011). |
| `NetworkRestrictedProvider` | `REMO_WEB_OPERATOR_AUTH=none` (loud opt-in, FR-013) | Always returns `OperatorIdentity("network-restricted", "network-restricted")`. |
| _unconfigured_ | env unset | No provider → mint refused with actionable diagnostic; readiness flags "operator auth not configured" (R5). |

_Future (Out of Scope): `OidcVerifierProvider` (JWKS + iss/aud/exp) slots in here
with no change to `PairingSession*`._

## WebSettings — added / removed fields (`web/config.py`)

| Change | Field | Notes |
|---|---|---|
| **removed** | `api_token` | The static `REMO_WEB_API_TOKEN` gate is gone (FR-021). A set value is ignored (inert), with a one-line "now ignored" info log at startup (R13). |
| added | `pairing_ttl_s: float` | `REMO_WEB_PAIRING_TTL_S`, default `900.0` (FR-002). |
| added | `operator_auth: str` | `REMO_WEB_OPERATOR_AUTH` — `"forward"` \| `"none"` \| `""` (unset = minting disabled). |
| added | `forward_auth_header: str` | `REMO_WEB_FORWARD_AUTH_HEADER` — no default; required when `operator_auth="forward"`. |

## Workstation-side: saved file (`core/web_adopt.py`) — reduced

| Change | Item | Notes |
|---|---|---|
| **removed** | `SavedCredentials.url`, `.token` | No durable credential persisted (FR-019). |
| removed | `save_credentials`, `load_saved_credentials` (credential path), `--save` flag, `NoSavedCredentialsError` fallback | Adopt/push obtain a fresh code every time (FR-018/FR-019). |
| retained (optional) | non-secret push cache | Deployment id + per-instance fingerprints only (no url/token) for the push skip-optimization; keyed by the deployment id read from `/status` at push time (R10). |

## Inherited unchanged from 011 (referenced, not modified)

- **Service identity** (`web-identity/id_ed25519{,.pub}`, `known_hosts`,
  `state.json`) — persists across restarts (011 FR-002); this feature does not
  touch it.
- **AdoptionPayload** (`PUT /setup/registry` body: `version`, `registry[]`,
  `host_keys{}`) — wire shape and atomic two-file apply are unchanged.
- **KnownHost** registry entries and the colon-delimited registry file.
