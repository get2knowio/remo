// The left session rail: search/filter, provider chips, "active only" toggle,
// "open all", instance-grouped session rows (with git/zellij glyphs and
// add-to-grid), and a glyph legend. Presentational — filter state and the
// rail model are owned by AppShell.

import type { MouseEvent } from "react";
import type { SessionTarget } from "../api/client";
import type { FavoriteEntry } from "../state/settings";
import type { UseWorkspaceResult } from "../state/workspace";
import { providerMeta } from "./providerMeta";
import type { RailFavorite, RailFilters, RailGroup, RailModel } from "./railModel";
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
  onToggleHostCollapsed: (instanceId: string) => void;
  onToggleFavorite: (id: string, entry: FavoriteEntry) => void;
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
  onToggleHostCollapsed,
  onToggleFavorite,
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
          <>
            {model.favorites.length > 0 && (
              <div className="rail-inst" data-testid="rail-favorites">
                <div className="rail-inst-head">
                  <span className="rail-fav-star">★</span>
                  <span className="rail-inst-name">Favorites</span>
                </div>
                {model.favorites.map((fav) => (
                  <RailFavoriteRow
                    key={`fav-row-${fav.id}`}
                    fav={fav}
                    attached={attached}
                    visible={visible}
                    focusedId={focusedId}
                    onSelect={(t, e) =>
                      isModifierClick(e) ? workspace.addSession(t) : workspace.selectOnly(t)
                    }
                    onAdd={(t) => workspace.addSession(t)}
                    onToggleFavorite={onToggleFavorite}
                  />
                ))}
              </div>
            )}
            {model.groups.map((group) => (
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
                onToggleCollapsed={onToggleHostCollapsed}
                onToggleFavorite={onToggleFavorite}
              />
            ))}
          </>
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
  onToggleCollapsed: (instanceId: string) => void;
  onToggleFavorite: (id: string, entry: FavoriteEntry) => void;
}

function RailInstance({
  group,
  attached,
  visible,
  focusedId,
  onSelect,
  onAdd,
  onOpenAll,
  onToggleCollapsed,
  onToggleFavorite,
}: RailInstanceProps): JSX.Element {
  const { instance, meta, status, error } = group;
  const bodyId = `rail-inst-body-${instance.instance_id}`;
  const activeCount = group.rows.filter((r) => r.active).length;
  return (
    <div className="rail-inst">
      {/* The whole header is a pointer convenience for the caret's toggle;
          every other control in it stops propagation (the Part 1 contract:
          chevron/header = collapse, name = detail later). */}
      <div
        className="rail-inst-head rail-inst-head--clickable"
        onClick={() => onToggleCollapsed(instance.instance_id)}
      >
        <button
          type="button"
          className="rail-inst-caret"
          data-testid={`collapse-toggle-${instance.instance_id}`}
          title={group.collapsed ? "Expand" : "Collapse"}
          aria-expanded={!group.collapsed}
          aria-controls={bodyId}
          onClick={(e) => {
            e.stopPropagation();
            onToggleCollapsed(instance.instance_id);
          }}
        >
          {group.collapsed ? "›" : "⌄"}
        </button>
        <span className="rail-inst-dot" style={{ background: meta.color }} />
        {/* Part 2 seam: becomes a button opening the host detail page. */}
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
        {group.collapsed && (
          <span
            className="rail-inst-count"
            data-testid={`collapsed-count-${instance.instance_id}`}
            title={`${group.rows.length} sessions${activeCount > 0 ? `, ${activeCount} active` : ""}`}
          >
            {group.rows.length}
            {activeCount > 0 && (
              <span style={{ color: "var(--git-active)" }}> ⚡{activeCount}</span>
            )}
          </span>
        )}
        {group.openableTargets.length > 0 && (
          <button
            type="button"
            className="rail-inst-openall"
            data-testid={`open-all-instance-${instance.instance_id}`}
            title="Open all on this instance"
            onClick={(e) => {
              e.stopPropagation();
              onOpenAll(group.openableTargets);
            }}
          >
            ⊞
          </button>
        )}
      </div>

      <div id={bodyId}>
        {!group.collapsed && (
          <>
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
                providerColor={meta.color}
                active={row.active}
                favorited={row.favorited}
                attached={attached.includes(row.target.id)}
                visible={visible.includes(row.target.id)}
                focused={focusedId === row.target.id}
                onSelect={onSelect}
                onAdd={onAdd}
                onToggleFavorite={onToggleFavorite}
              />
            ))}
          </>
        )}
      </div>
    </div>
  );
}

interface RailSessionRowProps {
  target: SessionTarget;
  providerColor: string;
  /** Has a live session — discovered, or one this console is attached to. */
  active: boolean;
  favorited: boolean;
  attached: boolean;
  visible: boolean;
  focused: boolean;
  onSelect: (target: SessionTarget, e: MouseEvent) => void;
  onAdd: (target: SessionTarget) => void;
  onToggleFavorite: (id: string, entry: FavoriteEntry) => void;
}

function RailSessionRow({
  target,
  active,
  favorited,
  attached,
  visible,
  focused,
  onSelect,
  onAdd,
  onToggleFavorite,
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
        {active && (
          <span title="Active Zellij session" style={{ color: "var(--git-active)" }}>
            ⚡
          </span>
        )}
      </span>
      <button
        type="button"
        className={`rail-row-fav${favorited ? " rail-row-fav--on" : ""}`}
        data-testid={`fav-toggle-${target.id}`}
        title={favorited ? "Remove from favorites" : "Add to favorites"}
        aria-pressed={favorited}
        onClick={(e) => {
          e.stopPropagation();
          onToggleFavorite(target.id, {
            project: target.project,
            instanceType: target.instance_type,
            instanceName: target.instance_name,
          });
        }}
      >
        {favorited ? "★" : "☆"}
      </button>
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

interface RailFavoriteRowProps {
  fav: RailFavorite;
  attached: string[];
  visible: string[];
  focusedId: string | null;
  onSelect: (target: SessionTarget, e: MouseEvent) => void;
  onAdd: (target: SessionTarget) => void;
  onToggleFavorite: (id: string, entry: FavoriteEntry) => void;
}

function RailFavoriteRow({
  fav,
  attached,
  visible,
  focusedId,
  onSelect,
  onAdd,
  onToggleFavorite,
}: RailFavoriteRowProps): JSX.Element {
  const { target } = fav;
  const stale = target === null;
  const isVisible = target !== null && visible.includes(target.id);
  const isFocused = target !== null && focusedId === target.id;
  const mark = isFocused ? "▸" : isVisible ? "•" : target !== null && attached.includes(target.id) ? "◦" : "";
  const rowClass = [
    "rail-row",
    stale ? "rail-row--stale" : "",
    isVisible ? "rail-row--visible" : "",
    isFocused ? "rail-row--focused" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={rowClass}
      data-testid={`fav-row-${fav.id}`}
      title={
        stale
          ? "Host unavailable — favorite kept"
          : isVisible
            ? "In view · click to focus alone, ⌘-click to remove"
            : "Click to open · ⌘-click to add to grid"
      }
      onClick={target === null ? undefined : (e) => onSelect(target, e)}
    >
      <span className="rail-row-mark">{mark}</span>
      <span className="rail-row-name">{fav.project}</span>
      <span className="rail-row-host" style={{ color: fav.providerColor }}>
        {fav.hostLabel}
      </span>
      <span className="rail-row-glyphs">
        {fav.active && (
          <span title="Active Zellij session" style={{ color: "var(--git-active)" }}>
            ⚡
          </span>
        )}
      </span>
      <button
        type="button"
        className="rail-row-fav rail-row-fav--on"
        data-testid={`fav-row-toggle-${fav.id}`}
        title="Remove from favorites"
        aria-pressed
        onClick={(e) => {
          e.stopPropagation();
          onToggleFavorite(fav.id, fav.entry);
        }}
      >
        ★
      </button>
      {target !== null && (
        <button
          type="button"
          className="rail-row-add"
          data-testid={`fav-add-${fav.id}`}
          title={isVisible ? "Remove from grid" : "Add to grid (⌘-click)"}
          onClick={(e) => {
            e.stopPropagation();
            onAdd(target);
          }}
        >
          {isVisible ? "−" : "+"}
        </button>
      )}
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
