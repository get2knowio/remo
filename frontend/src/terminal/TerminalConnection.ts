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
// the SAME still-running remote Zellij session because session_target_id is
// unchanged.

import {
  ApiError,
  closeTerminal as closeTerminalRequest,
  createTerminal,
  openTerminalSocket,
  type TypedError,
} from "../api/client";

export type TerminalConnectionState =
  | "connecting"
  | "ready"
  | "disconnected"
  | "reconnecting"
  | "closed"
  | "error";

interface ControlMessage {
  v: 1;
  type: "ready" | "exit" | "error" | "pong";
  code?: number;
  class?: "auth" | "network" | "remote_capability" | "missing_project" | "remote_launch";
  message?: string;
}

const MAX_AUTO_RECONNECT_ATTEMPTS = 3;
const RECONNECT_BACKOFF_MS = [500, 1500, 3500];

export interface TerminalConnectionCallbacks {
  onData?: (data: Uint8Array) => void;
  onReady?: () => void;
  onExit?: (code: number) => void;
  onError?: (error: TypedError) => void;
  onStateChange?: (state: TerminalConnectionState) => void;
}

/**
 * Owns one terminal's WebSocket across its full life: initial connect,
 * bounded automatic reconnect on unexpected loss, and a manual `reconnect()`
 * fallback once the auto-retry budget is exhausted.
 */
export class TerminalConnection {
  private readonly sessionTargetId: string;
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

  constructor(
    sessionTargetId: string,
    cols: number,
    rows: number,
    callbacks: TerminalConnectionCallbacks = {},
  ) {
    this.sessionTargetId = sessionTargetId;
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

  /** Sends a `resize` control frame; server clamps to safe bounds (FR-060). */
  sendResize(cols: number, rows: number): void {
    this.cols = cols;
    this.rows = rows;
    this.sendControl({ v: 1, type: "resize", cols, rows });
  }

  /** Sends a `ping` control frame (keepalive / liveness probe). */
  sendPing(): void {
    this.sendControl({ v: 1, type: "ping" });
  }

  /** Client-initiated clean close (WS code 1000) plus server-side cleanup. */
  async close(): Promise<void> {
    this.clientInitiatedClose = true;
    this.removeWakeListeners();
    this.clearReconnectTimer();
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

  private sendControl(message: Record<string, unknown>): void {
    if (this.socket?.readyState !== WebSocket.OPEN) {
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
      created = await createTerminal(this.sessionTargetId, this.cols, this.rows);
    } catch (error) {
      this.attaching = false;
      // A newer attach (e.g. a wake-triggered one) has superseded this; stay quiet.
      if (gen === this.attachGen) {
        this.handleFatalError(error);
      }
      return;
    }

    // Superseded while awaiting the fresh terminal — don't open a second socket.
    if (gen !== this.attachGen) {
      this.attaching = false;
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
      this.socket = null;
      if (this.clientInitiatedClose || event.code === 1000) {
        this.setState("closed");
        return;
      }
      void this.handleUnexpectedClose();
    };
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
