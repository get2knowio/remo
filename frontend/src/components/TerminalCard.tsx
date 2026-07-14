// One adapter-backed terminal (T042, US2). Renders a provider/instance/
// project identity header (always visible, per US3 scenario 4), a connection
// state indicator, a renderer surface, and reconnect/close controls
// (FR-032). Standalone-usable given just a `SessionTarget` — Phase 5 (US3)
// wires several of these into a grid/tab workspace around this component.

import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionTarget, TypedError } from "../api/client";
import type { RendererAdapter } from "../terminal/RendererAdapter";
import { GhosttyRenderer } from "../terminal/GhosttyRenderer";
import { TerminalConnection, type TerminalConnectionState } from "../terminal/TerminalConnection";
import "./TerminalCard.css";

/** Factory for the renderer to attach — swappable per FR-036/SC-009 so a
 * fallback to `XtermRenderer` (or any other adapter) is a one-line change
 * with no backend impact. Defaults to `GhosttyRenderer` (spec decision #6). */
export type RendererFactory = () => RendererAdapter;

const DEFAULT_RENDERER_FACTORY: RendererFactory = () => new GhosttyRenderer();

const DEFAULT_COLS = 80;
const DEFAULT_ROWS = 24;

const STATE_LABELS: Record<TerminalConnectionState, string> = {
  connecting: "Connecting…",
  ready: "Connected",
  disconnected: "Disconnected",
  reconnecting: "Reconnecting…",
  closed: "Closed",
  error: "Error",
};

interface TerminalCardProps {
  target: SessionTarget;
  /** Injectable renderer factory; defaults to `GhosttyRenderer`. */
  createRenderer?: RendererFactory;
  /** Called after the user closes this terminal (e.g. to unmount the card). */
  onClose?: () => void;
  /**
   * Gates keyboard-input FORWARDING only (T046/T048, FR-031, US3 scenario
   * 2). A `TerminalCard` may stay mounted (and its `TerminalConnection`
   * connected) while hidden — e.g. in `TabView`'s non-active tabs, or any
   * card that currently isn't the workspace's focused target — so that
   * "hidden terminals remain connected" (US3 scenario 3). What must NOT
   * happen is a hidden/unfocused card silently receiving the user's
   * keystrokes.
   *
   * Design: the renderer adapter (`ghostty-web`/xterm) stays open and its
   * `onData` subscription stays live regardless of focus, so connection
   * keepalive/output rendering is unaffected — only the forward-to-server
   * step (`connection.sendInput`) is conditional. The mount effect below
   * only runs once per `target.id` (see its dependency array), so
   * `isFocused` is read through a ref updated on every prop change rather
   * than being a dependency itself — that avoids tearing down and
   * recreating the terminal connection every time focus changes.
   *
   * Defaults to `true` so this component stays a fully standalone,
   * always-forwarding terminal when used outside a multi-terminal workspace
   * (e.g. in isolation/tests), matching its pre-T046 behavior.
   */
  isFocused?: boolean;
  /**
   * Called when the user clicks into this card's terminal surface. In
   * `GridView` (T046) every card is simultaneously visible, so clicking
   * directly into one is the natural "type here now" gesture — the caller
   * (GridView/TabView) wires this to `workspace.setFocused(target.id)` so
   * click-to-focus and the keyboard-cycling shortcut (T048) share the same
   * `focusedTargetId` source of truth.
   */
  onFocusRequest?: () => void;
}

export function TerminalCard({
  target,
  createRenderer,
  onClose,
  isFocused = true,
  onFocusRequest,
}: TerminalCardProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const adapterRef = useRef<RendererAdapter | null>(null);
  const connectionRef = useRef<TerminalConnection | null>(null);
  // Guards against React StrictMode's dev-mode double-invoke of effects,
  // which would otherwise open two terminals for one card.
  const createdRef = useRef(false);
  // Read inside the input handler below instead of being a mount-effect
  // dependency, so toggling focus never tears down/recreates the terminal
  // connection (see the `isFocused` prop doc above).
  const isFocusedRef = useRef(isFocused);

  const [connectionState, setConnectionState] = useState<TerminalConnectionState>("connecting");
  const [needsManualReconnect, setNeedsManualReconnect] = useState(false);
  const [error, setError] = useState<TypedError | null>(null);

  useEffect(() => {
    isFocusedRef.current = isFocused;
  }, [isFocused]);

  useEffect(() => {
    if (createdRef.current) {
      return undefined;
    }
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    createdRef.current = true;

    const adapter = (createRenderer ?? DEFAULT_RENDERER_FACTORY)();
    adapterRef.current = adapter;
    adapter.open(container);

    const connection = new TerminalConnection(target.id, DEFAULT_COLS, DEFAULT_ROWS, {
      onData: (data) => adapter.write(data),
      onReady: () => setError(null),
      onError: (typedError) => setError(typedError),
      onStateChange: (state) => {
        setConnectionState(state);
        setNeedsManualReconnect(connectionRef.current?.needsManualReconnect ?? false);
      },
    });
    connectionRef.current = connection;

    const unsubscribeInput = adapter.onData((data) => {
      // Gate on the ref, not the `isFocused` prop directly — this closure is
      // created once (mount effect keyed on target.id only) and must see
      // up-to-date focus without re-running the whole connect/dispose cycle.
      if (isFocusedRef.current) {
        connection.sendInput(data);
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      // A hidden pane (TabView's non-active tabs use `display: none`) collapses
      // to 0x0. Measuring + resizing then would compute a 1x1 grid and shrink
      // the REMOTE PTY to a single column, corrupting a backgrounded Zellij/TUI
      // layout. Skip while not visible; the observer fires again (with real
      // dimensions) when the pane is shown, and `ready` re-syncs the size too.
      if (container.clientWidth === 0 || container.clientHeight === 0) {
        return;
      }
      const dims = adapter.fit();
      connection.sendResize(dims.cols, dims.rows);
    });
    resizeObserver.observe(container);

    void connection.connect();

    return () => {
      createdRef.current = false;
      unsubscribeInput();
      resizeObserver.disconnect();
      void connection.close();
      adapter.dispose();
      adapterRef.current = null;
      connectionRef.current = null;
    };
    // Intentionally keyed on target.id only: this card owns exactly one
    // terminal for its lifetime, and createRenderer is a factory the caller
    // is expected to keep stable (or accept re-creation is not desired mid-
    // life — swapping renderers is a deploy-time choice, not a live one).
  }, [target.id]);

  const handleReconnect = useCallback(() => {
    setError(null);
    void connectionRef.current?.reconnect();
  }, []);

  const handleClose = useCallback(() => {
    void connectionRef.current?.close();
    onClose?.();
  }, [onClose]);

  const handleFocusSurface = useCallback(() => {
    adapterRef.current?.focus();
    onFocusRequest?.();
  }, [onFocusRequest]);

  return (
    <div
      className={`terminal-card${isFocused ? " terminal-card--focused" : ""}`}
      data-testid={`terminal-card-${target.id}`}
      data-focused={isFocused}
      data-connection-state={connectionState}
    >
      <header className="terminal-card-header">
        <div className="terminal-card-identity">
          <span className="terminal-card-instance">
            {target.instance_type} / {target.instance_name}
          </span>
          <span className="terminal-card-project">{target.project}</span>
        </div>
        <div className="terminal-card-controls">
          <span className={`terminal-card-state terminal-card-state--${connectionState}`}>
            {STATE_LABELS[connectionState]}
          </span>
          {needsManualReconnect && (
            <button
              type="button"
              className="terminal-card-reconnect-button"
              data-testid={`terminal-reconnect-${target.id}`}
              onClick={handleReconnect}
            >
              Reconnect
            </button>
          )}
          <button
            type="button"
            className="terminal-card-close-button"
            data-testid={`terminal-close-${target.id}`}
            onClick={handleClose}
          >
            Close
          </button>
        </div>
      </header>

      {error && (
        <div className="terminal-card-error">
          <p className="terminal-card-error-message">
            [{error.code}] {error.message}
          </p>
          {error.remediation && (
            <p className="terminal-card-error-remediation">{error.remediation}</p>
          )}
          {error.retryable && (
            <button type="button" className="terminal-card-retry-button" onClick={handleReconnect}>
              Retry
            </button>
          )}
        </div>
      )}

      <div
        ref={containerRef}
        className="terminal-card-surface"
        data-testid={`terminal-surface-${target.id}`}
        onClick={handleFocusSurface}
      />
    </div>
  );
}
