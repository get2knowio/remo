// One adapter-backed terminal (US2/US3), styled as the console's single-view
// terminal or a grid tile depending on `mode`. Owns exactly one
// `TerminalConnection` + `RendererAdapter` for its lifetime; stays mounted even
// when hidden (parent toggles `isVisible`) so the SSH connection and browser
// scrollback survive (US3 scenario 3).
//
// Structural invariant: the `.terminal-card-surface` div is ALWAYS the last
// child at the same tree position regardless of `mode`, so switching
// single↔grid only re-renders the header chrome and never remounts the
// terminal surface (which would tear down the live connection).

import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionTarget, TypedError } from "../api/client";
import { providerMeta } from "./providerMeta";
import {
  terminalFontOptions,
  useSettings,
  type SettingsState,
  type TerminalFontOptions,
} from "../state/settings";
import type { RendererAdapter } from "../terminal/RendererAdapter";
import { createDefaultRenderer } from "../terminal/defaultRenderer";
import { TerminalConnection, type TerminalConnectionState } from "../terminal/TerminalConnection";
import "./TerminalCard.css";

const DEFAULT_COLS = 80;
const DEFAULT_ROWS = 24;
/** How much to shrink the terminal font in a grid tile when "scale to fit". */
const GRID_FIT_SCALE = 0.8;

const STATE_LABELS: Record<TerminalConnectionState, string> = {
  connecting: "Connecting…",
  ready: "Connected",
  disconnected: "Disconnected",
  reconnecting: "Reconnecting…",
  closed: "Closed",
  error: "Error",
};

export type TerminalCardMode = "single" | "grid";

interface TerminalCardProps {
  target: SessionTarget;
  /** Registry region for this target's instance (badge only). */
  region?: string;
  mode: TerminalCardMode;
  /** Whether this card is shown in the pane; hidden cards stay mounted. */
  isVisible: boolean;
  /** Whether this card currently receives keyboard input + the focus ring. */
  isFocused: boolean;
  /** 1-based position label shown on a grid tile. */
  num?: number;
  onClose: () => void;
  /** Grid tile clicked → solo it (single view). */
  onSolo?: () => void;
  /** Single view "Back to grid" — omitted when there's no grid to return to. */
  onBackToGrid?: () => void;
  /** Called when the user clicks into the surface (focus this terminal). */
  onFocusRequest?: () => void;
  /** Called when output arrives while this card is hidden (rail activity dot). */
  onActivity?: () => void;
  /** Called when the remote process exits (session may have ended) or the
   * terminal is closed — the caller should re-run discovery for this instance
   * so the rail's live Zellij/git state stops being stale. */
  onEnded?: () => void;
}

function effectiveFont(settings: SettingsState, mode: TerminalCardMode): TerminalFontOptions {
  const base = terminalFontOptions(settings);
  if (mode === "grid" && settings.gridFit) {
    return { ...base, fontSize: Math.max(9, Math.round(base.fontSize * GRID_FIT_SCALE)) };
  }
  return base;
}

export function TerminalCard({
  target,
  region,
  mode,
  isVisible,
  isFocused,
  num,
  onClose,
  onSolo,
  onBackToGrid,
  onFocusRequest,
  onActivity,
  onEnded,
}: TerminalCardProps): JSX.Element {
  const settings = useSettings();

  const containerRef = useRef<HTMLDivElement | null>(null);
  const adapterRef = useRef<RendererAdapter | null>(null);
  const connectionRef = useRef<TerminalConnection | null>(null);
  const createdRef = useRef(false);
  // Read inside handlers so toggling focus/visibility never tears down the
  // connection (mount effect is keyed on target.id only).
  const isFocusedRef = useRef(isFocused);
  const isVisibleRef = useRef(isVisible);
  const onActivityRef = useRef(onActivity);
  const onEndedRef = useRef(onEnded);
  const fontRef = useRef<TerminalFontOptions>(effectiveFont(settings, mode));

  const [connectionState, setConnectionState] = useState<TerminalConnectionState>("connecting");
  const [needsManualReconnect, setNeedsManualReconnect] = useState(false);
  const [error, setError] = useState<TypedError | null>(null);

  useEffect(() => {
    isFocusedRef.current = isFocused;
  }, [isFocused]);
  useEffect(() => {
    isVisibleRef.current = isVisible;
  }, [isVisible]);
  useEffect(() => {
    onActivityRef.current = onActivity;
  }, [onActivity]);
  useEffect(() => {
    onEndedRef.current = onEnded;
  }, [onEnded]);

  // Apply live font/size/ligature changes (and grid-fit scaling) to the open
  // terminal, then re-fit and tell the server the new dimensions.
  const font = effectiveFont(settings, mode);
  useEffect(() => {
    fontRef.current = font;
    const adapter = adapterRef.current;
    const connection = connectionRef.current;
    const container = containerRef.current;
    if (!adapter || !connection) {
      return;
    }
    adapter.applyFont(font);
    // Only re-fit/resize when actually visible: a hidden card collapses to
    // 0x0, and fitting then would shrink the remote PTY to 1x1 and corrupt a
    // backgrounded TUI. The ResizeObserver re-fits (with real dimensions) when
    // the card is shown again, so applyFont alone is enough while hidden.
    if (container && container.clientWidth > 0 && container.clientHeight > 0) {
      const dims = adapter.fit();
      connection.sendResize(dims.cols, dims.rows);
    }
    // font is a fresh object each render; compare by its fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [font.fontFamily, font.fontSize, font.ligatures]);

  useEffect(() => {
    if (createdRef.current) {
      return undefined;
    }
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    createdRef.current = true;

    const adapter = createDefaultRenderer(fontRef.current, settings.renderer);
    adapterRef.current = adapter;
    adapter.open(container);

    const connection = new TerminalConnection(target.id, DEFAULT_COLS, DEFAULT_ROWS, {
      onData: (data) => {
        adapter.write(data);
        if (!isVisibleRef.current) {
          onActivityRef.current?.();
        }
      },
      onReady: () => setError(null),
      onExit: () => {
        // The remote process exited — the Zellij session may have ended (e.g.
        // the user quit Zellij). Re-run discovery so the rail's ⚡/git state
        // reflects reality instead of the now-stale cache.
        onEndedRef.current?.();
      },
      onError: (typedError) => setError(typedError),
      onStateChange: (state) => {
        setConnectionState(state);
        setNeedsManualReconnect(connectionRef.current?.needsManualReconnect ?? false);
      },
    });
    connectionRef.current = connection;

    const unsubscribeInput = adapter.onData((data) => {
      if (isFocusedRef.current) {
        connection.sendInput(data);
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      // A hidden pane collapses to 0x0; measuring then would shrink the remote
      // PTY to 1x1 and corrupt a backgrounded TUI. Skip while not visible; the
      // observer fires again with real dimensions when shown, and `ready`
      // re-syncs size too.
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
    // Keyed on target.id (+ renderer engine): this card owns exactly one
    // terminal for its lifetime (see file header). Flipping the engine in
    // Settings intentionally tears down and rebuilds the terminal with the
    // chosen renderer, reconnecting to the same remote Zellij session.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id, settings.renderer]);

  const handleReconnect = useCallback(() => {
    setError(null);
    void connectionRef.current?.reconnect();
  }, []);

  const handleClose = useCallback(() => {
    void connectionRef.current?.close();
    onClose();
    // Closing the card is also a good moment to re-check the instance: the
    // user may have quit/detached the session before closing.
    onEndedRef.current?.();
  }, [onClose]);

  const handleFocusSurface = useCallback(() => {
    adapterRef.current?.focus();
    onFocusRequest?.();
  }, [onFocusRequest]);

  const prov = providerMeta(target.instance_type);
  const badge = [prov.label, target.instance_name, region].filter(Boolean).join(" · ");

  // Grid tile: clicking anywhere on the tile (outside the buttons) solos it.
  const handleTileClick = mode === "grid" ? onSolo : undefined;

  return (
    <div
      className={`terminal-card terminal-card--${mode}${isFocused ? " terminal-card--focused" : ""}`}
      data-testid={`terminal-card-${target.id}`}
      data-focused={isFocused}
      data-connection-state={connectionState}
      style={{ display: isVisible ? undefined : "none" }}
      onClick={handleTileClick}
    >
      <header className="terminal-card-header">
        {mode === "grid" && num !== undefined && (
          <span className="terminal-card-num">{num}</span>
        )}
        <span className="terminal-card-provider-dot" style={{ background: prov.color }} />
        <div className="terminal-card-identity">
          <span className="terminal-card-project">{target.project}</span>
          {mode === "single" ? (
            <span className="terminal-card-badge">{badge}</span>
          ) : (
            <span className="terminal-card-instance">{target.instance_name}</span>
          )}
        </div>
        <span
          className={`terminal-card-state terminal-card-state--${connectionState}`}
          title={STATE_LABELS[connectionState]}
        >
          {STATE_LABELS[connectionState]}
        </span>
        <div className="terminal-card-controls">
          {mode === "single" && onBackToGrid && (
            <button
              type="button"
              className="tc-btn"
              title="Return to the grid you came from"
              onClick={(e) => {
                e.stopPropagation();
                onBackToGrid();
              }}
            >
              ⊞ Grid
            </button>
          )}
          {needsManualReconnect && (
            <button
              type="button"
              className="tc-btn tc-btn--accent"
              data-testid={`terminal-reconnect-${target.id}`}
              onClick={(e) => {
                e.stopPropagation();
                handleReconnect();
              }}
            >
              ↻ Reconnect
            </button>
          )}
          <button
            type="button"
            className="tc-btn tc-btn--close"
            data-testid={`terminal-close-${target.id}`}
            title="Close terminal — remote Zellij session stays alive"
            onClick={(e) => {
              e.stopPropagation();
              handleClose();
            }}
          >
            {mode === "grid" ? "✕" : "Close"}
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
            <button
              type="button"
              className="tc-btn tc-btn--accent"
              onClick={(e) => {
                e.stopPropagation();
                handleReconnect();
              }}
            >
              Retry
            </button>
          )}
        </div>
      )}

      <div
        ref={containerRef}
        className="terminal-card-surface"
        data-testid={`terminal-surface-${target.id}`}
        onClick={(e) => {
          e.stopPropagation();
          handleFocusSurface();
        }}
      />
    </div>
  );
}
