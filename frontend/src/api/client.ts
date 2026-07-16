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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
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

export interface ReadinessResponse {
  /** true when GET /ready returned 200 (all gating checks pass). */
  ready: boolean;
  status: string;
  checks: Record<string, ReadinessCheck>;
  detail?: string;
}

/**
 * `GET /api/v1/ready` — returns 200 (ready) or 503 (not_ready) but always with
 * a `checks` body. Unlike the other calls this reads the body on BOTH statuses
 * (a 503 is expected config state, not a transport error). A network-level
 * failure rejects with `ApiError{code:"network_error"}` so callers can show the
 * offline overlay.
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
 */
export function openTerminalSocket(terminalId: string, token: string): WebSocket {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${wsProtocol}//${window.location.host}/api/v1/terminals/${encodeURIComponent(terminalId)}`;
  return new WebSocket(url, ["remo-terminal.v1", token]);
}
