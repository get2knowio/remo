// Typed REST client for the Remo web service (`/api/v1`).
//
// Types mirror `specs/010-web-session-interface/data-model.md` and
// `specs/010-web-session-interface/contracts/rest-api.md` exactly.

// ---- Discovery types (data-model.md) ----

export type ZellijState = "active" | "exited" | "absent";
export type DevcontainerRunning = "running" | "stopped" | "unknown";

export interface SessionTarget {
  id: string;
  instance_type: string;
  instance_name: string;
  project: string;
  has_devcontainer: boolean;
  zellij_state: ZellijState;
  devcontainer_running: DevcontainerRunning;
  discovered_at: string;
  // Read-only git status (added by hosts running the newer remo-host agent;
  // older hosts omit these and the server defaults them to false/0, so the
  // rail simply shows no git glyphs). ahead/behind may be stale — discovery
  // never runs `git fetch`.
  git_tracked: boolean;
  git_dirty: boolean;
  git_ahead: number;
  git_behind: number;
}

export type InstanceStatus =
  | "ok"
  | "unreachable"
  | "auth_failed"
  | "no_remo_host"
  | "incompatible_protocol"
  | "malformed"
  | "timeout";

export interface RemoteCapability {
  protocol_version: number;
  host_tools_version: string;
  projects_root: string;
  operations: string[];
  zellij: boolean;
  docker: boolean;
}

export interface TypedError {
  code: string;
  message: string;
  retryable: boolean;
  remediation: string;
}

export interface DiscoveryInstance {
  instance_id: string;
  instance_type: string;
  instance_name: string;
  status: InstanceStatus;
  region: string;
  capability?: RemoteCapability;
  error?: TypedError;
  refreshed_at: string;
}

export interface HostsResponse {
  instances: DiscoveryInstance[];
}

export interface SessionsResponse {
  targets: SessionTarget[];
}

export interface RefreshResponse {
  refreshing: boolean;
}

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

interface ErrorEnvelope {
  error: TypedError;
}

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

/**
 * Handle a forward-auth challenge on an XHR by re-authenticating through a
 * top-level navigation. Never returns normally: it either navigates the whole
 * document (throwing to halt the caller before the navigation lands) or, if we
 * already tried to re-auth within the cooldown, throws a clear `auth_required`
 * ApiError instead of looping.
 */
function reauthenticate(): never {
  let last = 0;
  try {
    last = Number(sessionStorage.getItem(_REAUTH_KEY) ?? 0) || 0;
  } catch {
    last = 0;
  }
  const now = Date.now();
  if (now - last < _REAUTH_COOLDOWN_MS) {
    // We just reloaded to re-auth and are being challenged again — the SSO
    // round-trip isn't restoring a usable session. Stop reloading; surface a
    // clear error rather than looping.
    throw new ApiError({
      code: "auth_required",
      message: "Sign-in is required, but the access proxy did not restore a session.",
      retryable: false,
      remediation:
        "Sign in through your access proxy and reload. If this repeats, the " +
        "forward-auth proxy may be misconfigured (its session cookie is not reaching this app).",
    });
  }
  try {
    sessionStorage.setItem(_REAUTH_KEY, String(now));
  } catch {
    // sessionStorage unavailable — navigate anyway.
  }
  // A full document request re-runs the proxy's SSO redirect chain (IdP round
  // trip), unlike a fetch which cannot. The SPA reloads authenticated and
  // subsequent XHRs return 200.
  window.location.assign(window.location.href);
  // location.assign() schedules the navigation asynchronously and lets sync
  // code keep running; throw so the caller never treats this as data.
  throw new ApiError({
    code: "auth_challenge",
    message: "Re-authenticating…",
    retryable: false,
    remediation: "",
  });
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      // Catch a forward-auth proxy's cross-origin SSO redirect as an opaque
      // response instead of a thrown CSP-blocked follow (see reauthenticate()).
      redirect: "manual",
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (cause) {
    // Network-level failure (offline, connection refused, etc.) — surface it
    // through the same ApiError shape as HTTP-level errors so callers only
    // ever handle one error type.
    throw new ApiError({
      code: "network_error",
      message: cause instanceof Error ? cause.message : "Network request failed",
      retryable: true,
      remediation: "Check your network connection and that the Remo web service is reachable.",
    });
  }

  // A forward-auth session lapse: the proxy answered with a cross-origin 3xx,
  // now an opaque redirect. Re-authenticate via a top-level navigation rather
  // than surfacing a bogus "network_error"/CSP failure. The API itself never
  // issues same-origin redirects, so this is unambiguously a proxy challenge.
  if (response.type === "opaqueredirect") {
    reauthenticate();
  }

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

export async function refreshDiscovery(instanceId?: string): Promise<RefreshResponse> {
  return request<RefreshResponse>("/api/v1/discovery/refresh", {
    method: "POST",
    body: instanceId ? JSON.stringify({ instance_id: instanceId }) : undefined,
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
  let response: Response;
  try {
    response = await fetch("/api/v1/ready", { method: "GET" });
  } catch (cause) {
    throw new ApiError({
      code: "network_error",
      message: cause instanceof Error ? cause.message : "Network request failed",
      retryable: true,
      remediation: "Check that the Remo web service is reachable.",
    });
  }
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

export interface MintPairingResponse {
  code: string;
  expires_in: number;
}

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
  let response: Response;
  try {
    response = await fetch(`/api/v1/pairing/mint?origin=${encodeURIComponent(origin)}`, {
      method: "POST",
    });
  } catch (cause) {
    throw new ApiError({
      code: "network_error",
      message: cause instanceof Error ? cause.message : "Network request failed",
      retryable: true,
      remediation: "Check that the Remo web service is reachable.",
    });
  }
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
  // keepalive lets the request outlive the page during unload.
  void fetch(path, { method: "POST", keepalive: true }).catch(() => undefined);
}

// ---- Terminals (T041, US2) ----
//
// Per contracts/rest-api.md and contracts/terminal-websocket.md. The token
// returned by createTerminal() is single-use and MUST travel only via the WS
// subprotocol list (never the URL/query string, FR-049) — see
// openTerminalSocket() below.

export interface CreateTerminalResponse {
  terminal_id: string;
  ws_token: string;
  ws_subprotocol: string;
  expires_in: number;
  state: string;
}

export interface TerminalSummary {
  terminal_id: string;
  session_target_id: string;
  state: string;
  created_at: string;
  last_activity_at: string;
}

export interface ListTerminalsResponse {
  terminals: TerminalSummary[];
}

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
