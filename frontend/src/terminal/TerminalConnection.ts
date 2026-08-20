// Higher-level per-terminal lifecycle wrapper (T041, US2).
//
// Wraps one terminal's full lifecycle: createTerminal() -> openTerminalSocket()
// -> binary/JSON frame handling -> bounded auto-reconnect -> manual fallback
// (Clarifications Q2, FR-020). `client.ts`'s `openTerminalSocket()` stays a
// thin WebSocket constructor; all state-machine/control-frame logic lives
// here so `TerminalCard.tsx` (T042) only deals with a small typed surface.
//
// Reconnect is never a resume of the closed socket (contracts/
// terminal-websocket.md): every retry — automatic or manual — calls
// createTerminal() again for a brand-new terminal_id + token, attaching to
// the SAME still-running remote Zellij session (or host shell) because the
// origin — session_target_id / instance_id — is unchanged.

import {
  ApiError,
  asTerminalOrigin,
  closeTerminal as closeTerminalRequest,
  createTerminal,
  openTerminalSocket,
  type TerminalOrigin,
  type TypedError,
} from "../api/client";
// Generated from the service-side `remo-terminal.v1` frame contract
// (src/remo_cli/web/frames.py -> frontend/src/api/generated/terminal-frames.json
// -> terminal-frames.d.ts). Regenerate with `npm run generate:types`; see
// docs/maintaining-generated-types.md. This is the service->browser
// direction, which is what handleControlMessage() below parses.
import type { components } from "../api/generated/terminal-frames";

export type TerminalConnectionState =
  | "connecting"
  | "ready"
  | "disconnected"
  | "reconnecting"
  | "closed"
  | "error";

type ControlMessage = components["schemas"]["OutboundFrame"];

const MAX_AUTO_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BACKOFF_MS = [500, 1500, 3500];
/** How often to ping the WS to measure round-trip latency (also a keepalive). */
const PING_INTERVAL_MS = 4000;
/**
 * Delays (ms) at which a just-sent resize is re-asserted.
 *
 * A resize reaches the remote as exactly ONE SIGWINCH: the service applies the
 * winsize to its PTY and signals the child unconditionally (web/terminal.py
 * `TerminalSession.resize`), and `ssh` only forwards a window-change when it
 * catches that signal. Everything below — ssh, the remote PTY, Zellij, and the
 * `docker exec` TTY that `devcontainer exec` runs Claude Code on — has to be
 * listening at that instant. If any of them is mid-startup or busy, the signal
 * is lost, and NOTHING regenerates it: `TerminalCard` sends a given grid only
 * once (it skips a fit whose cols/rows are unchanged), so the remote stays
 * pinned at the stale size until the operator forces a *different* one by hand.
 * That is the "maximize and restore to bring the prompt back" workaround —
 * observed as `stty size` reporting 67 rows against a panel with room for 59.
 *
 * Re-asserting is safe and idempotent in both directions: the service signals
 * on every resize frame regardless of whether the size changed, so an unchanged
 * size still produces the SIGWINCH a dropped one never will, while a remote
 * that is already correct simply redraws to identical output. Bounded on
 * purpose — each re-assert costs a remote redraw, so this buys convergence
 * after a resize without any steady-state churn.
 */
const RESIZE_REASSERT_DELAYS_MS = [400, 1200, 3000];

/**
 * Read-only connection facts for the console's diagnostics snapshot
 * (state/diagnostics.ts).
 *
 * REDACTION CONTRACT: the socket is reported by `readyState`/`bufferedAmount`
 * ONLY. Its `url` carries the terminal id and its `protocol` carries the WS
 * token (the auth token rides as a subprotocol value — see
 * `openTerminalSocket`), so neither may ever appear here. `CloseEvent.reason`
 * is server-authored text and is kept, truncated.
 */
export interface ConnectionDiagnostics {
  state: TerminalConnectionState;
  needsManualReconnect: boolean;
  reconnectAttempts: number;
  /** The last grid handed to `sendResize` — what the remote SHOULD be on,
   * whether or not the frame actually reached the socket. */
  lastSentGrid: { cols: number; rows: number };
  lastClose: { code: number; reason: string; at: string } | null;
  /** Control frames dropped because the socket was not OPEN. Non-zero after a
   * normal startup race (the `ready` handler re-sends the size); a growing
   * count means frames are being lost against a live-looking connection. */
  droppedControlFrames: number;
  socket: { readyState: number; bufferedAmount: number } | null;
}

/** Longest server-authored close reason retained in a snapshot. */
const MAX_CLOSE_REASON_CHARS = 120;

export interface TerminalConnectionCallbacks {
  onData?: (data: Uint8Array) => void;
  onReady?: () => void;
  onExit?: (code: number) => void;
  onError?: (error: TypedError) => void;
  onStateChange?: (state: TerminalConnectionState) => void;
  /** WS round-trip time in ms, from each ping→pong while connected. */
  onLatency?: (rttMs: number) => void;
}

/**
 * Owns one terminal's WebSocket across its full life: initial connect,
 * bounded automatic reconnect on unexpected loss, and a manual `reconnect()`
 * fallback once the auto-retry budget is exhausted.
 */
export class TerminalConnection {
  private readonly origin: TerminalOrigin;
  private cols: number;
  private rows: number;
  private readonly callbacks: TerminalConnectionCallbacks;

  private socket: WebSocket | null = null;
  private terminalId: string | null = null;
  private state: TerminalConnectionState = "connecting";
  private clientInitiatedClose = false;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  private _needsManualReconnect = false;
  /** True while an attach() is between requesting a fresh terminal and having a
   * socket assigned — serializes the burst of wake events (visibilitychange +
   * focus + online all fire near-simultaneously on resume). */
  private attaching = false;
  /** Bumped each attach() so a slow, superseded attach can't assign a socket. */
  private attachGen = 0;
  private wakeListenersBound = false;
  private pingTimer: ReturnType<typeof setInterval> | undefined;
  /** performance.now() when the outstanding ping was sent (null if none). */
  private pingSentAt: number | null = null;
  /** Pending re-asserts of the last-sent size (RESIZE_REASSERT_DELAYS_MS). */
  private resizeReassertTimers: Array<ReturnType<typeof setTimeout>> = [];
  /** Last close code/reason, retained for diagnostics — the handler itself
   * only branches on the code, so without this it is read and thrown away. */
  private lastClose: { code: number; reason: string; at: string } | null = null;
  /** Control frames sendControl dropped because the socket was not OPEN. */
  private droppedControlFrames = 0;

  /**
   * `origin` is what the terminal attaches to: a bare string is the session
   * shorthand (`{kind: "session", sessionTargetId}` — every pre-existing call
   * site unchanged), `{kind: "host_shell", instanceId}` is a plain login shell
   * on the host itself (host-admin-gated).
   */
  constructor(
    origin: string | TerminalOrigin,
    cols: number,
    rows: number,
    callbacks: TerminalConnectionCallbacks = {},
  ) {
    this.origin = asTerminalOrigin(origin);
    this.cols = cols;
    this.rows = rows;
    this.callbacks = callbacks;
  }

  get currentState(): TerminalConnectionState {
    return this.state;
  }

  get needsManualReconnect(): boolean {
    return this._needsManualReconnect;
  }

  /** Starts the initial connection. Call once after construction. */
  async connect(): Promise<void> {
    this.addWakeListeners();
    await this.attach("connecting");
  }

  /** User-triggered reconnect after the auto-retry budget is exhausted. */
  async reconnect(): Promise<void> {
    this._needsManualReconnect = false;
    this.reconnectAttempts = 0;
    await this.attach("reconnecting");
  }

  /** Sends terminal input (keystrokes/paste) as a binary WS frame. */
  sendInput(data: Uint8Array | string): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      return;
    }
    this.socket.send(typeof data === "string" ? new TextEncoder().encode(data) : data);
  }

  /** Sends a `resize` control frame; server clamps to safe bounds (FR-060).
   *
   * Also schedules the bounded re-assert burst (RESIZE_REASSERT_DELAYS_MS), so
   * a SIGWINCH dropped by a busy or still-starting remote chain gets further
   * chances instead of leaving the remote pinned at the stale size forever.
   */
  sendResize(cols: number, rows: number): void {
    this.cols = cols;
    this.rows = rows;
    this.sendControl({ v: 1, type: "resize", cols, rows });
    this.scheduleResizeReasserts();
  }

  /**
   * Re-send the current dims without scheduling another burst.
   *
   * Deliberately NOT routed through `sendResize`: that would re-arm the timers
   * and the burst would never terminate.
   */
  reassertSize(): void {
    if (this.cols <= 0 || this.rows <= 0) {
      return;
    }
    this.sendControl({ v: 1, type: "resize", cols: this.cols, rows: this.rows });
  }

  private scheduleResizeReasserts(): void {
    // A window drag calls this many times; only the final size deserves a
    // burst, so each call replaces the pending one rather than stacking.
    this.clearResizeReasserts();
    this.resizeReassertTimers = RESIZE_REASSERT_DELAYS_MS.map((delay) =>
      setTimeout(() => this.reassertSize(), delay),
    );
  }

  private clearResizeReasserts(): void {
    for (const timer of this.resizeReassertTimers) {
      clearTimeout(timer);
    }
    this.resizeReassertTimers = [];
  }

  /** Sends a `ping` control frame (keepalive / liveness probe). */
  sendPing(): void {
    this.sendControl({ v: 1, type: "ping" });
  }

  /** Client-initiated clean close (WS code 1000) plus server-side cleanup. */
  async close(): Promise<void> {
    this.clientInitiatedClose = true;
    // Supersede any in-flight attach(): without this, an awaited
    // createTerminal() resolving after close() would pass the gen check and
    // open a brand-new socket on a connection the caller just tore down.
    this.attachGen += 1;
    this.attaching = false;
    this.removeWakeListeners();
    this.stopPinging();
    this.clearReconnectTimer();
    this.clearResizeReasserts();
    const socket = this.socket;
    const terminalId = this.terminalId;
    this.socket = null;
    if (socket && socket.readyState <= WebSocket.OPEN) {
      socket.close(1000, "client close");
    }
    this.setState("closed");
    if (terminalId) {
      try {
        await closeTerminalRequest(terminalId);
      } catch {
        // Best-effort cleanup — the server also reaps on WS close/timeout.
      }
    }
  }

  /** Read-only snapshot for the diagnostics blob. See ConnectionDiagnostics
   * for what is deliberately absent (socket url/protocol carry the token). */
  diagnostics(): ConnectionDiagnostics {
    return {
      state: this.state,
      needsManualReconnect: this._needsManualReconnect,
      reconnectAttempts: this.reconnectAttempts,
      lastSentGrid: { cols: this.cols, rows: this.rows },
      lastClose: this.lastClose,
      droppedControlFrames: this.droppedControlFrames,
      socket: this.socket
        ? { readyState: this.socket.readyState, bufferedAmount: this.socket.bufferedAmount }
        : null,
    };
  }

  private sendControl(message: Record<string, unknown>): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.droppedControlFrames += 1;
      return;
    }
    this.socket.send(JSON.stringify(message));
  }

  private setState(state: TerminalConnectionState): void {
    this.state = state;
    this.callbacks.onStateChange?.(state);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer !== undefined) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = undefined;
    }
  }

  private addWakeListeners(): void {
    if (this.wakeListenersBound) {
      return;
    }
    this.wakeListenersBound = true;
    if (typeof window !== "undefined") {
      window.addEventListener("online", this.onWake);
      window.addEventListener("focus", this.onWake);
    }
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", this.onWake);
    }
  }

  private removeWakeListeners(): void {
    if (!this.wakeListenersBound) {
      return;
    }
    this.wakeListenersBound = false;
    if (typeof window !== "undefined") {
      window.removeEventListener("online", this.onWake);
      window.removeEventListener("focus", this.onWake);
    }
    if (typeof document !== "undefined") {
      document.removeEventListener("visibilitychange", this.onWake);
    }
  }

  // Regaining focus/visibility/connectivity (e.g. reopening a slept laptop lid)
  // is our cue that a socket that died while backgrounded should recover NOW,
  // rather than waiting out a backoff that may have already been exhausted while
  // hidden. Reset the auto-retry budget and force a fresh attach.
  private readonly onWake = (): void => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      return; // visibilitychange firing on the way OUT — ignore.
    }
    if (this.clientInitiatedClose || this.state === "closed" || this.attaching) {
      return;
    }
    const readyState = this.socket?.readyState;
    if (readyState === WebSocket.OPEN) {
      // Looks alive; prod it so a silently-dead (post-sleep) socket surfaces an
      // onclose and takes the reconnect path. Best-effort — a throw here just
      // means the close is imminent anyway.
      try {
        this.sendPing();
      } catch {
        /* dead socket; onclose will drive the reconnect */
      }
      return;
    }
    if (readyState === WebSocket.CONNECTING) {
      return; // a handshake is already in flight.
    }
    this._needsManualReconnect = false;
    this.reconnectAttempts = 0;
    this.clearReconnectTimer();
    void this.attach("reconnecting");
  };

  /** Creates a fresh terminal_id + token and opens a new WS to it. */
  private async attach(nextState: "connecting" | "reconnecting"): Promise<void> {
    const gen = ++this.attachGen;
    this.attaching = true;
    this.clientInitiatedClose = false;
    this.setState(nextState);

    let created;
    try {
      created = await createTerminal(this.origin, this.cols, this.rows);
    } catch (error) {
      this.attaching = false;
      // A newer attach (e.g. a wake-triggered one) has superseded this; stay quiet.
      if (gen === this.attachGen) {
        this.handleFatalError(error);
      }
      return;
    }

    // Superseded while awaiting the fresh terminal — don't open a second
    // socket. Whether the superseder was close() or a newer attach (which
    // created its own terminal), THIS terminal will never be attached: reap
    // it server-side now rather than waiting for the idle sweep.
    if (gen !== this.attachGen) {
      this.attaching = false;
      void closeTerminalRequest(created.terminal_id).catch(() => {
        // Best-effort — the server also reaps never-attached terminals.
      });
      return;
    }

    this.terminalId = created.terminal_id;
    const socket = openTerminalSocket(created.terminal_id, created.ws_token);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    this.attaching = false;

    socket.onopen = () => {
      // Server confirms readiness via the `ready` control frame, not onopen.
    };

    socket.onmessage = (event: MessageEvent) => {
      if (typeof event.data === "string") {
        this.handleControlMessage(event.data);
      } else {
        const bytes =
          event.data instanceof ArrayBuffer ? new Uint8Array(event.data) : new Uint8Array();
        this.callbacks.onData?.(bytes);
      }
    };

    socket.onerror = () => {
      // Actual failure detail (if any) arrives via onclose's code/reason or a
      // preceding `error` control frame; nothing actionable here alone.
    };

    socket.onclose = (event: CloseEvent) => {
      // Retained before the branching below, which otherwise consumes the code
      // and discards the reason — the two facts a "it just disconnected" report
      // needs most.
      this.lastClose = {
        code: event.code,
        reason: (event.reason ?? "").slice(0, MAX_CLOSE_REASON_CHARS),
        at: new Date().toISOString(),
      };
      this.socket = null;
      this.stopPinging();
      if (this.clientInitiatedClose || event.code === 1000) {
        this.setState("closed");
        return;
      }
      void this.handleUnexpectedClose();
    };
  }

  /** Begin (or restart) the latency ping loop; call once per fresh connection. */
  private startPinging(): void {
    this.stopPinging();
    const sendMeasuredPing = (): void => {
      if (this.socket?.readyState !== WebSocket.OPEN) {
        return;
      }
      this.pingSentAt = performance.now();
      this.sendPing();
    };
    sendMeasuredPing(); // one immediately, for a fast first sample
    this.pingTimer = setInterval(sendMeasuredPing, PING_INTERVAL_MS);
  }

  private stopPinging(): void {
    if (this.pingTimer !== undefined) {
      clearInterval(this.pingTimer);
      this.pingTimer = undefined;
    }
    this.pingSentAt = null;
  }

  private handleControlMessage(raw: string): void {
    let message: ControlMessage;
    try {
      message = JSON.parse(raw) as ControlMessage;
    } catch {
      return;
    }

    switch (message.type) {
      case "ready":
        this.reconnectAttempts = 0;
        this.setState("ready");
        // The PTY was spawned at the dims from the POST /terminals body (the
        // initial 80x24 default). Any fit()-driven resize the card sent before
        // the socket reached OPEN was silently dropped by sendControl's
        // readyState guard. `this.cols/this.rows` still track the latest
        // fit() dims (sendResize updates them even when the frame is dropped),
        // so re-send them now to size the remote terminal to the real surface.
        this.sendControl({ v: 1, type: "resize", cols: this.cols, rows: this.rows });
        this.callbacks.onReady?.();
        this.startPinging();
        break;
      case "exit":
        this.callbacks.onExit?.(message.code ?? 0);
        break;
      case "error":
        this.callbacks.onError?.({
          code: message.class ?? "unknown",
          message: message.message ?? "Terminal error",
          retryable: message.class !== "missing_project",
          remediation: "",
        });
        this.setState("error");
        break;
      case "pong":
        if (this.pingSentAt !== null) {
          const rtt = performance.now() - this.pingSentAt;
          this.pingSentAt = null;
          this.callbacks.onLatency?.(rtt);
        }
        break;
    }
  }

  private handleFatalError(error: unknown): void {
    const typedError: TypedError =
      error instanceof ApiError
        ? { code: error.code, message: error.message, retryable: error.retryable, remediation: error.remediation }
        : {
            code: "unknown",
            message: error instanceof Error ? error.message : "Failed to create terminal",
            retryable: true,
            remediation: "",
          };
    this.callbacks.onError?.(typedError);
    this.setState("error");
  }

  private async handleUnexpectedClose(): Promise<void> {
    if (this.reconnectAttempts >= MAX_AUTO_RECONNECT_ATTEMPTS) {
      this._needsManualReconnect = true;
      this.setState("disconnected");
      return;
    }

    const delay =
      RECONNECT_BACKOFF_MS[this.reconnectAttempts] ??
      RECONNECT_BACKOFF_MS[RECONNECT_BACKOFF_MS.length - 1];
    this.reconnectAttempts += 1;
    this.setState("reconnecting");

    this.clearReconnectTimer();
    await new Promise<void>((resolve) => {
      this.reconnectTimer = setTimeout(resolve, delay);
    });

    if (this.clientInitiatedClose || this.attaching || this.socket) {
      // A wake-triggered reconnect already took over while we were backing off.
      return;
    }
    await this.attach("reconnecting");
  }
}
