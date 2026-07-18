// Root gate (011-web-adopt, FR-004 / research R12).
//
// While GET /api/v1/ready reports status "unconfigured", only the
// AwaitingAdoption page renders — the console shell (and with it the
// discovery store, session rail, and terminals) never mounts, so no instance
// data is fetched into the UI. The shared health poll keeps running either
// way, so once `remo web adopt` completes the gate flips to AppShell
// automatically; AppShell's `useDiscovery()` then triggers its usual
// first-mount full discovery refresh, landing the operator on a populated
// dashboard with zero manual refreshes.
//
// "loading" (before the first /ready response) renders AppShell as before, so
// startup behavior for configured deployments is unchanged.

import { useHealth } from "../state/health";
import { AppShell } from "./AppShell";
import { AwaitingAdoption } from "./AwaitingAdoption";

export function AppRoot(): JSX.Element {
  const health = useHealth();
  if (health.status === "unconfigured") {
    return <AwaitingAdoption />;
  }
  return <AppShell />;
}
