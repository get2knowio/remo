// The console top bar: rail toggle, brand, trusted-LAN lock, open-count,
// health indicator, refresh, settings, and shortcuts.

import type { HealthStatus } from "../state/health";
import "./TopBar.css";

// "unconfigured" is included for Record exhaustiveness, but in practice the
// root gate renders the AwaitingAdoption page (not the shell/top bar) while
// the service is awaiting adoption.
const HEALTH_LABEL: Record<HealthStatus, string> = {
  loading: "Discovering…",
  healthy: "Service healthy",
  unconfigured: "Awaiting adoption",
  degraded: "Degraded",
  offline: "Service offline",
};

const HEALTH_COLOR: Record<HealthStatus, string> = {
  loading: "var(--warn)",
  healthy: "var(--ok)",
  unconfigured: "var(--warn)",
  degraded: "var(--warn)",
  offline: "var(--danger)",
};

interface TopBarProps {
  showRailToggle: boolean;
  railCollapsed: boolean;
  onToggleRail: () => void;
  openCount: number;
  health: HealthStatus;
  healthDetail: string | null;
  refreshing: boolean;
  /** Median WS round-trip latency (ms) across open terminals, or null if none. */
  latencyMs: number | null;
  onRefresh: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
}

/** Latency → dot color: good (green) / so-so (yellow) / poor (red). */
function latencyColor(ms: number): string {
  if (ms < 100) {
    return "var(--ok)";
  }
  if (ms < 300) {
    return "var(--warn)";
  }
  return "var(--danger)";
}

export function TopBar({
  showRailToggle,
  railCollapsed,
  onToggleRail,
  openCount,
  health,
  healthDetail,
  refreshing,
  latencyMs,
  onRefresh,
  onSettings,
  onShortcuts,
}: TopBarProps): JSX.Element {
  // Prefer live WS latency (the real data-path measure); fall back to the
  // health status when no terminal is connected to measure.
  const showLatency = latencyMs != null;
  const dotColor = showLatency ? latencyColor(latencyMs) : HEALTH_COLOR[health];
  const statusLabel = showLatency ? `${Math.round(latencyMs)} ms` : HEALTH_LABEL[health];
  const statusTitle = showLatency
    ? "WebSocket round-trip latency (median across open terminals)"
    : (healthDetail ?? HEALTH_LABEL[health]);
  return (
    <header className="topbar">
      {showRailToggle && (
        <button
          type="button"
          className="topbar-icon-btn"
          title={railCollapsed ? "Show sessions sidebar" : "Hide sessions sidebar"}
          onClick={onToggleRail}
        >
          {railCollapsed ? "▸" : "◧"}
        </button>
      )}

      <div className="topbar-brand">
        <span className="topbar-brand-dot" />
        <span className="topbar-brand-name">remo</span>
        <span className="topbar-brand-badge">web console</span>
      </div>

      <div className="topbar-spacer" />

      <span
        className="topbar-lock"
        title="Trusted LAN / tailnet only — this console grants full shell access"
      >
        🔒
      </span>

      <span className="topbar-opencount">
        <span className="topbar-opencount-dot" />
        {openCount} open
      </span>

      <span className="topbar-health" title={statusTitle} data-testid="topbar-status">
        <span className="topbar-health-dot" style={{ background: dotColor }} />
        <span>{statusLabel}</span>
      </span>

      <button type="button" className="topbar-btn" onClick={onRefresh} data-testid="refresh-button">
        <span className={refreshing ? "rail-spin" : undefined}>⟳</span> Refresh
      </button>

      <button
        type="button"
        className="topbar-icon-btn"
        title="Settings"
        data-testid="settings"
        onClick={onSettings}
      >
        ⚙
      </button>
      <button
        type="button"
        className="topbar-icon-btn topbar-icon-btn--mono"
        title="Keyboard shortcuts"
        data-testid="shortcuts"
        onClick={onShortcuts}
      >
        ?
      </button>
    </header>
  );
}
