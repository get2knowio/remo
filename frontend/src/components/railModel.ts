// Pure builder for the session rail's grouped model. Centralized so the rail
// (for row numbers + grouping) and the keyboard 1–9 shortcut (for the flat
// openable list) agree on exactly which targets are numbered and in what order.

import type { DiscoveryInstance, InstanceStatus, SessionTarget } from "../api/client";
import { providerMeta, statusMeta, type ProviderMeta, type StatusMeta } from "./providerMeta";

export interface RailFilters {
  search: string;
  providerFilter: string | null;
  sessionOnly: boolean;
}

export interface RailRow {
  target: SessionTarget;
  /** 1–9 when among the first nine openable targets, else null. */
  num: number | null;
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
}

export interface RailModel {
  groups: RailGroup[];
  flatOpenable: SessionTarget[];
  availCount: number;
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
): RailModel {
  const q = filters.search.trim().toLowerCase();
  const byInstance = new Map<string, SessionTarget[]>();
  for (const t of targets) {
    const key = `${t.instance_type}::${t.instance_name}`;
    const list = byInstance.get(key) ?? [];
    list.push(t);
    byInstance.set(key, list);
  }

  const groups: RailGroup[] = [];
  const flatOpenable: SessionTarget[] = [];
  let availCount = 0;

  for (const instance of instances) {
    if (filters.providerFilter && instance.instance_type !== filters.providerFilter) {
      continue;
    }

    const key = `${instance.instance_type}::${instance.instance_name}`;
    const instTargets = byInstance.get(key) ?? [];
    const openable = instance.status === "ok";

    const filtered = instTargets.filter(
      (t) =>
        matchesSearch(instance, t, q) && (!filters.sessionOnly || t.zellij_state === "active"),
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
      return { target, num };
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
    });
  }

  return { groups, flatOpenable, availCount };
}
