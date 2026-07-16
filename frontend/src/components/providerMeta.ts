// Shared provider + instance-status presentation helpers for the console.
// Colors resolve to the CSS custom properties defined in theme/tokens.css.

import type { DiscoveryInstance, InstanceStatus } from "../api/client";

export interface ProviderMeta {
  label: string;
  /** A CSS color value (a var() reference) for the provider accent dot. */
  color: string;
}

const PROVIDERS: Record<string, ProviderMeta> = {
  aws: { label: "AWS", color: "var(--prov-aws)" },
  hetzner: { label: "Hetzner", color: "var(--prov-hetzner)" },
  proxmox: { label: "Proxmox", color: "var(--prov-proxmox)" },
  incus: { label: "Incus", color: "var(--prov-incus)" },
};

export function providerMeta(type: string): ProviderMeta {
  return PROVIDERS[type] ?? { label: type || "?", color: "var(--prov-unknown)" };
}

export interface StatusMeta {
  label: string;
  color: string;
  /** true for warn-ish states that should pulse (needs operator action). */
  pulse: boolean;
}

export function statusMeta(status: InstanceStatus): StatusMeta {
  switch (status) {
    case "ok":
      return { label: "online", color: "var(--ok)", pulse: false };
    case "auth_failed":
      return { label: "auth failed", color: "var(--danger)", pulse: false };
    case "unreachable":
      return { label: "unreachable", color: "var(--danger)", pulse: false };
    case "timeout":
      return { label: "timeout", color: "var(--danger)", pulse: false };
    case "no_remo_host":
      return { label: "update req.", color: "var(--warn)", pulse: true };
    case "incompatible_protocol":
      return { label: "update req.", color: "var(--warn)", pulse: true };
    case "malformed":
      return { label: "error", color: "var(--warn)", pulse: false };
    default:
      return { label: status, color: "var(--text-dim)", pulse: false };
  }
}

/** An instance a user can open sessions on right now. */
export function isInstanceOpenable(instance: DiscoveryInstance): boolean {
  return instance.status === "ok";
}
