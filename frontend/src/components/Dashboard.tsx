// Top-level dashboard (T030, US1). Groups `targets` by (instance_type,
// instance_name) and renders one InstanceGroup per discovered instance —
// including instances with zero targets and non-`ok` instances, so every
// configured instance stays visible with actionable status (FR-029).
//
// T047/T048 (US3): also owns the bulk-open controls (one / all-on-instance /
// selected / all), renders the open-terminal workspace (GridView/TabView)
// below the discovery groups, and wires keyboard-based focus cycling.

import { useMemo, useState } from "react";
import { useDiscovery } from "../state/discovery";
import { useWorkspace, type LayoutMode } from "../state/workspace";
import { useKeyboardSwitching } from "../state/useKeyboardSwitching";
import type { DiscoveryInstance, SessionTarget } from "../api/client";
import { InstanceGroup } from "./InstanceGroup";
import { GridView } from "./GridView";
import { TabView } from "./TabView";
import "./Dashboard.css";

const LAYOUT_MODE_LABELS: Record<LayoutMode, string> = {
  grid: "Grid",
  tabs: "Tabs",
  focused: "Focused",
};
const LAYOUT_MODES = Object.keys(LAYOUT_MODE_LABELS) as LayoutMode[];

interface InstanceGroupData {
  instance: DiscoveryInstance;
  targets: SessionTarget[];
}

function groupKey(instanceType: string, instanceName: string): string {
  return `${instanceType}::${instanceName}`;
}

function groupTargetsByInstance(
  instances: DiscoveryInstance[],
  targets: SessionTarget[],
): InstanceGroupData[] {
  const targetsByKey = new Map<string, SessionTarget[]>();
  for (const target of targets) {
    const key = groupKey(target.instance_type, target.instance_name);
    const existing = targetsByKey.get(key);
    if (existing) {
      existing.push(target);
    } else {
      targetsByKey.set(key, [target]);
    }
  }

  return instances.map((instance) => ({
    instance,
    targets: targetsByKey.get(groupKey(instance.instance_type, instance.instance_name)) ?? [],
  }));
}

function formatLastRefreshed(lastRefreshedAt: string | null): string {
  if (!lastRefreshedAt) {
    return "never";
  }
  const parsed = new Date(lastRefreshedAt);
  if (Number.isNaN(parsed.getTime())) {
    return lastRefreshedAt;
  }
  return parsed.toLocaleTimeString();
}

export function Dashboard(): JSX.Element {
  const { instances, targets, refresh, isRefreshing, lastRefreshedAt } = useDiscovery();
  const workspace = useWorkspace();

  // "Open selected" (FR-030) is a lightweight, dashboard-local concern — it
  // doesn't need to survive a reload the way `workspace.openTargetIds` does,
  // so plain component state is enough (no need to route it through the
  // workspace store).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const groups = useMemo(() => groupTargetsByInstance(instances, targets), [instances, targets]);

  // Join workspace.openTargetIds against live discovery data. The two
  // stores are intentionally independent (see workspace.ts) — this is the
  // one place they meet. Any id that doesn't currently resolve to a real
  // SessionTarget (e.g. stale after a restart/registry change, or
  // discovery just hasn't loaded yet) is silently skipped rather than
  // rendering a broken card.
  const targetsById = useMemo(
    () => new Map(targets.map((target) => [target.id, target] as const)),
    [targets],
  );
  const openTargets = useMemo(
    () =>
      workspace.openTargetIds
        .map((id) => targetsById.get(id))
        .filter((target): target is SessionTarget => target !== undefined),
    [workspace.openTargetIds, targetsById],
  );
  const openTargetIds = useMemo(() => openTargets.map((target) => target.id), [openTargets]);

  useKeyboardSwitching(openTargetIds, workspace.focusedTargetId, workspace.setFocused);

  function toggleSelect(targetId: string): void {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(targetId)) {
        next.delete(targetId);
      } else {
        next.add(targetId);
      }
      return next;
    });
  }

  function openSelected(): void {
    const selectedTargets = targets.filter((target) => selectedIds.has(target.id));
    workspace.openMany(selectedTargets);
    setSelectedIds(new Set());
  }

  function openAll(): void {
    workspace.openMany(targets);
  }

  return (
    <div className="dashboard">
      <header className="dashboard-header">
        <h1 className="dashboard-title">Remo &mdash; Session Dashboard</h1>
        <div className="dashboard-controls">
          <span className="dashboard-last-refreshed">
            Last refreshed: {formatLastRefreshed(lastRefreshedAt)}
          </span>
          <button
            type="button"
            className="dashboard-refresh-button"
            disabled={isRefreshing}
            onClick={() => void refresh()}
          >
            {isRefreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </header>

      {groups.length === 0 ? (
        <p className="dashboard-empty">
          No instances configured yet. Add hosts to the Remo registry to see them here.
        </p>
      ) : (
        <>
          <div className="dashboard-bulk-controls">
            <button
              type="button"
              className="dashboard-bulk-button"
              data-testid="open-all-button"
              disabled={targets.length === 0}
              onClick={openAll}
            >
              Open all ({targets.length})
            </button>
            <button
              type="button"
              className="dashboard-bulk-button"
              data-testid="open-selected-button"
              disabled={selectedIds.size === 0}
              onClick={openSelected}
            >
              Open selected ({selectedIds.size})
            </button>
          </div>

          <div className="dashboard-groups">
            {groups.map(({ instance, targets: instanceTargets }) => (
              <InstanceGroup
                key={groupKey(instance.instance_type, instance.instance_name)}
                instance={instance}
                targets={instanceTargets}
                onRefresh={() => void refresh(instance.instance_id)}
                selectedIds={selectedIds}
                onToggleSelect={toggleSelect}
                onOpenTarget={workspace.openTarget}
                onOpenAll={workspace.openMany}
              />
            ))}
          </div>
        </>
      )}

      {openTargets.length > 0 && (
        <section className="dashboard-workspace" data-testid="workspace">
          <header className="dashboard-workspace-header">
            <h2 className="dashboard-workspace-title">Open terminals ({openTargets.length})</h2>
            <div className="dashboard-layout-switcher" role="group" aria-label="Layout mode">
              {LAYOUT_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  data-testid={`layout-${mode}`}
                  className={
                    workspace.layoutMode === mode
                      ? "dashboard-layout-button dashboard-layout-button--active"
                      : "dashboard-layout-button"
                  }
                  onClick={() => workspace.setLayoutMode(mode)}
                >
                  {LAYOUT_MODE_LABELS[mode]}
                </button>
              ))}
            </div>
          </header>

          {workspace.layoutMode === "grid" ? (
            <GridView
              openTargets={openTargets}
              focusedTargetId={workspace.focusedTargetId}
              onFocus={workspace.setFocused}
              onClose={workspace.closeTarget}
            />
          ) : (
            <TabView
              openTargets={openTargets}
              focusedTargetId={workspace.focusedTargetId}
              onFocus={workspace.setFocused}
              onClose={workspace.closeTarget}
              showTabBar={workspace.layoutMode === "tabs"}
            />
          )}
        </section>
      )}
    </div>
  );
}
