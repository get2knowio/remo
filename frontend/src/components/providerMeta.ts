// Shared provider + instance-status presentation helpers for the console.
// Colors resolve to the CSS custom properties defined in theme/tokens.css.

import type { DiscoveryInstance, InstanceStatus } from "../api/client";
import type { components } from "../api/generated/schema";

// The known/closed provider vocabulary, derived from the generated OpenAPI
// schema rather than re-declared by hand — see PROVIDERS below. The wire
// field (`instance_type`) stays genuinely open (a third-party provider is
// valid data), which is why `providerMeta`'s parameter is plain `string`.
type KnownProviderType = components["schemas"]["KnownProviderType"];

export interface ProviderMeta {
  label: string;
  /** A CSS color value (a var() reference) for the provider accent dot. */
  color: string;
}

// A Record keyed by the schema-derived union is checked exhaustively by tsc:
// removing (or failing to add) a key here is a compile error, so a new
// first-party provider can't silently fall through to the "unknown" fallback.
const PROVIDERS: Record<KnownProviderType, ProviderMeta> = {
  aws: { label: "AWS", color: "var(--prov-aws)" },
  hetzner: { label: "Hetzner", color: "var(--prov-hetzner)" },
  proxmox: { label: "Proxmox", color: "var(--prov-proxmox)" },
  incus: { label: "Incus", color: "var(--prov-incus)" },
};

export function providerMeta(type: string): ProviderMeta {
  return PROVIDERS[type as KnownProviderType] ?? { label: type || "?", color: "var(--prov-unknown)" };
}

export interface StatusMeta {
  label: string;
  color: string;
  /** true for warn-ish states that should pulse (needs operator action). */
  pulse: boolean;
}

// A Record keyed by the schema-derived InstanceStatus union is checked
// exhaustively by tsc: a new status value added server-side and mirrored into
// the generated schema becomes a compile error here ("Property '...' is
// missing") until this mapping is updated — catching "we forgot to handle the
// new state" at build time (FR-013).
const STATUS_META: Record<InstanceStatus, StatusMeta> = {
  ok: { label: "online", color: "var(--ok)", pulse: false },
  auth_failed: { label: "auth failed", color: "var(--danger)", pulse: false },
  unreachable: { label: "unreachable", color: "var(--danger)", pulse: false },
  timeout: { label: "timeout", color: "var(--danger)", pulse: false },
  no_remo_host: { label: "update req.", color: "var(--warn)", pulse: true },
  incompatible_protocol: { label: "update req.", color: "var(--warn)", pulse: true },
  malformed: { label: "error", color: "var(--warn)", pulse: false },
};

// The `?? …` fallback below is NOT dead code despite STATUS_META being typed
// exhaustive: TypeScript's exhaustiveness is compile-time only. At runtime an
// object index with a string outside the compiled union (e.g. an old browser
// bundle talking to a newer service that added a status value) legitimately
// evaluates to `undefined`. Do NOT delete this branch to "achieve
// exhaustiveness" — that would make an off-union value throw instead of
// rendering gracefully (FR-013a, SC-010).
export function statusMeta(status: InstanceStatus): StatusMeta {
  return STATUS_META[status] ?? { label: status, color: "var(--text-dim)", pulse: false };
}

/** An instance a user can open sessions on right now. */
export function isInstanceOpenable(instance: DiscoveryInstance): boolean {
  return instance.status === "ok";
}
