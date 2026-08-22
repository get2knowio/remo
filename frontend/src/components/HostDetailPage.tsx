// Full-screen host detail overlay (plan §2.4) — the SettingsPage precedent:
// absolute inset 0, z-index 75, ‹ Back topbar, no router. Opened by clicking
// a host NAME in the rail (the Part 1 header contract: chevron/header =
// collapse, name = detail).
//
// Read-only always: live stats (useHostStats) + the projects table. Mutating
// affordances (clone / rebuild / delete / host shell) render ONLY when the
// service advertises `features.host_admin`; when the host's own capability
// operations[] predates the maintenance verbs (or stats 409s), the action
// sections are replaced by a capability nudge naming the exact
// upgrade/configure command.

import { useMemo, useState } from "react";
import {
  ApiError,
  cloneProject,
  deleteProject,
  rebuildProject,
  type DiscoveryInstance,
  type JobAccepted,
  type SessionTarget,
} from "../api/client";
import { useDiscovery } from "../state/discovery";
import { useHealth } from "../state/health";
import { useHostStats } from "../state/hostStats";
import { hostKey } from "../state/settings";
import { DeleteProjectDialog } from "./DeleteProjectDialog";
import { HostShellPanel } from "./HostShellPanel";
import { JobProgressPanel } from "./JobProgressPanel";
import { providerMeta, statusMeta } from "./providerMeta";
import { RebuildConfirmDialog } from "./RebuildConfirmDialog";
import "./HostDetailPage.css";

interface HostDetailPageProps {
  instanceId: string;
  onClose: () => void;
  /** Open a project's terminal in the workspace (the caller also closes this
   * overlay so the terminal is immediately visible). */
  onOpenTarget: (target: SessionTarget) => void;
}

/** The remo-host operations the maintenance surface needs (contract §ops). */
const MAINTENANCE_OPS = ["projects.clone", "projects.delete", "projects.rebuild", "jobs.status"];

/**
 * The command that brings a host's tools up to date. Console-owned mapping:
 * an added (`type="ssh"`) host is configured via `remo configure`, a provider
 * host via its `upgrade` verb (the five configure plays are not
 * interchangeable — 022).
 */
function upgradeCommand(instance: DiscoveryInstance): string {
  return instance.instance_type === "ssh"
    ? `remo configure ${instance.instance_name}`
    : `remo ${instance.instance_type} upgrade ${instance.instance_name}`;
}

function formatBytes(n: number): string {
  if (n >= 1024 ** 4) {
    return `${(n / 1024 ** 4).toFixed(1)} TiB`;
  }
  if (n >= 1024 ** 3) {
    return `${(n / 1024 ** 3).toFixed(1)} GiB`;
  }
  if (n >= 1024 ** 2) {
    return `${(n / 1024 ** 2).toFixed(0)} MiB`;
  }
  return `${(n / 1024).toFixed(0)} KiB`;
}

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86_400);
  const h = Math.floor((seconds % 86_400) / 3_600);
  const m = Math.floor((seconds % 3_600) / 60);
  if (d > 0) {
    return `up ${d}d ${h}h`;
  }
  if (h > 0) {
    return `up ${h}h ${m}m`;
  }
  return `up ${m}m`;
}

function pct(used: number, total: number): number {
  if (total <= 0) {
    return 0;
  }
  return Math.min(100, Math.max(0, (used / total) * 100));
}

/** A labeled meter bar with the absolute numbers alongside — never a bare %. */
function StatBar({
  label,
  value,
  detail,
  sub,
  warnAt = 85,
  testid,
}: {
  label: string;
  value: number;
  detail: string;
  sub?: string;
  warnAt?: number;
  testid?: string;
}): JSX.Element {
  return (
    <div className="hd-stat" data-testid={testid}>
      <div className="hd-stat-head">
        <span className="hd-stat-label">{label}</span>
        <span className="hd-stat-detail">{detail}</span>
      </div>
      <div className="hd-stat-track">
        <div
          className={`hd-stat-fill${value >= warnAt ? " hd-stat-fill--warn" : ""}`}
          style={{ width: `${value}%` }}
        />
      </div>
      {sub && <div className="hd-stat-sub">{sub}</div>}
    </div>
  );
}

export function HostDetailPage({
  instanceId,
  onClose,
  onOpenTarget,
}: HostDetailPageProps): JSX.Element {
  const discovery = useDiscovery();
  const health = useHealth();
  const { stats, stale, unsupported } = useHostStats(instanceId);

  const [shellOpen, setShellOpen] = useState(false);
  const [job, setJob] = useState<JobAccepted | null>(null);
  const [rebuildFor, setRebuildFor] = useState<SessionTarget | null>(null);
  const [deleteFor, setDeleteFor] = useState<SessionTarget | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [repo, setRepo] = useState("");
  const [cloneName, setCloneName] = useState("");
  const [cloneBusy, setCloneBusy] = useState(false);
  const [cloneError, setCloneError] = useState<string | null>(null);

  const instance = discovery.instances.find((i) => i.instance_id === instanceId);

  const projects = useMemo(() => {
    if (!instance) {
      return [] as SessionTarget[];
    }
    const key = hostKey(instance.instance_type, instance.instance_name);
    return discovery.targets
      .filter((t) => hostKey(t.instance_type, t.instance_name) === key)
      .sort((a, b) => a.project.localeCompare(b.project));
  }, [discovery.targets, instance]);

  if (!instance) {
    return (
      <div className="hd" data-testid="host-detail-page">
        <div className="hd-topbar">
          <button type="button" className="hd-back" data-testid="host-detail-back" onClick={onClose}>
            ‹ Back
          </button>
          <span className="hd-title">Host</span>
        </div>
        <div className="hd-scroll">
          <div className="hd-inner">
            <p className="hd-sub">This host is no longer in the discovery snapshot.</p>
          </div>
        </div>
      </div>
    );
  }

  const meta = providerMeta(instance.instance_type);
  const status = statusMeta(instance.status);
  const capability = instance.capability ?? null;
  const hostAdmin = health.hostAdmin;
  const ops = capability?.operations ?? [];
  const missingOps = MAINTENANCE_OPS.filter((op) => !ops.includes(op));
  // Actions need both the feature gate AND a host whose tools carry the verbs.
  const canMaintain = hostAdmin && missingOps.length === 0;
  // The nudge replaces the action sections: gate on, host reachable, verbs
  // missing — i.e. an upgrade would actually fix it. A stats 409 is the same
  // condition observed the other way; its envelope's remediation is preferred
  // because the server words the exact command for this host type.
  const showNudge = hostAdmin && !canMaintain && instance.status === "ok";
  const nudgeText =
    unsupported?.remediation ||
    `This host's tools predate project maintenance. Run: ${upgradeCommand(instance)}`;

  const refreshInstance = (): void => {
    void discovery.refresh(instanceId);
  };

  const submitClone = async (): Promise<void> => {
    const trimmed = repo.trim();
    if (!trimmed || cloneBusy) {
      return;
    }
    setCloneBusy(true);
    setCloneError(null);
    try {
      const accepted = await cloneProject(instanceId, trimmed, cloneName.trim() || undefined);
      setJob(accepted);
      setRepo("");
      setCloneName("");
    } catch (error) {
      if (error instanceof ApiError) {
        setCloneError(error.remediation ? `${error.message} — ${error.remediation}` : error.message);
      } else {
        setCloneError(error instanceof Error ? error.message : "Clone failed");
      }
    } finally {
      setCloneBusy(false);
    }
  };

  const confirmRebuild = async (target: SessionTarget, noCache: boolean): Promise<void> => {
    setRebuildFor(null);
    try {
      const accepted = await rebuildProject(instanceId, target.project, noCache);
      setJob(accepted);
    } catch (error) {
      setCloneError(error instanceof ApiError ? error.message : "Rebuild failed");
    }
  };

  const confirmDelete = async (target: SessionTarget): Promise<void> => {
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deleteProject(instanceId, target.project);
      setDeleteFor(null);
      refreshInstance();
    } catch (error) {
      setDeleteError(error instanceof ApiError ? error.message : "Delete failed");
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="hd" data-testid="host-detail-page">
      <div className="hd-topbar">
        <button type="button" className="hd-back" data-testid="host-detail-back" onClick={onClose}>
          ‹ Back
        </button>
        <span className="hd-provider" style={{ color: meta.color }}>
          <span className="hd-provider-dot" style={{ background: meta.color }} />
          {meta.label}
        </span>
        <span className="hd-title">{instance.instance_name}</span>
        {instance.region && <span className="hd-region">{instance.region}</span>}
        <span
          className="hd-status"
          data-testid="host-detail-status"
          style={{ color: status.color, borderColor: status.color }}
        >
          {status.label}
        </span>
        <span className="hd-spacer" />
        {capability && (
          <span className="hd-meta" title="remo-host tools / protocol version">
            tools {capability.host_tools_version} · v{capability.protocol_version}
          </span>
        )}
        {stats && <span className="hd-meta">{formatUptime(stats.uptime_s)}</span>}
        <button
          type="button"
          className="hd-btn"
          data-testid="host-detail-refresh"
          disabled={discovery.isRefreshing}
          onClick={refreshInstance}
        >
          {discovery.isRefreshing ? "Refreshing…" : "⟳ Refresh"}
        </button>
        {hostAdmin && (
          <button
            type="button"
            className={`hd-btn${shellOpen ? " hd-btn--active" : ""}`}
            data-testid="host-shell-toggle"
            onClick={() => setShellOpen((v) => !v)}
          >
            &gt;_ Shell
          </button>
        )}
      </div>

      <div className="hd-scroll">
        <div className="hd-inner">
          {shellOpen && hostAdmin && (
            <HostShellPanel
              instanceId={instanceId}
              instanceName={instance.instance_name}
              onClose={() => setShellOpen(false)}
            />
          )}

          {/* ---- Stats ---- */}
          <section>
            <div className="hd-heading">
              Stats
              {stale && stats && (
                <span className="hd-stale" data-testid="stats-stale-badge">
                  stale — last poll failed
                </span>
              )}
            </div>
            {unsupported ? (
              <div className="hd-nudge" data-testid="stats-nudge">
                <div className="hd-nudge-title">⬆ Host tools out of date</div>
                <p>{unsupported.message}</p>
                <code>{unsupported.remediation || upgradeCommand(instance)}</code>
              </div>
            ) : stats === null ? (
              <p className="hd-sub">
                {stale ? "Stats unavailable — the host did not answer." : "Reading host stats…"}
              </p>
            ) : (
              <div className="hd-stats" data-testid="stats-strip">
                <StatBar
                  label={`CPU · ${stats.cpu_count} cores`}
                  value={stats.cpu_used_pct}
                  detail={`${stats.cpu_used_pct.toFixed(0)}%`}
                  sub={`load ${stats.load_1.toFixed(2)} / ${stats.load_5.toFixed(2)} / ${stats.load_15.toFixed(2)}`}
                  testid="stat-cpu"
                />
                <StatBar
                  label="Memory"
                  value={pct(stats.mem_used, stats.mem_total)}
                  detail={`${formatBytes(stats.mem_used)} / ${formatBytes(stats.mem_total)}`}
                  testid="stat-mem"
                />
                {stats.swap_total > 0 && (
                  <StatBar
                    label="Swap"
                    value={pct(stats.swap_used, stats.swap_total)}
                    detail={`${formatBytes(stats.swap_used)} / ${formatBytes(stats.swap_total)}`}
                    testid="stat-swap"
                  />
                )}
                {stats.disks.map((disk) => (
                  <StatBar
                    key={disk.mount}
                    label={`Disk ${disk.mount}`}
                    value={pct(disk.used_bytes, disk.size_bytes)}
                    detail={`${formatBytes(disk.used_bytes)} / ${formatBytes(disk.size_bytes)}`}
                    testid={`stat-disk-${disk.mount}`}
                  />
                ))}
                {stats.temps.length > 0 && (
                  <div className="hd-stat" data-testid="stat-temps">
                    <div className="hd-stat-head">
                      <span className="hd-stat-label">Temperatures</span>
                    </div>
                    <div className="hd-temps">
                      {stats.temps.map((t, i) => (
                        <span className="hd-temp" key={`${t.name}-${t.label}-${i}`}>
                          <span className="hd-temp-name">{t.label || t.name}</span>
                          <span className={t.celsius >= 80 ? "hd-temp--hot" : ""}>
                            {t.celsius.toFixed(0)}°C
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* ---- Projects ---- */}
          <section>
            <div className="hd-heading">Projects</div>
            {projects.length === 0 ? (
              <p className="hd-sub">No projects discovered on this host.</p>
            ) : (
              <table className="hd-table" data-testid="projects-table">
                <tbody>
                  {projects.map((t) => (
                    <tr key={t.id} data-testid={`project-row-${t.id}`}>
                      <td className="hd-td-name">{t.project}</td>
                      <td className="hd-td-chips">
                        {t.has_devcontainer && (
                          <span
                            className={`hd-chip${t.devcontainer_running === "running" ? " hd-chip--on" : ""}`}
                            title={`devcontainer ${t.devcontainer_running}`}
                          >
                            ▣ {t.devcontainer_running === "running" ? "container up" : "container"}
                          </span>
                        )}
                        {t.zellij_state === "active" && (
                          <span className="hd-chip hd-chip--active" title="Active Zellij session">
                            ⚡ session
                          </span>
                        )}
                      </td>
                      <td className="hd-td-glyphs">
                        {/* The rail's git glyph vocabulary (SessionRail legend). */}
                        {t.git_dirty && (
                          <span title="Uncommitted changes" style={{ color: "var(--git-changes)" }}>
                            ●
                          </span>
                        )}
                        {t.git_ahead > 0 && (
                          <span title={`${t.git_ahead} to push`} style={{ color: "var(--git-sync)" }}>
                            ⇡
                          </span>
                        )}
                        {t.git_behind > 0 && (
                          <span title={`${t.git_behind} to pull`} style={{ color: "var(--git-sync)" }}>
                            ⇣
                          </span>
                        )}
                      </td>
                      <td className="hd-td-actions">
                        <button
                          type="button"
                          className="hd-btn"
                          data-testid={`open-project-${t.id}`}
                          onClick={() => onOpenTarget(t)}
                        >
                          Open
                        </button>
                        {canMaintain && t.has_devcontainer && (
                          <button
                            type="button"
                            className="hd-btn"
                            data-testid={`rebuild-project-${t.id}`}
                            onClick={() => setRebuildFor(t)}
                          >
                            Rebuild
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {job && (
            <JobProgressPanel
              instanceId={instanceId}
              job={job}
              onFinished={refreshInstance}
              onDismiss={() => setJob(null)}
            />
          )}

          {/* ---- Maintenance (host-admin-gated; nudge when tools predate it) ---- */}
          {showNudge && (
            <div className="hd-nudge" data-testid="capability-nudge">
              <div className="hd-nudge-title">⬆ Host tools out of date</div>
              <p>
                Project maintenance (clone / rebuild / delete) needs newer host tools on{" "}
                <code>{instance.instance_name}</code>.
              </p>
              <code>{nudgeText}</code>
            </div>
          )}

          {canMaintain && (
            <section data-testid="new-project-section">
              <div className="hd-heading">+ New project</div>
              <p className="hd-sub">
                Clone a GitHub repository into this host&rsquo;s projects directory. Private
                repositories need <code>gh auth login</code> on the host first.
              </p>
              <form
                className="hd-clone"
                onSubmit={(e) => {
                  e.preventDefault();
                  void submitClone();
                }}
              >
                <input
                  className="hd-input"
                  value={repo}
                  data-testid="clone-repo-input"
                  placeholder="owner/repo or https://github.com/owner/repo"
                  onInput={(e) => setRepo((e.target as HTMLInputElement).value)}
                />
                <input
                  className="hd-input hd-input--name"
                  value={cloneName}
                  data-testid="clone-name-input"
                  placeholder="name (optional)"
                  onInput={(e) => setCloneName((e.target as HTMLInputElement).value)}
                />
                <button
                  type="submit"
                  className="hd-btn hd-btn--primary"
                  data-testid="clone-submit"
                  disabled={!repo.trim() || cloneBusy}
                >
                  {cloneBusy ? "Starting…" : "Clone"}
                </button>
              </form>
              {cloneError && <div className="hd-error">{cloneError}</div>}
            </section>
          )}

          {canMaintain && projects.length > 0 && (
            <section className="hd-danger" data-testid="danger-zone">
              <div className="hd-heading hd-heading--danger">Danger zone</div>
              {projects.map((t) => (
                <div className="hd-danger-row" key={t.id}>
                  <span className="hd-danger-name">{t.project}</span>
                  <span className="hd-danger-note">
                    Removes the directory, containers and session from the host.
                  </span>
                  <button
                    type="button"
                    className="hd-btn hd-btn--danger"
                    data-testid={`delete-project-${t.id}`}
                    onClick={() => {
                      setDeleteError(null);
                      setDeleteFor(t);
                    }}
                  >
                    Delete
                  </button>
                </div>
              ))}
            </section>
          )}
        </div>
      </div>

      {rebuildFor && (
        <RebuildConfirmDialog
          project={rebuildFor.project}
          target={rebuildFor}
          onConfirm={(noCache) => void confirmRebuild(rebuildFor, noCache)}
          onCancel={() => setRebuildFor(null)}
        />
      )}
      {deleteFor && (
        <DeleteProjectDialog
          project={deleteFor.project}
          dirty={deleteFor.git_dirty || deleteFor.git_ahead > 0}
          busy={deleteBusy}
          error={deleteError}
          onConfirm={() => void confirmDelete(deleteFor)}
          onCancel={() => setDeleteFor(null)}
        />
      )}
    </div>
  );
}
