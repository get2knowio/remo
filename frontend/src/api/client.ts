// Typed REST client for the Remo web service (`/api/v1`).
//
// Request/response types below are generated from the FastAPI service's
// OpenAPI contract, not hand-mirrored from a spec doc. The source of truth
// is `frontend/src/api/generated/schema.d.ts` (produced by `openapi-typescript`
// from `frontend/src/api/generated/openapi.json`). To refresh both, run
// `uv run python scripts/export_openapi.py` (regenerates the OpenAPI source
// from the service) followed by `npm run generate:types` (regenerates the
// TypeScript) from `frontend/`. See docs/maintaining-generated-types.md.

import type { components } from "./generated/schema";

// ---- Discovery types (generated from the service's OpenAPI contract) ----

export type ZellijState = components["schemas"]["ZellijState"];
export type DevcontainerRunning = components["schemas"]["DevcontainerRunning"];

/**
 * A session target's git fields (git_tracked/git_dirty/git_ahead/git_behind)
 * are read-only status added by hosts running the newer remo-host agent;
 * older hosts omit these and the server defaults them to false/0, so the
 * rail simply shows no git glyphs. ahead/behind may be stale — discovery
 * never runs `git fetch`.
 */
export type SessionTarget = components["schemas"]["SessionTargetOut"];

export type InstanceStatus = components["schemas"]["InstanceStatus"];

export type RemoteCapability = components["schemas"]["CapabilityOut"];

export type TypedError = components["schemas"]["ErrorOut"];

export type DiscoveryInstance = components["schemas"]["InstanceOut"];

export type HostsResponse = components["schemas"]["HostsResponse"];

export type SessionsResponse = components["schemas"]["SessionsResponse"];

export type RefreshResponse = components["schemas"]["RefreshResponse"];

// ---- Error handling ----

/**
 * Typed error thrown by every client method below. Carries the structured
 * `{code, message, retryable, remediation}` envelope from the server (or a
 * synthesized equivalent for network-level failures) so UI code can render
 * retry affordances / remediation text instead of a generic message string.
 */
export class ApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly remediation: string;

  constructor(typedError: TypedError) {
    super(typedError.message);
    this.name = "ApiError";
    this.code = typedError.code;
    this.retryable = typedError.retryable;
    this.remediation = typedError.remediation;
  }
}

type ErrorEnvelope = components["schemas"]["ErrorEnvelope"];

// ---- Forward-auth (SSO proxy) re-authentication ----
//
// When remo-web is deployed behind a whole-app forward-auth proxy (Traefik
// ForwardAuth + an OIDC IdP such as Authentik — e.g. a Hola app), an
// unauthenticated or expired-session request is answered with a 302 to the
// cross-origin IdP (`https://auth.example.com/application/o/authorize/...`). A
// same-origin `fetch()` cannot complete that SSO round-trip, and remo-web's
// strict `connect-src 'self'` CSP blocks following the redirect at all — so the
// only way to restore the session is a TOP-LEVEL navigation, which re-triggers
// the proxy's SSO flow (the browser CAN follow it through the IdP and back).
//
// `request()` uses `redirect: "manual"` so such a redirect surfaces as an opaque
// response (`response.type === "opaqueredirect"`, status 0) instead of throwing
// on the blocked cross-origin follow; we then reload the document to re-auth. A
// sessionStorage cooldown prevents a reload loop when auth genuinely can't
// complete. With no proxy (REMO_WEB_OPERATOR_AUTH=none) there are no redirects,
// so this path never fires.

const _REAUTH_KEY = "remo:last-reauth";
const _REAUTH_COOLDOWN_MS = 10_000;

/** True once this document has committed to a re-auth navigation. Concurrent
 * challenges are the NORM, not the exception — returning to a backgrounded tab
 * fires the discovery poll, the sessions fetch and the readiness poll at once,
 * and a lapsed proxy session challenges all of them. Only the first may
 * navigate; without this flag the others hit the sessionStorage cooldown below
 * and wrongly report that re-auth had already failed. */
let _reauthNavigating = false;

/** Set once a COMPLETED reload has been challenged again. The cooldown alone
 * only suppresses re-auth for 10s, so a genuinely broken proxy would reload the
 * page every 10 seconds forever — a page that reloads under the operator is
 * worse than one that stops and says why. After giving up, this document never
 * navigates again; the error's remediation asks for a manual reload, and this
 * makes that true. */
let _reauthGaveUp = false;

/** A challenge arriving while a re-auth navigation is already under way. Benign:
 * the document is about to be replaced. */
function _reauthInFlightError(): ApiError {
  return new ApiError({
    code: "auth_challenge",
    message: "Re-authenticating…",
    retryable: false,
    remediation: "",
  });
}

/** Re-auth ran and did not produce a usable session. */
function _reauthFailedError(): ApiError {
  return new ApiError({
    code: "auth_required",
    message: "Sign-in is required, but the access proxy did not restore a session.",
    retryable: false,
    remediation:
      "Sign in through your access proxy, then reload this page. If it repeats, the " +
      "forward-auth proxy may be misconfigured (its session cookie is not reaching this app).",
  });
}

/**
 * Handle a forward-auth challenge on an XHR by re-authenticating through a
 * top-level navigation. Never returns normally: it either reloads the whole
 * document (throwing to halt the caller before the navigation lands), reports
 * that a reload is already under way, or — if a previous reload was already
 * challenged again — throws a clear `auth_required` ApiError instead of looping.
 */
function reauthenticate(): never {
  if (_reauthNavigating) {
    throw _reauthInFlightError();
  }
  if (_reauthGaveUp) {
    throw _reauthFailedError();
  }
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(_REAUTH_KEY) ?? 0) || 0;
  } catch {
    last = 0;
  }
  const now = Date.now();
  if (now - last < _REAUTH_COOLDOWN_MS) {
    // We reloaded to re-auth and are being challenged again — the SSO round
    // trip isn't restoring a usable session. Stop reloading, for good.
    _reauthGaveUp = true;
    throw _reauthFailedError();
  }
  try {
    sessionStorage.setItem(_REAUTH_KEY, String(now));
  } catch {
    // sessionStorage unavailable — navigate anyway.
  }
  _reauthNavigating = true;
  // A full document request re-runs the proxy's SSO redirect chain (IdP round
  // trip), unlike a fetch which cannot. The SPA reloads authenticated and
  // subsequent XHRs return 200. reload() rather than assign(location.href):
  // assigning the CURRENT url is a same-document navigation when it carries a
  // fragment, which would silently skip the network round trip we need.
  window.location.reload();
  // reload() schedules the navigation asynchronously and lets sync code keep
  // running; throw so the caller never treats this as data.
  throw _reauthInFlightError();
}

/**
 * `fetch` for same-origin API calls, with forward-auth challenge detection.
 *
 * EVERY call to this app's API must go through here. A plain `fetch()` follows
 * the proxy's cross-origin 302 to the IdP, which the strict `connect-src 'self'`
 * CSP then blocks — surfacing as an opaque "Failed to fetch" that callers
 * misread as "the service is down" (it presented as a bogus offline overlay
 * when the readiness poll did exactly this).
 *
 * Two shapes of challenge are recognized, because forward-auth proxies differ:
 *   - a 3xx to the IdP, seen as `type: "opaqueredirect"` thanks to
 *     `redirect: "manual"`. This is unambiguous only because the API itself
 *     never issues a redirect of any kind — every route is an exact path, so a
 *     3xx on an API call always came from something in front of us;
 *   - a bare 401. The service itself never issues one (web/api/setup.py is
 *     explicit: a dormant surface answers 404, "never a distinguishable 401"),
 *     so a 401 reaching the browser came from the proxy in front of it.
 */
async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(path, { ...init, redirect: "manual" });
  } catch (cause) {
    // Network-level failure (offline, connection refused, etc.).
    throw new ApiError({
      code: "network_error",
      message: cause instanceof Error ? cause.message : "Network request failed",
      retryable: true,
      remediation: "Check your network connection and that the Remo web service is reachable.",
    });
  }
  if (response.type === "opaqueredirect" || response.status === 401) {
    reauthenticate();
  }
  return response;
}

/** True for the two error codes that mean "a re-auth is happening / needed",
 * so callers can avoid reporting a proxy challenge as a service outage. */
export function isAuthChallenge(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.code === "auth_challenge" || error.code === "auth_required")
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await apiFetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!response.ok) {
    let envelope: ErrorEnvelope | undefined;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      envelope = undefined;
    }
    if (envelope?.error) {
      throw new ApiError(envelope.error);
    }
    throw new ApiError({
      code: "unknown",
      message: `Request failed with status ${response.status}`,
      retryable: false,
      remediation: "Check the server logs for details.",
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

// ---- Discovery (T028, US1) ----

export async function getHosts(): Promise<HostsResponse> {
  return request<HostsResponse>("/api/v1/hosts", { method: "GET" });
}

export async function getSessions(): Promise<SessionsResponse> {
  return request<SessionsResponse>("/api/v1/sessions", { method: "GET" });
}

/**
 * Trigger a server-side discovery run. `GET /hosts` and `GET /sessions` only
 * READ the service's cache — this is the only call that repopulates it.
 *
 * `force: false` asks for a TTL-gated run (no-op while the cache is fresh),
 * which is what the background poll sends so a long-lived page stays current
 * without every tick costing an SSH round trip to every instance.
 */
export async function refreshDiscovery(
  instanceId?: string,
  options?: { force?: boolean },
): Promise<RefreshResponse> {
  const body: { instance_id?: string; force?: boolean } = {};
  if (instanceId) {
    body.instance_id = instanceId;
  }
  if (options?.force === false) {
    body.force = false;
  }
  return request<RefreshResponse>("/api/v1/discovery/refresh", {
    method: "POST",
    body: Object.keys(body).length > 0 ? JSON.stringify(body) : undefined,
  });
}

// ---- Health / readiness ----

export type ReadinessCheck = string; // e.g. "ok" | "missing" | "unreadable" | ...

/**
 * Top-level service state reported by `GET /api/v1/ready` (011-web-adopt).
 * On a 200 response, `"unconfigured"` means the service is up but awaiting
 * adoption (`remo web adopt`); any OTHER 200 status (e.g. `"ok"`) means the
 * service is configured. 503 keeps its existing "broken/degraded" semantics.
 * Open union so unknown future 200 statuses are treated as configured.
 */
export type ServiceStatus = "ok" | "unconfigured" | (string & {});

export interface ReadinessResponse {
  /** true when GET /ready returned 200 (all gating checks pass). */
  ready: boolean;
  status: ServiceStatus;
  checks: Record<string, ReadinessCheck>;
  detail?: string;
}

/**
 * `GET /api/v1/ready` — returns 200 (ready) or 503 (not_ready) but always with
 * a `checks` body. Unlike the other calls this reads the body on BOTH statuses
 * (a 503 is expected config state, not a transport error). A 200 with
 * `status: "unconfigured"` means the service is awaiting adoption — see
 * `ServiceStatus` above. A network-level failure rejects with
 * `ApiError{code:"network_error"}` so callers can show the offline overlay.
 */
export async function getReady(): Promise<ReadinessResponse> {
  // Via apiFetch, not a bare fetch: a lapsed forward-auth session must trigger
  // re-auth, not masquerade as an unreachable service (the offline overlay).
  const response = await apiFetch("/api/v1/ready", { method: "GET" });
  let body: { status?: string; checks?: Record<string, string>; detail?: string } = {};
  try {
    body = (await response.json()) as typeof body;
  } catch {
    body = {};
  }
  return {
    ready: response.ok,
    status: body.status ?? (response.ok ? "ready" : "not_ready"),
    checks: body.checks ?? {},
    detail: body.detail,
  };
}

// ---- Pairing (012-web-adopt-pairing) ----
//
// The awaiting-adoption page (and the dashboard re-sync affordance) mints an
// ephemeral pairing code on open, which the operator copies to the clipboard
// and pastes into `remo web adopt` / `remo web push`. The code is returned only
// by mintPairingCode() at runtime — never embedded in the bundle (FR-016) — and
// the caller MUST hold it out of the DOM (copy-only, never displayed).

export type MintPairingResponse = components["schemas"]["MintPairingResponse"];

/**
 * `POST /api/v1/pairing/mint` — mint a fresh code (rotation-on-open, FR-003).
 * `origin` distinguishes the adopt page from the dashboard re-sync affordance.
 * A `403` means operator authentication is required/not configured (the page is
 * reached through a forward-auth proxy in the gated posture) — surfaced via
 * ApiError so the page can show guidance rather than a code.
 */
export async function mintPairingCode(
  origin: "adopt" | "resync" = "adopt",
): Promise<MintPairingResponse> {
  const response = await apiFetch(`/api/v1/pairing/mint?origin=${encodeURIComponent(origin)}`, {
    method: "POST",
  });
  if (response.status === 403) {
    // Operator authentication required / not configured — surface a distinct
    // code so the adopt page can prompt to sign in rather than showing a code.
    throw new ApiError({
      code: "forbidden",
      message: "Operator authentication is required to mint a pairing code.",
      retryable: false,
      remediation: "Sign in through your access proxy, then reload this page.",
    });
  }
  if (!response.ok) {
    throw new ApiError({
      code: "unknown",
      message: `Mint failed with status ${response.status}`,
      retryable: true,
      remediation: "Reload this page to try again.",
    });
  }
  return (await response.json()) as MintPairingResponse;
}

/**
 * `POST /api/v1/pairing/end` — best-effort session end (page-hide, FR-004).
 * Uses `navigator.sendBeacon` when available so it survives page unload; the
 * server treats it as idempotent and the idle TTL is the authoritative backstop.
 */
export function endPairing(): void {
  const path = "/api/v1/pairing/end";
  try {
    if (navigator.sendBeacon?.(path)) {
      return;
    }
  } catch {
    // fall through to fetch
  }
  // keepalive lets the request outlive the page during unload. Deliberately a
  // bare fetch, not apiFetch: this fires as the page goes away, where a re-auth
  // navigation would be both futile and destructive. The server's idle TTL is
  // the backstop if the challenge eats it.
  void fetch(path, { method: "POST", keepalive: true }).catch(() => undefined);
}

// ---- Terminals (T041, US2) ----
//
// Per contracts/rest-api.md and contracts/terminal-websocket.md. The token
// returned by createTerminal() is single-use and MUST travel only via the WS
// subprotocol list (never the URL/query string, FR-049) — see
// openTerminalSocket() below.

export type CreateTerminalResponse = components["schemas"]["CreateTerminalResponse"];

export type TerminalSummary = components["schemas"]["TerminalOut"];

export type ListTerminalsResponse = components["schemas"]["TerminalsListResponse"];

/** `POST /api/v1/terminals` — request a terminal for an opaque target id. */
export async function createTerminal(
  sessionTargetId: string,
  cols: number,
  rows: number,
): Promise<CreateTerminalResponse> {
  return request<CreateTerminalResponse>("/api/v1/terminals", {
    method: "POST",
    body: JSON.stringify({ session_target_id: sessionTargetId, cols, rows }),
  });
}

/** `GET /api/v1/terminals` — list this client's terminals. */
export async function listTerminals(): Promise<ListTerminalsResponse> {
  return request<ListTerminalsResponse>("/api/v1/terminals", { method: "GET" });
}

/** `DELETE /api/v1/terminals/{terminal_id}` — reap the PTY/SSH attachment. */
export async function closeTerminal(terminalId: string): Promise<void> {
  return request<void>(`/api/v1/terminals/${encodeURIComponent(terminalId)}`, {
    method: "DELETE",
  });
}

/**
 * Opens the raw WebSocket for `WS /api/v1/terminals/{terminal_id}`. The
 * single-use token rides as a WS subprotocol value alongside the protocol id
 * `remo-terminal.v1` — this is how it reaches the server without ever
 * touching the URL/query string (FR-049). Same-origin, matching the page's
 * `ws:`/`wss:` scheme.
 *
 * This function only constructs and returns the socket; connection-lifecycle
 * and control-frame handling live in `terminal/TerminalConnection.ts`.
 *
 * Forward-auth note: a raw WebSocket upgrade cannot itself distinguish a proxy
 * SSO redirect/401 from an ordinary failure. It does not need to — every attach
 * (and reconnect) first calls `createTerminal()`, which goes through
 * `request()` and so triggers the top-level SSO re-auth (see `reauthenticate`)
 * whenever the session has lapsed, before/at the point the socket is opened.
 */
export function openTerminalSocket(terminalId: string, token: string): WebSocket {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${wsProtocol}//${window.location.host}/api/v1/terminals/${encodeURIComponent(terminalId)}`;
  return new WebSocket(url, ["remo-terminal.v1", token]);
}
