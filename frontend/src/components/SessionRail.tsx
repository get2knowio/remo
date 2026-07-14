// The left session rail: search/filter, provider chips, "active only" toggle,
// "open all", instance-grouped session rows (with git/zellij glyphs and
// add-to-grid), and a glyph legend. Presentational — filter state and the
// rail model are owned by AppShell.

import type { MouseEvent } from "react";
import type { SessionTarget } from "../api/client";
import type { UseWorkspaceResult } from "../state/workspace";
import { providerMeta } from "./providerMeta";
import type { RailFilters, RailGroup, RailModel } from "./railModel";
import "./SessionRail.css";

interface SessionRailProps {
  model: RailModel;
  filters: RailFilters;
  providers: string[];
  isLoading: boolean;
  noRegistry: boolean;
  noCredentials: boolean;
  workspace: UseWorkspaceResult;
  onSearch: (value: string) => void;
  onToggleProvider: (provider: string) => void;
  onToggleSessionOnly: () => void;
  onOpenAllAvailable: () => void;
}

const SKELETON_WIDTHS = ["70%", "52%", "84%", "44%", "66%", "58%", "76%", "48%", "62%"];

function isModifierClick(e: MouseEvent): boolean {
  return e.metaKey || e.ctrlKey || e.shiftKey;
}

export function SessionRail({
  model,
  filters,
  providers,
  isLoading,
  noRegistry,
  noCredentials,
  workspace,
  onSearch,
  onToggleProvider,
  onToggleSessionOnly,
  onOpenAllAvailable,
}: SessionRailProps): JSX.Element {
  const { attached, visible, focusedId } = workspace;

  return (
    <div className="rail">
      <div className="rail-filter">
        <div className="rail-search">
          <span className="rail-search-icon">⌕</span>
          <input
            value={filters.search}
            onInput={(e) => onSearch((e.target as HTMLInputElement).value)}
            placeholder="Filter sessions…"
            data-testid="rail-search"
            aria-label="Filter sessions"
          />
        </div>

        {providers.length > 0 && (
          <div className="rail-chips">
            {providers.map((p) => {
              const active = filters.providerFilter === p;
              return (
                <button
                  key={p}
                  type="button"
                  className={`rail-chip${active ? " rail-chip--active" : ""}`}
                  data-testid={`provider-chip-${p}`}
                  onClick={() => onToggleProvider(p)}
                >
                  <span className="rail-chip-dot" style={{ background: providerMeta(p).color }} />
                  {providerMeta(p).label}
                </button>
              );
            })}
          </div>
        )}

        <div className="rail-toggles">
          <button
            type="button"
            className={`rail-toggle${filters.sessionOnly ? " rail-toggle--active" : ""}`}
            onClick={onToggleSessionOnly}
          >
            ⚡ Active only
          </button>
          <button
            type="button"
            className="rail-openall"
            data-testid="open-all-button"
            title="Open every available session as a grid"
            disabled={model.availCount === 0}
            onClick={onOpenAllAvailable}
          >
            ⊞ Open all · {model.availCount}
          </button>
        </div>
      </div>

      <div className="rail-scroll">
        {isLoading ? (
          <RailSkeleton />
        ) : noRegistry ? (
          <RailNotice
            icon="◍"
            title="Empty registry"
            body="No instances registered. Add one with the CLI, then refresh."
            code="$ remo <provider> create"
          />
        ) : noCredentials ? (
          <RailNotice
            icon="🔑"
            title="No SSH credentials"
            body="Instances are registered but the service has no SSH identity to reach them."
            code="-v ~/.ssh:/home/remo/.ssh:ro"
            variant="warn"
          />
        ) : (
          model.groups.map((group) => (
            <RailInstance
              key={group.instance.instance_id}
              group={group}
              attached={attached}
              visible={visible}
              focusedId={focusedId}
              onSelect={(t, e) =>
                isModifierClick(e) ? workspace.addSession(t) : workspace.selectOnly(t)
              }
              onAdd={(t) => workspace.addSession(t)}
              onOpenAll={(ts) => workspace.openMany(ts)}
            />
          ))
        )}
      </div>

      <div className="rail-legend">
        <span>
          <span style={{ color: "var(--git-changes)" }}>●</span> changes
        </span>
        <span>
          <span style={{ color: "var(--git-sync)" }}>⇡</span> push
        </span>
        <span>
          <span style={{ color: "var(--git-sync)" }}>⇣</span> pull
        </span>
        <span>
          <span style={{ color: "var(--git-active)" }}>⚡</span> active
        </span>
      </div>
    </div>
  );
}

interface RailInstanceProps {
  group: RailGroup;
  attached: string[];
  visible: string[];
  focusedId: string | null;
  onSelect: (target: SessionTarget, e: MouseEvent) => void;
  onAdd: (target: SessionTarget) => void;
  onOpenAll: (targets: SessionTarget[]) => void;
}

function RailInstance({
  group,
  attached,
  visible,
  focusedId,
  onSelect,
  onAdd,
  onOpenAll,
}: RailInstanceProps): JSX.Element {
  const { instance, meta, status, error } = group;
  return (
    <div className="rail-inst">
      <div className="rail-inst-head">
        <span className="rail-inst-dot" style={{ background: meta.color }} />
        <span className="rail-inst-name">{instance.instance_name}</span>
        {instance.region && <span className="rail-inst-region">{instance.region}</span>}
        <span className="rail-inst-spacer" />
        <span
          className="rail-inst-status"
          style={{ color: status.color }}
          title={status.label}
        >
          <span
            className="rail-inst-status-dot"
            style={{
              background: status.color,
              animation: status.pulse ? "rpulse 1.6s ease infinite" : undefined,
            }}
          />
          {status.label}
        </span>
        {group.openableTargets.length > 0 && (
          <button
            type="button"
            className="rail-inst-openall"
            data-testid={`open-all-instance-${instance.instance_id}`}
            title="Open all on this instance"
            onClick={() => onOpenAll(group.openableTargets)}
          >
            ⊞
          </button>
        )}
      </div>

      {error && (
        <div className="rail-inst-error">
          <div className="rail-inst-error-title">
            {error.icon} {error.title}
          </div>
          <div className="rail-inst-error-msg">{error.message}</div>
          {error.hint && <code className="rail-inst-error-hint">{error.hint}</code>}
        </div>
      )}

      {group.isEmptyProjects && (
        <div className="rail-inst-empty">
          Reachable, no projects in <code>~/projects</code>
        </div>
      )}

      {group.rows.map((row) => (
        <RailSessionRow
          key={row.target.id}
          target={row.target}
          num={row.num}
          providerColor={meta.color}
          attached={attached.includes(row.target.id)}
          visible={visible.includes(row.target.id)}
          focused={focusedId === row.target.id}
          onSelect={onSelect}
          onAdd={onAdd}
        />
      ))}
    </div>
  );
}

interface RailSessionRowProps {
  target: SessionTarget;
  num: number | null;
  providerColor: string;
  attached: boolean;
  visible: boolean;
  focused: boolean;
  onSelect: (target: SessionTarget, e: MouseEvent) => void;
  onAdd: (target: SessionTarget) => void;
}

function RailSessionRow({
  target,
  num,
  attached,
  visible,
  focused,
  onSelect,
  onAdd,
}: RailSessionRowProps): JSX.Element {
  const mark = focused ? "▸" : visible ? "•" : attached ? "◦" : "";
  const rowClass = [
    "rail-row",
    visible ? "rail-row--visible" : "",
    focused ? "rail-row--focused" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={rowClass}
      data-testid={`session-row-${target.id}`}
      title={
        visible
          ? "In view · click to focus alone, ⌘-click to remove"
          : "Click to open · ⌘-click to add to grid"
      }
      onClick={(e) => onSelect(target, e)}
    >
      <span className="rail-row-mark">{mark}</span>
      <span className="rail-row-num">{num ?? ""}</span>
      <span className="rail-row-name">{target.project}</span>
      <span className="rail-row-glyphs">
        {target.git_dirty && (
          <span title="Uncommitted changes" style={{ color: "var(--git-changes)" }}>
            ●
          </span>
        )}
        {target.git_ahead > 0 && (
          <span title={`${target.git_ahead} to push`} style={{ color: "var(--git-sync)" }}>
            ⇡
          </span>
        )}
        {target.git_behind > 0 && (
          <span title={`${target.git_behind} to pull`} style={{ color: "var(--git-sync)" }}>
            ⇣
          </span>
        )}
        {target.zellij_state === "active" && (
          <span title="Active Zellij session" style={{ color: "var(--git-active)" }}>
            ⚡
          </span>
        )}
      </span>
      <button
        type="button"
        className="rail-row-add"
        data-testid={`add-to-grid-${target.id}`}
        title={visible ? "Remove from grid" : "Add to grid (⌘-click)"}
        onClick={(e) => {
          e.stopPropagation();
          onAdd(target);
        }}
      >
        {visible ? "−" : "+"}
      </button>
    </div>
  );
}

function RailSkeleton(): JSX.Element {
  return (
    <div className="rail-skeleton">
      {SKELETON_WIDTHS.map((w, i) => (
        <div className="rail-skeleton-row" key={i}>
          <span className="rail-skeleton-dot" />
          <span className="rail-skeleton-bar" style={{ width: w }} />
        </div>
      ))}
      <div className="rail-skeleton-note">
        <span className="rail-spin">⟳</span> discovering…
      </div>
    </div>
  );
}

interface RailNoticeProps {
  icon: string;
  title: string;
  body: string;
  code?: string;
  variant?: "default" | "warn";
}

function RailNotice({ icon, title, body, code, variant = "default" }: RailNoticeProps): JSX.Element {
  return (
    <div className={`rail-notice rail-notice--${variant}`}>
      <div className="rail-notice-icon">{icon}</div>
      <div className="rail-notice-title">{title}</div>
      <p className="rail-notice-body">{body}</p>
      {code && <code className="rail-notice-code">{code}</code>}
    </div>
  );
}
