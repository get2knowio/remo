// Pure builder for the session rail's grouped model. Centralized so the rail
// (for row numbers + grouping) and the keyboard 1–9 shortcut (for the flat
// openable list) agree on exactly which targets are numbered and in what order.

import type { DiscoveryInstance, InstanceStatus, SessionTarget } from "../api/client";
import { hostKey, type FavoriteEntry } from "../state/settings";
import { providerMeta, statusMeta, type ProviderMeta, type StatusMeta } from "./providerMeta";

export interface RailFilters {
  search: string;
  providerFilter: string | null;
  sessionOnly: boolean;
}

export interface RailPrefs {
  /** Hosts the user has collapsed, keyed by `instance_id`. */
  collapsedHostIds?: ReadonlySet<string>;
  /** Favorited targets, keyed by `SessionTarget.id` (settings store shape). */
  favorites?: Record<string, FavoriteEntry>;
}

export interface RailFavorite {
  /** The favorited target's id (also the favorites-store key). */
  id: string;
  project: string;
  /** The host's name, shown to disambiguate same-named projects. */
  hostLabel: string;
  providerColor: string;
  /** The live target, or null when it is absent from the current snapshot
   * (stale: rendered dimmed, unclickable, removal still possible). */
  target: SessionTarget | null;
  /** The SAME 1–9 number as this target's host-group row — favorites never
   * get numbers of their own (see the invariant below). */
  num: number | null;
  active: boolean;
  entry: FavoriteEntry;
}

export interface RailRow {
  target: SessionTarget;
  /** 1–9 when among the first nine openable targets, else null. */
  num: number | null;
  /**
   * Whether to show this row as having a live session (the ⚡).
   *
   * Discovery's `zellij_state` is a snapshot, and a session this console just
   * started is not in it until the next discovery run lands. But the console
   * knows something discovery doesn't: it is holding an open terminal on that
   * project, and `remo-host sessions attach` creates-or-attaches a Zellij
   * session — so a live terminal IS an active session, immediately and by
   * construction. Both sources feed this one flag so the ⚡ and the
   * "Active only" filter can never disagree about a row.
   */
  active: boolean;
  favorited: boolean;
}

export interface RailErrorInfo {
  icon: string;
  title: string;
  message: string;
  hint: string;
}

export interface RailGroup {
  instance: DiscoveryInstance;
  meta: ProviderMeta;
  status: StatusMeta;
  rows: RailRow[];
  openableTargets: SessionTarget[];
  isError: boolean;
  isEmptyProjects: boolean;
  error: RailErrorInfo | null;
  /** Effective collapse: the stored pref, overridden open by an active search
   * (else matches inside collapsed groups would be invisible). Rows/error/
   * empty-state stay populated regardless — the component decides what to
   * hide, and the collapsed badge derives from `rows`. */
  collapsed: boolean;
}

export interface RailModel {
  groups: RailGroup[];
  flatOpenable: SessionTarget[];
  availCount: number;
  /** Favorited targets, sorted by project then host label. A separate list,
   * not a RailGroup — a group requires an instance/status/error. */
  favorites: RailFavorite[];
}

const ERROR_HEADINGS: Partial<Record<InstanceStatus, { icon: string; title: string }>> = {
  auth_failed: { icon: "⛔", title: "SSH auth failed" },
  unreachable: { icon: "⚠", title: "Unreachable" },
  timeout: { icon: "⚠", title: "Timed out" },
  no_remo_host: { icon: "⬆", title: "Host tools missing" },
  incompatible_protocol: { icon: "⬆", title: "Host tools out of date" },
  malformed: { icon: "⚠", title: "Protocol error" },
};

function matchesSearch(instance: DiscoveryInstance, target: SessionTarget, q: string): boolean {
  if (!q) {
    return true;
  }
  return `${target.project} ${instance.instance_name} ${instance.instance_type}`
    .toLowerCase()
    .includes(q);
}

export function buildRailModel(
  instances: DiscoveryInstance[],
  targets: SessionTarget[],
  filters: RailFilters,
  /** Target ids this console currently holds a connected terminal for. */
  liveTargetIds: ReadonlySet<string> = new Set(),
  prefs: RailPrefs = {},
): RailModel {
  const q = filters.search.trim().toLowerCase();
  const favEntries = prefs.favorites ?? {};
  const isActive = (t: SessionTarget): boolean =>
    t.zellij_state === "active" || liveTargetIds.has(t.id);
  const byInstance = new Map<string, SessionTarget[]>();
  for (const t of targets) {
    const key = hostKey(t.instance_type, t.instance_name);
    const list = byInstance.get(key) ?? [];
    list.push(t);
    byInstance.set(key, list);
  }

  const groups: RailGroup[] = [];
  const flatOpenable: SessionTarget[] = [];
  let availCount = 0;
  // The 1–9 numbers as assigned to host-group rows; favorites look their own
  // number up here so a starred row and its twin always agree.
  const numById = new Map<string, number>();

  for (const instance of instances) {
    if (filters.providerFilter && instance.instance_type !== filters.providerFilter) {
      continue;
    }

    const key = hostKey(instance.instance_type, instance.instance_name);
    const instTargets = byInstance.get(key) ?? [];
    const openable = instance.status === "ok";

    const filtered = instTargets.filter(
      (t) => matchesSearch(instance, t, q) && (!filters.sessionOnly || isActive(t)),
    );

    const instMatches =
      !q || `${instance.instance_name} ${instance.instance_type}`.toLowerCase().includes(q);
    if (q && !instMatches && filtered.length === 0) {
      continue;
    }

    const rows: RailRow[] = filtered.map((target) => {
      let num: number | null = null;
      if (openable) {
        flatOpenable.push(target);
        availCount += 1;
        num = flatOpenable.length <= 9 ? flatOpenable.length : null;
      }
      if (num !== null) {
        numById.set(target.id, num);
      }
      return { target, num, active: isActive(target), favorited: target.id in favEntries };
    });

    const isError = instance.status !== "ok" && instance.error != null;
    const isEmptyProjects = openable && instTargets.length === 0;

    let error: RailErrorInfo | null = null;
    if (isError && instance.error) {
      const heading = ERROR_HEADINGS[instance.status] ?? { icon: "⚠", title: "Error" };
      error = {
        icon: heading.icon,
        title: heading.title,
        message: instance.error.message,
        hint: instance.error.remediation,
      };
    }

    groups.push({
      instance,
      meta: providerMeta(instance.instance_type),
      status: statusMeta(instance.status),
      rows,
      openableTargets: openable ? filtered : [],
      isError,
      isEmptyProjects,
      error,
      collapsed: q === "" && (prefs.collapsedHostIds?.has(instance.instance_id) ?? false),
    });
  }

  const targetById = new Map(targets.map((t) => [t.id, t]));
  const favorites: RailFavorite[] = [];
  for (const [id, entry] of Object.entries(favEntries)) {
    // A stale favorite (target absent from the snapshot) is filtered via its
    // stored entry, so it stays subject to the same three rail filters.
    const target = targetById.get(id) ?? null;
    const project = target?.project ?? entry.project;
    const instanceType = target?.instance_type ?? entry.instanceType;
    const hostLabel = target?.instance_name ?? entry.instanceName;
    if (filters.providerFilter && instanceType !== filters.providerFilter) {
      continue;
    }
    const active = target !== null && isActive(target);
    if (filters.sessionOnly && !active) {
      continue;
    }
    if (q && !`${project} ${hostLabel} ${instanceType}`.toLowerCase().includes(q)) {
      continue;
    }
    favorites.push({
      id,
      project,
      hostLabel,
      providerColor: providerMeta(instanceType).color,
      target,
      num: numById.get(id) ?? null,
      active,
      entry,
    });
  }
  favorites.sort(
    (a, b) => a.project.localeCompare(b.project) || a.hostLabel.localeCompare(b.hostLabel),
  );

  return { groups, flatOpenable, availCount, favorites };
}
