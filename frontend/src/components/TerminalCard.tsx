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

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useDraggable, useDroppable } from "@dnd-kit/core";
import type { SessionTarget, TypedError } from "../api/client";
import { providerMeta } from "./providerMeta";
import {
  effectiveTerminalTheme,
  settingsActions,
  terminalThemeLabel,
  terminalFontOptions,
  useSettings,
  type SettingsState,
  type TerminalFontOptions,
} from "../state/settings";
import {
  TERMINAL_THEMES,
  type TerminalThemeColors,
} from "../theme/terminalThemes";
import type { MasterSide } from "../state/workspace";
import { removeLatency, reportLatency } from "../state/latency";
import type { RendererAdapter } from "../terminal/RendererAdapter";
import { createDefaultRenderer } from "../terminal/defaultRenderer";
import {
  TerminalConnection,
  type TerminalConnectionState,
} from "../terminal/TerminalConnection";
import "./TerminalCard.css";

const DEFAULT_COLS = 80;
const DEFAULT_ROWS = 24;
/** How much to shrink the terminal font in a grid tile when "scale to fit". */
const GRID_FIT_SCALE = 0.8;

/** Glyph + label per tiling state. The glyph is a half block drawing where the
 * master area sits, so the button reads as a picture of the layout. */
const MASTER_GLYPH: Record<
  MasterSide | "grid",
  { glyph: string; title: string }
> = {
  grid: { glyph: "▦", title: "Tile this terminal to the left" },
  left: { glyph: "▌", title: "Mastering the left — tile to the top" },
  top: { glyph: "▀", title: "Mastering the top — tile to the right" },
  right: { glyph: "▐", title: "Mastering the right — tile to the bottom" },
  bottom: { glyph: "▄", title: "Mastering the bottom — back to the even grid" },
};

const STATE_LABELS: Record<TerminalConnectionState, string> = {
  connecting: "Connecting…",
  ready: "Connected",
  disconnected: "Disconnected",
  reconnecting: "Reconnecting…",
  closed: "Closed",
  error: "Error",
};

export type TerminalCardMode = "single" | "grid";
/** Which of the mutually-exclusive display modes this card is currently in.
 * Drives the window-control cluster's active/disabled state. */
export type TerminalViewState = "normal" | "grid" | "fullscreen";

interface TerminalCardProps {
  target: SessionTarget;
  /** Registry region for this target's instance (badge only). */
  region?: string;
  mode: TerminalCardMode;
  /** Whether this card is shown in the pane; hidden cards stay mounted. */
  isVisible: boolean;
  /** Whether this card currently receives keyboard input + the focus ring. */
  isFocused: boolean;
  /** The display mode this card is currently in (window-control cluster state). */
  viewState: TerminalViewState;
  onClose: () => void;
  /** When true, the header acts as a dnd-kit drag handle and the tile is a drop
   * target (grid reorder). The DndContext + swap live in WorkspacePane. */
  reorderEnabled?: boolean;
  /** This tile's slot in a master/stack tiling, as a CSS `grid-area`. Undefined
   * in the uniform grid, where tiles auto-place exactly as they always have.
   * Applied to the card's OWN root element — never a wrapper, see WorkspacePane. */
  gridArea?: string;
  /** Which side this tile masters, or null when it is a plain stack tile.
   * Undefined hides the tiling control entirely (single view, fullscreen). */
  masterSide?: MasterSide | null;
  /** Advance this tile through the tiling cycle (left -> top -> right -> bottom
   * -> uniform grid). */
  onCycleTile?: () => void;
  /** Window-control "Normal": solo this terminal into the single view. */
  onNormal: () => void;
  /** Window-control "Grid": show the grid, or — when the grid is already shown
   * but tiled — flatten it back to even tiles. Omitted when neither applies. */
  onGrid?: () => void;
  /** Overrides the ⊞ control's label when it does something other than "show
   * the grid" (i.e. when it will flatten a tiling). */
  gridTitle?: string;
  /** Window-control "Fullscreen": enter fullscreen, or exit if already in it. */
  onToggleFullscreen: () => void;
  /** Called when the user clicks into the surface (focus this terminal). */
  onFocusRequest?: () => void;
  /** Focus-follows-mouse: called when the pointer enters this tile (grid only),
   * so hovering changes focus without a click. No-op'd mid-drag by the caller. */
  onHoverFocus?: () => void;
  /** Called when output arrives while this card is hidden (rail activity dot). */
  onActivity?: () => void;
  /** Called when the remote process exits (session may have ended) or the
   * terminal is closed — the caller should re-run discovery for this instance
   * so the rail's live Zellij/git state stops being stale. */
  onEnded?: () => void;
  /** The terminal reached `ready` — a Zellij session for this target now exists. */
  onStarted?: () => void;
}

/** Where the terminal-theme popup goes, as viewport-relative inline styles. */
export interface ThemeMenuPosition {
  style: CSSProperties;
  /** Which side of the swatch the menu opened on (asserted in tests). */
  placement: "below" | "above";
}

/** Gap between the swatch and the menu, and the minimum breathing room kept
 * against the viewport edges. */
const THEME_MENU_GAP = 6;
const THEME_MENU_MARGIN = 8;
/** Matches the menu's min-width in TerminalCard.css. */
const THEME_MENU_WIDTH = 216;
/** Below this, "below the swatch" is too cramped to be worth using. */
const THEME_MENU_MIN_USABLE = 160;

/**
 * Place the theme popup against the VIEWPORT rather than the card.
 *
 * A grid tile on a small screen (a 2x2 layout on an iPad, say) is routinely
 * shorter than the nine-entry theme list, and `.terminal-card` sets
 * `overflow: hidden` — so an absolutely-positioned menu was silently clipped at
 * the card's edge with no way to reach the rest. Fixed positioning escapes that
 * clip, and the computed `maxHeight` is what gives the menu something to scroll
 * (paired with `overscroll-behavior: contain` in CSS, so the scroll stops at the
 * menu instead of running the page).
 *
 * Opens downward by default, flipping above the swatch when there is more room
 * there, and clamps horizontally so the menu can never sit off-screen.
 */
export function themeMenuPosition(
  anchor: { top: number; bottom: number; right: number },
  viewport: { width: number; height: number } = {
    width: window.innerWidth,
    height: window.innerHeight,
  },
): ThemeMenuPosition {
  const roomBelow =
    viewport.height - anchor.bottom - THEME_MENU_GAP - THEME_MENU_MARGIN;
  const roomAbove = anchor.top - THEME_MENU_GAP - THEME_MENU_MARGIN;
  const placement: "below" | "above" =
    roomBelow < THEME_MENU_MIN_USABLE && roomAbove > roomBelow
      ? "above"
      : "below";

  // Right-align to the swatch, then clamp both edges into the viewport.
  const left = Math.max(
    THEME_MENU_MARGIN,
    Math.min(
      anchor.right - THEME_MENU_WIDTH,
      viewport.width - THEME_MENU_WIDTH - THEME_MENU_MARGIN,
    ),
  );
  // Never negative: a zero-height anchor (jsdom, or a card mid-teardown) would
  // otherwise ask for a negative max-height and render an unusable sliver.
  const maxHeight = Math.max(
    THEME_MENU_MIN_USABLE,
    placement === "above" ? roomAbove : roomBelow,
  );

  return {
    placement,
    style:
      placement === "above"
        ? {
            position: "fixed",
            bottom: viewport.height - anchor.top + THEME_MENU_GAP,
            left,
            maxHeight,
          }
        : {
            position: "fixed",
            top: anchor.bottom + THEME_MENU_GAP,
            left,
            maxHeight,
          },
  };
}

function effectiveFont(
  settings: SettingsState,
  mode: TerminalCardMode,
): TerminalFontOptions {
  const base = terminalFontOptions(settings);
  if (mode === "grid" && settings.gridFit) {
    return {
      ...base,
      fontSize: Math.max(9, Math.round(base.fontSize * GRID_FIT_SCALE)),
    };
  }
  return base;
}

export function TerminalCard({
  target,
  region,
  mode,
  isVisible,
  isFocused,
  viewState,
  onClose,
  reorderEnabled,
  gridArea,
  masterSide,
  onCycleTile,
  onNormal,
  onGrid,
  gridTitle,
  onToggleFullscreen,
  onFocusRequest,
  onHoverFocus,
  onActivity,
  onEnded,
  onStarted,
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
  const onStartedRef = useRef(onStarted);
  const fontRef = useRef<TerminalFontOptions>(effectiveFont(settings, mode));
  const themeRef = useRef<TerminalThemeColors>(
    effectiveTerminalTheme(settings, target.id).colors,
  );
  // Coalesced-fit bookkeeping: a pending rAF handle, and the last dims we sent
  // (to skip redundant resize frames).
  const fitRafRef = useRef<number | null>(null);
  const lastSentDimsRef = useRef<{ cols: number; rows: number } | null>(null);
  // Pending focus-follows-mouse dwell timer (cleared if the pointer leaves first).
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Latest focus-dwell setting, read inside the timer without re-creating the handler.
  const dwellMsRef = useRef(settings.focusDwellMs);
  dwellMsRef.current = settings.focusDwellMs;

  const [connectionState, setConnectionState] =
    useState<TerminalConnectionState>("connecting");
  const [needsManualReconnect, setNeedsManualReconnect] = useState(false);
  const [error, setError] = useState<TypedError | null>(null);
  const [hasSelection, setHasSelection] = useState(false);
  const [copied, setCopied] = useState(false);
  const [themeMenuOpen, setThemeMenuOpen] = useState(false);
  const themeMenuRef = useRef<HTMLDivElement | null>(null);
  const themeButtonRef = useRef<HTMLButtonElement | null>(null);
  // Viewport-relative placement for the theme popup; null until measured.
  const [themeMenuPos, setThemeMenuPos] = useState<ThemeMenuPosition | null>(
    null,
  );

  // dnd-kit reorder: the tile is both a draggable (handle = header grip, below)
  // and a droppable (swap target). Disabled outside a reorderable grid. We use
  // the DragOverlay pattern (WorkspacePane), so the source stays put (dimmed)
  // and we never apply `draggable.transform`.
  const draggable = useDraggable({ id: target.id, disabled: !reorderEnabled });
  const droppable = useDroppable({ id: target.id, disabled: !reorderEnabled });
  const setDraggableRef = draggable.setNodeRef;
  const setDroppableRef = droppable.setNodeRef;
  const setTileRef = useCallback(
    (node: HTMLDivElement | null) => {
      setDraggableRef(node);
      setDroppableRef(node);
    },
    [setDraggableRef, setDroppableRef],
  );

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
    onStartedRef.current = onStarted;
  }, [onEnded, onStarted]);

  // Coalesce fit()+resize into at most one per animation frame, and only send a
  // resize frame when the cell grid actually changed. A window drag fires the
  // ResizeObserver many times per second; without this each tick would fit()
  // and push a SIGWINCH-triggering resize to the remote PTY (and can trip the
  // browser's "ResizeObserver loop" warning). Stable identity (empty deps): it
  // reads everything through refs.
  const scheduleFit = useCallback(() => {
    if (fitRafRef.current !== null) {
      return; // a fit is already scheduled for this frame
    }
    fitRafRef.current = requestAnimationFrame(() => {
      fitRafRef.current = null;
      const adapter = adapterRef.current;
      const connection = connectionRef.current;
      const container = containerRef.current;
      if (!adapter || !connection || !container) {
        return;
      }
      // A hidden pane collapses to 0x0; fitting then would shrink the remote
      // PTY to 1x1 and corrupt a backgrounded TUI. Skip — the observer fires
      // again with real dimensions when the card is shown, and TerminalConnection
      // re-sends the last dims on `ready` after a reconnect.
      if (container.clientWidth === 0 || container.clientHeight === 0) {
        return;
      }
      const dims = adapter.fit();
      const last = lastSentDimsRef.current;
      if (last && last.cols === dims.cols && last.rows === dims.rows) {
        return; // grid unchanged — no need to resize the remote PTY
      }
      lastSentDimsRef.current = dims;
      connection.sendResize(dims.cols, dims.rows);
    });
  }, []);

  // Apply live font/size/ligature changes (and grid-fit scaling) to the open
  // terminal, then re-fit so the new cell grid reaches the remote PTY.
  const font = effectiveFont(settings, mode);
  useEffect(() => {
    fontRef.current = font;
    const adapter = adapterRef.current;
    if (!adapter) {
      return;
    }
    adapter.applyFont(font);
    // A font/size change alters the cell grid; re-fit (coalesced) so the new
    // cols/rows reach the remote PTY. scheduleFit no-ops while hidden (0x0).
    scheduleFit();
    // font is a fresh object each render; compare by its fields.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [font.fontFamily, font.fontSize, font.ligatures]);

  // Apply a live terminal-theme change (global default or this card's
  // override) to the open terminal. Deliberately NOT part of the mount effect's
  // dep array: recoloring must never remount the terminal (that would drop the
  // scrollback and reconnect). Colors don't alter cell metrics, so no re-fit.
  const theme = effectiveTerminalTheme(settings, target.id);
  useEffect(() => {
    themeRef.current = theme.colors;
    adapterRef.current?.applyTheme(theme.colors);
    // theme is derived fresh each render; its id identifies the palette.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme.id]);

  useEffect(() => {
    if (createdRef.current) {
      return undefined;
    }
    const container = containerRef.current;
    if (!container) {
      return undefined;
    }
    createdRef.current = true;

    const adapter = createDefaultRenderer(fontRef.current, themeRef.current);
    adapterRef.current = adapter;
    adapter.open(container);

    const connection = new TerminalConnection(
      target.id,
      DEFAULT_COLS,
      DEFAULT_ROWS,
      {
        onData: (data) => {
          adapter.write(data);
          if (!isVisibleRef.current) {
            onActivityRef.current?.();
          }
        },
        onReady: () => {
          setError(null);
          // Attaching creates-or-attaches the target's Zellij session, so the
          // rail can light its ⚡ now rather than waiting for the next discovery
          // run to notice (which is what made a freshly-started devcontainer look
          // sessionless until a page reload).
          onStartedRef.current?.();
        },
        onExit: () => {
          // The remote process exited — the Zellij session may have ended (e.g.
          // the user quit Zellij). Re-run discovery so the rail's ⚡/git state
          // reflects reality instead of the now-stale cache.
          onEndedRef.current?.();
        },
        onError: (typedError) => setError(typedError),
        onStateChange: (state) => {
          setConnectionState(state);
          setNeedsManualReconnect(
            connectionRef.current?.needsManualReconnect ?? false,
          );
          // Only a connected terminal contributes to the header latency median.
          if (state !== "ready") {
            removeLatency(target.id);
          }
        },
        onLatency: (rttMs) => reportLatency(target.id, rttMs),
      },
    );
    connectionRef.current = connection;

    const unsubscribeInput = adapter.onData((data) => {
      if (isFocusedRef.current) {
        connection.sendInput(data);
      }
    });

    // Show/hide the Copy affordance as the terminal selection changes.
    const unsubscribeSelection = adapter.onSelectionChange((has) =>
      setHasSelection(has),
    );

    // Reflow on every container size change (window resize, rail drag, grid
    // <-> single, tile show/hide). scheduleFit coalesces bursts to one fit per
    // frame and skips the hidden-0x0 case.
    const resizeObserver = new ResizeObserver(() => scheduleFit());
    resizeObserver.observe(container);

    void connection.connect();

    return () => {
      createdRef.current = false;
      if (fitRafRef.current !== null) {
        cancelAnimationFrame(fitRafRef.current);
        fitRafRef.current = null;
      }
      lastSentDimsRef.current = null;
      removeLatency(target.id);
      unsubscribeInput();
      unsubscribeSelection();
      resizeObserver.disconnect();
      void connection.close();
      adapter.dispose();
      adapterRef.current = null;
      connectionRef.current = null;
    };
    // Keyed on target.id alone: this card owns exactly one terminal for its
    // lifetime (see file header). Nothing else may enter this dep array — a
    // re-run tears down the connection and the browser scrollback with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id]);

  // Move DOM keyboard focus into the terminal whenever this card becomes the
  // focused, visible one — e.g. after clicking a rail row (selectOnly) or a
  // number-key jump. Without this the card is "focused" in workspace state (our
  // input gate opens) but the xterm textarea has no DOM focus, so keystrokes go
  // nowhere until the user clicks into the surface. Guarded on isVisible: a
  // hidden card must never steal focus.
  useEffect(() => {
    if (isFocused && isVisible) {
      adapterRef.current?.focus();
    }
  }, [isFocused, isVisible]);

  // Measure the popup's placement before paint, and keep it anchored while it
  // is open. A grid tile can be far smaller than the list, so the menu is
  // positioned against the VIEWPORT (see ThemeMenuPosition) rather than the
  // card, which clips its overflow.
  useLayoutEffect(() => {
    if (!themeMenuOpen) {
      setThemeMenuPos(null);
      return undefined;
    }
    const measure = (): void => {
      const button = themeButtonRef.current;
      if (button) {
        setThemeMenuPos(themeMenuPosition(button.getBoundingClientRect()));
      }
    };
    measure();
    // A rotate/resize moves the anchor; so does scrolling the pane under it.
    // Scrolling INSIDE the menu must not re-anchor (it would fight the user),
    // and element scrolls only reach window listeners in the capture phase.
    const onScroll = (e: Event): void => {
      if (themeMenuRef.current?.contains(e.target as Node)) {
        return;
      }
      measure();
    };
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [themeMenuOpen]);

  // Dismiss the theme popup on Escape or a click anywhere outside it.
  useEffect(() => {
    if (!themeMenuOpen) {
      return undefined;
    }
    const onPointerDown = (e: globalThis.PointerEvent): void => {
      if (!themeMenuRef.current?.contains(e.target as Node)) {
        setThemeMenuOpen(false);
      }
    };
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        setThemeMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [themeMenuOpen]);

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

  const handleCopy = useCallback(() => {
    void adapterRef.current?.copySelection().then((ok) => {
      if (ok) {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }
    });
  }, []);

  const clearHoverTimer = useCallback(() => {
    if (hoverTimerRef.current !== null) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
  }, []);

  // Focus-follows-mouse with a dwell delay: arm a timer on enter, fire only if
  // the pointer is still resting here after HOVER_FOCUS_DELAY_MS.
  const handleMouseEnter = useCallback(() => {
    if (!onHoverFocus) {
      return;
    }
    clearHoverTimer();
    hoverTimerRef.current = setTimeout(() => {
      hoverTimerRef.current = null;
      onHoverFocus();
    }, dwellMsRef.current);
  }, [onHoverFocus, clearHoverTimer]);

  // Cancel a pending dwell when the pointer leaves or the card unmounts.
  useEffect(() => clearHoverTimer, [clearHoverTimer]);

  // Chrome off trades the header (identity, state, window controls, and the drag
  // handle — the header IS the handle) for ~36px of terminal per tile. The
  // toggle lives in the app header, which stays visible, so it is always one tap
  // back. Fullscreen keeps its chrome regardless: there is exactly one card on
  // screen, the space saved is negligible, and the ✕/⤡ controls are the way out.
  const showChrome = settings.showTileChrome || viewState === "fullscreen";

  const prov = providerMeta(target.instance_type);
  const badge = [prov.label, target.instance_name, region]
    .filter(Boolean)
    .join(" · ");

  // The Settings-wide choice, shown as the popup's "Default — …" entry. Under
  // "auto" that resolves per site mode, so the entry names what it means now.
  const globalTheme = effectiveTerminalTheme(settings);
  const globalLabel = terminalThemeLabel(settings.termTheme, settings);
  const hasThemeOverride = settings.termThemeOverrides[target.id] !== undefined;

  const isDragging = draggable.isDragging;
  // Highlight the tile the drop will land on (swap target) — but not the source.
  const isDropTarget =
    Boolean(reorderEnabled) && droppable.isOver && !isDragging;
  const dragClasses = `${isDragging ? " terminal-card--dragging" : ""}${isDropTarget ? " terminal-card--drop-target" : ""}`;

  return (
    <div
      ref={setTileRef}
      className={`terminal-card terminal-card--${mode}${isFocused ? " terminal-card--focused" : ""}${dragClasses}`}
      data-testid={`terminal-card-${target.id}`}
      data-focused={isFocused}
      data-connection-state={connectionState}
      // --bg-term drives the card's own background and the surface's 2px/4px
      // padding gutter; pinning it to this card's terminal theme keeps the
      // gutter flush with what the emulator paints inside it.
      style={
        {
          display: isVisible ? undefined : "none",
          gridArea,
          "--bg-term": theme.colors.background,
        } as CSSProperties
      }
      onMouseEnter={handleMouseEnter}
      onMouseLeave={clearHoverTimer}
    >
      {showChrome && (
        <header className="terminal-card-header">
          {/* The header (left of the controls) is the drag handle for reordering
           * grid tiles; it no longer solos on click — the ◻ control does that. */}
          <div
            ref={reorderEnabled ? draggable.setActivatorNodeRef : undefined}
            className={`terminal-card-grip${reorderEnabled ? " terminal-card-grip--handle" : ""}`}
            {...(reorderEnabled ? draggable.listeners : {})}
            {...(reorderEnabled ? draggable.attributes : {})}
          >
            <span
              className="terminal-card-provider-dot"
              style={{ background: prov.color }}
            />
            <div className="terminal-card-identity">
              <span className="terminal-card-project">{target.project}</span>
              {mode === "single" ? (
                <span className="terminal-card-badge">{badge}</span>
              ) : (
                <span className="terminal-card-instance">
                  {target.instance_name}
                </span>
              )}
            </div>
          </div>
          <span
            className={`terminal-card-state terminal-card-state--${connectionState}`}
            title={STATE_LABELS[connectionState]}
          >
            {STATE_LABELS[connectionState]}
          </span>
          <div className="terminal-card-controls">
            {hasSelection && (
              <button
                type="button"
                className="tc-btn"
                data-testid={`terminal-copy-${target.id}`}
                title="Copy selection (⌘C / Ctrl+Shift+C)"
                onClick={(e) => {
                  e.stopPropagation();
                  handleCopy();
                }}
              >
                {copied ? "✓ Copied" : "⧉ Copy"}
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
            {/* Per-terminal color scheme. The swatch shows what this card is
             * painted with; the popup sets an override, or clears back to
             * "Default" so the card follows the Settings-wide choice again. */}
            <div className="tc-theme" ref={themeMenuRef}>
              <button
                type="button"
                ref={themeButtonRef}
                className="tc-btn tc-btn--swatch"
                data-testid={`terminal-theme-${target.id}`}
                title={`Terminal theme: ${theme.label}${hasThemeOverride ? "" : " (default)"}`}
                aria-label="Terminal theme"
                aria-haspopup="menu"
                aria-expanded={themeMenuOpen}
                style={{
                  background: theme.colors.background,
                  color: theme.colors.foreground,
                }}
                onClick={(e) => {
                  e.stopPropagation();
                  setThemeMenuOpen((v) => !v);
                }}
              >
                A
              </button>
              {themeMenuOpen && themeMenuPos && (
                <div
                  className="tc-theme-menu"
                  role="menu"
                  style={themeMenuPos.style}
                >
                  <button
                    type="button"
                    className={`tc-theme-item${hasThemeOverride ? "" : " tc-theme-item--on"}`}
                    role="menuitem"
                    data-testid={`terminal-theme-default-${target.id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      settingsActions.setTermThemeOverride(target.id, null);
                      setThemeMenuOpen(false);
                    }}
                  >
                    <span
                      className="tc-theme-swatch"
                      style={{
                        background: globalTheme.colors.background,
                        borderColor: globalTheme.colors.brightBlack,
                      }}
                    />
                    <span className="tc-theme-label">
                      Default — {globalLabel}
                    </span>
                  </button>
                  {TERMINAL_THEMES.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={`tc-theme-item${hasThemeOverride && t.id === theme.id ? " tc-theme-item--on" : ""}`}
                      role="menuitem"
                      onClick={(e) => {
                        e.stopPropagation();
                        settingsActions.setTermThemeOverride(target.id, t.id);
                        setThemeMenuOpen(false);
                      }}
                    >
                      <span
                        className="tc-theme-swatch"
                        style={{
                          background: t.colors.background,
                          borderColor: t.colors.brightBlack,
                        }}
                      >
                        <i style={{ background: t.colors.red }} />
                        <i style={{ background: t.colors.green }} />
                        <i style={{ background: t.colors.blue }} />
                      </span>
                      <span className="tc-theme-label">{t.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {/* Tiling control. Cycles rather than opening a menu: it is one tap on
             * a touch device, where dragging to a 64px edge band with a thumb is
             * the awkward path. The half-block glyphs draw where the master area
             * lands, so the current state is legible without the tooltip. */}
            {onCycleTile && (
              <button
                type="button"
                className={`tc-btn tc-btn--icon${masterSide ? " tc-btn--active" : ""}`}
                data-testid={`terminal-tile-${target.id}`}
                title={MASTER_GLYPH[masterSide ?? "grid"].title}
                aria-label={MASTER_GLYPH[masterSide ?? "grid"].title}
                aria-pressed={Boolean(masterSide)}
                onClick={(e) => {
                  e.stopPropagation();
                  onCycleTile();
                }}
              >
                {MASTER_GLYPH[masterSide ?? "grid"].glyph}
              </button>
            )}
            {/* Window-control cluster: mutually-exclusive display modes ordered
             * by how much space they take — Grid (smaller/tiled) → Normal (fills
             * the app's main pane) → Fullscreen (whole window) — plus close. The
             * current mode is shown active + disabled; Fullscreen toggles. Icons
             * only; the label is the tooltip. */}
            <div
              className="tc-winctl"
              role="group"
              aria-label="Terminal display mode"
            >
              <button
                type="button"
                className={`tc-btn tc-btn--icon${viewState === "grid" ? " tc-btn--active" : ""}`}
                data-testid={`terminal-grid-${target.id}`}
                title={gridTitle ?? "Grid view"}
                aria-label={gridTitle ?? "Grid view"}
                aria-pressed={viewState === "grid"}
                disabled={!onGrid}
                onClick={(e) => {
                  e.stopPropagation();
                  onGrid?.();
                }}
              >
                ⊞
              </button>
              <button
                type="button"
                className={`tc-btn tc-btn--icon${viewState === "normal" ? " tc-btn--active" : ""}`}
                data-testid={`terminal-normal-${target.id}`}
                title="Fill the main pane (single view)"
                aria-label="Fill the main pane"
                aria-pressed={viewState === "normal"}
                disabled={viewState === "normal"}
                onClick={(e) => {
                  e.stopPropagation();
                  onNormal();
                }}
              >
                ◻
              </button>
              <button
                type="button"
                className={`tc-btn tc-btn--icon${viewState === "fullscreen" ? " tc-btn--active" : ""}`}
                data-testid={`terminal-fullscreen-${target.id}`}
                title={
                  viewState === "fullscreen" ? "Exit fullscreen" : "Fullscreen"
                }
                aria-label={
                  viewState === "fullscreen" ? "Exit fullscreen" : "Fullscreen"
                }
                aria-pressed={viewState === "fullscreen"}
                onClick={(e) => {
                  e.stopPropagation();
                  onToggleFullscreen();
                }}
              >
                {viewState === "fullscreen" ? "⤡" : "⤢"}
              </button>
              <button
                type="button"
                className="tc-btn tc-btn--icon tc-btn--close"
                data-testid={`terminal-close-${target.id}`}
                title="Close terminal — remote Zellij session stays alive"
                aria-label="Close terminal"
                onClick={(e) => {
                  e.stopPropagation();
                  handleClose();
                }}
              >
                ✕
              </button>
            </div>
          </div>
        </header>
      )}

      {error && (
        <div className="terminal-card-error">
          <p className="terminal-card-error-message">
            [{error.code}] {error.message}
          </p>
          {error.remediation && (
            <p className="terminal-card-error-remediation">
              {error.remediation}
            </p>
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
