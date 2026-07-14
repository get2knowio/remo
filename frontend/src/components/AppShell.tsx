// Top-level console shell: top bar + resizable/collapsible session rail +
// terminal pane, plus the settings/shortcuts/offline overlays. Owns filter
// state and the responsive (narrow) layout; delegates discovery, health,
// workspace, and settings to their stores.

import { useCallback, useEffect, useMemo, useState, type PointerEvent } from "react";
import type { SessionTarget } from "../api/client";
import { useDiscovery } from "../state/discovery";
import { useHealth } from "../state/health";
import { settingsActions, useSettings } from "../state/settings";
import { useConsoleKeyboard } from "../state/useConsoleKeyboard";
import { useWorkspace } from "../state/workspace";
import { buildRailModel, type RailFilters } from "./railModel";
import { OfflineOverlay } from "./OfflineOverlay";
import { SessionRail } from "./SessionRail";
import { SettingsPage } from "./SettingsPage";
import { ShortcutsModal } from "./ShortcutsModal";
import { TopBar } from "./TopBar";
import { WorkspacePane } from "./WorkspacePane";
import "./AppShell.css";

const NARROW_BREAKPOINT = 820;

export function AppShell(): JSX.Element {
  const discovery = useDiscovery();
  const health = useHealth();
  const settings = useSettings();
  const workspace = useWorkspace();

  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<string | null>(null);
  const [sessionOnly, setSessionOnly] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [narrow, setNarrow] = useState(
    typeof window !== "undefined" ? window.innerWidth < NARROW_BREAKPOINT : false,
  );

  useEffect(() => {
    const onResize = (): void => setNarrow(window.innerWidth < NARROW_BREAKPOINT);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const filters: RailFilters = useMemo(
    () => ({ search, providerFilter, sessionOnly }),
    [search, providerFilter, sessionOnly],
  );

  const targetsById = useMemo(() => {
    const map = new Map<string, SessionTarget>();
    for (const t of discovery.targets) {
      map.set(t.id, t);
    }
    return map;
  }, [discovery.targets]);

  const railModel = useMemo(
    () => buildRailModel(discovery.instances, discovery.targets, filters),
    [discovery.instances, discovery.targets, filters],
  );

  const regionByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const i of discovery.instances) {
      map.set(`${i.instance_type}::${i.instance_name}`, i.region);
    }
    return map;
  }, [discovery.instances]);

  const instanceIdByKey = useMemo(() => {
    const map = new Map<string, string>();
    for (const i of discovery.instances) {
      map.set(`${i.instance_type}::${i.instance_name}`, i.instance_id);
    }
    return map;
  }, [discovery.instances]);

  // A terminal exited or was closed: re-discover its instance so the rail's
  // live ⚡/git state stops being stale (e.g. the ⚡ clears once a quit Zellij
  // session is gone). Targeted to the one instance to keep it cheap.
  const refresh = discovery.refresh;
  const onTerminalEnded = useCallback(
    (target: SessionTarget) => {
      const id = instanceIdByKey.get(`${target.instance_type}::${target.instance_name}`);
      void refresh(id);
    },
    [instanceIdByKey, refresh],
  );

  const providers = useMemo(
    () => [...new Set(discovery.instances.map((i) => i.instance_type))],
    [discovery.instances],
  );

  const onEscapeOverlay = useCallback((): boolean => {
    if (settingsOpen) {
      setSettingsOpen(false);
      return true;
    }
    if (shortcutsOpen) {
      setShortcutsOpen(false);
      return true;
    }
    return false;
  }, [settingsOpen, shortcutsOpen]);

  const onToggleShortcuts = useCallback(() => setShortcutsOpen((v) => !v), []);

  useConsoleKeyboard({
    flatOpenable: railModel.flatOpenable,
    workspace,
    onToggleShortcuts,
    onEscapeOverlay,
  });

  // Divider drag → live rail width.
  const onRailDragStart = useCallback(
    (e: PointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startW = settings.railWidth;
      const move = (ev: globalThis.PointerEvent): void => {
        settingsActions.setRailWidth(startW + (ev.clientX - startX));
      };
      const up = (): void => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        document.body.style.userSelect = "";
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
      document.body.style.userSelect = "none";
    },
    [settings.railWidth],
  );

  const paneHasContent = workspace.attached.length > 0;
  const railHidden = narrow ? paneHasContent : settings.railCollapsed;
  const paneHidden = narrow && !paneHasContent;
  const showDivider = !narrow && !settings.railCollapsed;

  // "Loading" until the first discovery has produced instances — either before
  // the first poll (lastRefreshedAt null) or while a refresh is still in flight
  // (the auto-triggered first launch discovery). This keeps the "Empty
  // registry" notice from flashing before discovery has actually finished.
  const isLoading =
    discovery.instances.length === 0 &&
    (discovery.lastRefreshedAt === null || discovery.isRefreshing);
  const noRegistry = !isLoading && discovery.instances.length === 0;
  const noCredentials = health.checks.ssh_identity === "missing";

  return (
    <div className="app-shell">
      <TopBar
        showRailToggle={!narrow}
        railCollapsed={settings.railCollapsed}
        onToggleRail={() => settingsActions.toggleRailCollapsed()}
        openCount={workspace.attached.length}
        health={health.status}
        healthDetail={health.detail}
        refreshing={discovery.isRefreshing}
        onRefresh={() => void discovery.refresh()}
        onSettings={() => setSettingsOpen(true)}
        onShortcuts={onToggleShortcuts}
      />

      {discovery.isRefreshing && (
        <div className="app-refresh-bar">
          <div className="app-refresh-bar-fill" />
        </div>
      )}

      <div className="app-body">
        <aside
          className="app-rail"
          style={{
            width: narrow ? "100%" : `${settings.railWidth}px`,
            display: railHidden ? "none" : "flex",
          }}
        >
          <SessionRail
            model={railModel}
            filters={filters}
            providers={providers}
            isLoading={isLoading}
            noRegistry={noRegistry}
            noCredentials={noCredentials}
            workspace={workspace}
            onSearch={setSearch}
            onToggleProvider={(p) => setProviderFilter((cur) => (cur === p ? null : p))}
            onToggleSessionOnly={() => setSessionOnly((v) => !v)}
            onOpenAllAvailable={() => workspace.openMany(railModel.flatOpenable)}
          />
        </aside>

        {showDivider && (
          <div
            className="app-divider"
            title="Drag to resize the sessions panel"
            onPointerDown={onRailDragStart}
          />
        )}

        <div className="app-pane" style={{ display: paneHidden ? "none" : "flex" }}>
          {narrow && paneHasContent && (
            <div className="app-backbar">
              <button
                type="button"
                className="app-backbar-btn"
                onClick={() => {
                  workspace.closeTerm(workspace.focusedId ?? workspace.visible[0] ?? "");
                }}
              >
                ‹ Sessions
              </button>
            </div>
          )}
          <WorkspacePane
            targetsById={targetsById}
            regionByKey={regionByKey}
            onTerminalEnded={onTerminalEnded}
            narrow={narrow}
          />
        </div>
      </div>

      {settingsOpen && <SettingsPage onClose={() => setSettingsOpen(false)} />}
      {shortcutsOpen && <ShortcutsModal onClose={() => setShortcutsOpen(false)} />}
      {health.status === "offline" && <OfflineOverlay onRetry={() => void health.retry()} />}
    </div>
  );
}
