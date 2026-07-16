// The console top bar: rail toggle, brand, trusted-LAN lock, open-count,
// health indicator, refresh, settings, and shortcuts.

import type { HealthStatus } from "../state/health";
import "./TopBar.css";

const HEALTH_LABEL: Record<HealthStatus, string> = {
  loading: "Discovering…",
  healthy: "Service healthy",
  degraded: "Degraded",
  offline: "Service offline",
};

const HEALTH_COLOR: Record<HealthStatus, string> = {
  loading: "var(--warn)",
  healthy: "var(--ok)",
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
  onRefresh: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
}

export function TopBar({
  showRailToggle,
  railCollapsed,
  onToggleRail,
  openCount,
  health,
  healthDetail,
  refreshing,
  onRefresh,
  onSettings,
  onShortcuts,
}: TopBarProps): JSX.Element {
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

      <span className="topbar-health" title={healthDetail ?? HEALTH_LABEL[health]}>
        <span className="topbar-health-dot" style={{ background: HEALTH_COLOR[health] }} />
        <span>{HEALTH_LABEL[health]}</span>
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
