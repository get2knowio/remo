// Host detail page gating (plan §2.4): the feature gate (`features.
// host_admin`) hides every mutating affordance outright; the capability gate
// (the host's own operations[] / a stats 409) replaces the action sections
// with an upgrade nudge; and the stats strip renders only what the host
// actually reports (no temps card without sensors, no swap bar without swap).
//
// Poll mechanics (5s cadence, visibility pause/resume, 409 stop) are the
// hook's own contract and are covered in state/hostStats.test.ts — here the
// hook is mocked so each gating state can be pinned directly.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DiscoveryInstance, HostStats, SessionTarget, TypedError } from "../api/client";

const useDiscovery = vi.fn();
const useHealth = vi.fn();
const useHostStats = vi.fn();
const rebuildProject = vi.fn();

vi.mock("../state/discovery", () => ({ useDiscovery: () => useDiscovery() }));
vi.mock("../state/health", () => ({ useHealth: () => useHealth() }));
vi.mock("../state/hostStats", () => ({ useHostStats: () => useHostStats() }));
// ApiError (and the rest of the client) stays real so error rendering is
// exercised; only the rebuild call is stubbed.
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, rebuildProject: (...a: unknown[]) => rebuildProject(...a) };
});
// The shell panel drags in the real renderer stack; its own behavior is not
// under test here.
vi.mock("./HostShellPanel", () => ({
  HostShellPanel: () => <div data-testid="host-shell-panel" />,
}));

import { ApiError } from "../api/client";
import { HostDetailPage } from "./HostDetailPage";

const ALL_OPS = ["host.stats", "projects.clone", "projects.delete", "projects.rebuild", "jobs.status"];

function instance(overrides: Partial<DiscoveryInstance> = {}): DiscoveryInstance {
  return {
    instance_id: "i-1",
    instance_name: "box",
    instance_type: "incus",
    region: "local",
    status: "ok",
    capability: {
      host_tools_version: "9.9.9",
      protocol_version: 1,
      projects_root: "/home/remo/projects",
      operations: ALL_OPS,
      zellij: true,
      docker: true,
    },
    error: null,
    refreshed_at: null,
    ...overrides,
  } as DiscoveryInstance;
}

function target(project: string, overrides: Partial<SessionTarget> = {}): SessionTarget {
  return {
    id: `t-${project}`,
    instance_type: "incus",
    instance_name: "box",
    project,
    zellij_state: "none",
    has_devcontainer: true,
    devcontainer_running: "stopped",
    discovered_at: "2026-08-20T00:00:00Z",
    git_tracked: true,
    git_dirty: false,
    git_ahead: 0,
    git_behind: 0,
    ...overrides,
  } as SessionTarget;
}

function stats(overrides: Partial<HostStats> = {}): HostStats {
  return {
    uptime_s: 90_000,
    load_1: 0.5,
    load_5: 0.4,
    load_15: 0.3,
    cpu_count: 8,
    cpu_used_pct: 12,
    mem_total: 8 * 1024 ** 3,
    mem_used: 2 * 1024 ** 3,
    mem_available: 6 * 1024 ** 3,
    swap_total: 0,
    swap_used: 0,
    disks: [{ mount: "/", size_bytes: 100e9, used_bytes: 40e9, avail_bytes: 60e9 }],
    temps: [],
    ...overrides,
  } as HostStats;
}

interface Setup {
  instances?: DiscoveryInstance[];
  targets?: SessionTarget[];
  hostAdmin?: boolean;
  registryAdmin?: boolean;
  hookResult?: {
    stats: HostStats | null;
    stale: boolean;
    unsupported: TypedError | null;
  };
}

const refresh = vi.fn();
const onClose = vi.fn();
const onOpenTarget = vi.fn();

function mount({
  instances = [instance()],
  targets = [target("alpha")],
  hostAdmin = true,
  registryAdmin = false,
  hookResult = { stats: stats(), stale: false, unsupported: null },
}: Setup = {}): void {
  useDiscovery.mockReturnValue({
    instances,
    targets,
    refresh,
    isRefreshing: false,
    lastRefreshedAt: null,
  });
  useHealth.mockReturnValue({
    status: "healthy",
    checks: {},
    detail: null,
    hostAdmin,
    registryAdmin,
    registryChange: null,
    retry: vi.fn(),
  });
  useHostStats.mockReturnValue({ ...hookResult, refetch: vi.fn() });
  render(<HostDetailPage instanceId="i-1" onClose={onClose} onOpenTarget={onOpenTarget} />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("HostDetailPage", () => {
  it("renders actions when the gate is on and the host has every op", () => {
    mount();
    expect(screen.getByTestId("new-project-section")).toBeInTheDocument();
    expect(screen.getByTestId("danger-zone")).toBeInTheDocument();
    expect(screen.getByTestId("rebuild-project-t-alpha")).toBeInTheDocument();
    expect(screen.getByTestId("host-shell-toggle")).toBeInTheDocument();
    expect(screen.queryByTestId("capability-nudge")).not.toBeInTheDocument();
  });

  it("replaces action sections with the capability nudge when operations[] predates them", () => {
    const inst = instance();
    inst.capability = { ...inst.capability!, operations: ["host.stats"] };
    mount({ instances: [inst] });

    const nudge = screen.getByTestId("capability-nudge");
    // The nudge names this host's exact upgrade command.
    expect(nudge.textContent).toContain("remo incus upgrade box");
    expect(screen.queryByTestId("new-project-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("danger-zone")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rebuild-project-t-alpha")).not.toBeInTheDocument();
    // The host shell needs no host tools — the button stays.
    expect(screen.getByTestId("host-shell-toggle")).toBeInTheDocument();
  });

  it("names `remo configure` for an added (ssh) host's nudge", () => {
    const inst = instance({ instance_type: "ssh" });
    inst.capability = { ...inst.capability!, operations: [] };
    mount({ instances: [inst], targets: [target("alpha", { instance_type: "ssh" })] });
    expect(screen.getByTestId("capability-nudge").textContent).toContain("remo configure box");
  });

  it("names the SHORT container name in a host-scoped provider's upgrade command", () => {
    // Incus/Proxmox register `host/container` names, but the upgrade verb
    // takes the container name alone — `remo incus upgrade host/container`
    // dead-ends on the CLI.
    const inst = instance({ instance_name: "orion/box" });
    inst.capability = { ...inst.capability!, operations: ["host.stats"] };
    mount({ instances: [inst], targets: [] });
    const nudge = screen.getByTestId("capability-nudge");
    expect(nudge.textContent).toContain("remo incus upgrade box");
    expect(nudge.textContent).not.toContain("upgrade orion/box");
  });

  it("keeps clone/delete when only projects.rebuild is missing (no rebuild button, no nudge)", () => {
    // remo-host deliberately omits projects.rebuild without the reference
    // devcontainer CLI; clone/delete/jobs still work, so the maintenance
    // surface must stay and the nudge must not fire.
    const inst = instance();
    inst.capability = {
      ...inst.capability!,
      operations: ["host.stats", "projects.clone", "projects.delete", "jobs.status"],
    };
    mount({ instances: [inst] });
    expect(screen.getByTestId("new-project-section")).toBeInTheDocument();
    expect(screen.getByTestId("danger-zone")).toBeInTheDocument();
    expect(screen.queryByTestId("rebuild-project-t-alpha")).not.toBeInTheDocument();
    expect(screen.queryByTestId("capability-nudge")).not.toBeInTheDocument();
  });

  it("renders a rebuild failure near the projects table with its remediation", async () => {
    rebuildProject.mockRejectedValue(
      new ApiError({
        code: "operation_failed",
        message: "rebuild failed on host",
        retryable: false,
        remediation: "check devcontainer.json",
      }),
    );
    mount();
    fireEvent.click(screen.getByTestId("rebuild-project-t-alpha"));
    fireEvent.click(screen.getByTestId("rebuild-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("rebuild-error").textContent).toBe(
        "rebuild failed on host — check devcontainer.json",
      ),
    );
    // Opening the dialog again clears the stale error.
    fireEvent.click(screen.getByTestId("rebuild-project-t-alpha"));
    expect(screen.queryByTestId("rebuild-error")).not.toBeInTheDocument();
  });

  it("hides ALL mutating affordances when features.host_admin is false", () => {
    mount({ hostAdmin: false });
    expect(screen.queryByTestId("host-shell-toggle")).not.toBeInTheDocument();
    expect(screen.queryByTestId("new-project-section")).not.toBeInTheDocument();
    expect(screen.queryByTestId("danger-zone")).not.toBeInTheDocument();
    expect(screen.queryByTestId("rebuild-project-t-alpha")).not.toBeInTheDocument();
    // No nudge either: with the gate off there is nothing to upgrade toward.
    expect(screen.queryByTestId("capability-nudge")).not.toBeInTheDocument();
    // Read-only content stays.
    expect(screen.getByTestId("stats-strip")).toBeInTheDocument();
    expect(screen.getByTestId("projects-table")).toBeInTheDocument();
  });

  it("hides the temps card when the host reports no sensors", () => {
    mount();
    expect(screen.queryByTestId("stat-temps")).not.toBeInTheDocument();
  });

  it("shows the temps card when sensors exist", () => {
    mount({
      hookResult: {
        stats: stats({ temps: [{ name: "coretemp", label: "Core 0", celsius: 51 }] }),
        stale: false,
        unsupported: null,
      },
    });
    expect(screen.getByTestId("stat-temps").textContent).toContain("51°C");
  });

  it("shows swap only when swap_total > 0", () => {
    mount();
    expect(screen.queryByTestId("stat-swap")).not.toBeInTheDocument();
  });

  it("keeps the last snapshot with a stale badge when the poll fails", () => {
    mount({ hookResult: { stats: stats(), stale: true, unsupported: null } });
    expect(screen.getByTestId("stats-stale-badge")).toBeInTheDocument();
    expect(screen.getByTestId("stats-strip")).toBeInTheDocument();
  });

  it("surfaces the 409 envelope's remediation in the stats nudge", () => {
    mount({
      hookResult: {
        stats: null,
        stale: false,
        unsupported: {
          code: "unsupported_host_tools",
          message: "host tools predate stats",
          retryable: false,
          remediation: "Run: remo incus upgrade box",
        },
      },
    });
    expect(screen.getByTestId("stats-nudge").textContent).toContain("Run: remo incus upgrade box");
    expect(screen.queryByTestId("stats-strip")).not.toBeInTheDocument();
  });

  it("Open hands the target to the workspace", () => {
    mount();
    fireEvent.click(screen.getByTestId("open-project-t-alpha"));
    expect(onOpenTarget).toHaveBeenCalledWith(expect.objectContaining({ id: "t-alpha" }));
  });

  it("per-host Refresh triggers a targeted discovery refresh", () => {
    mount();
    fireEvent.click(screen.getByTestId("host-detail-refresh"));
    expect(refresh).toHaveBeenCalledWith("i-1");
  });
});

describe("HostDetailPage registry admin (023)", () => {
  const sshInstance = (overrides: Partial<DiscoveryInstance> = {}): DiscoveryInstance =>
    instance({ instance_type: "ssh", region: "", ...overrides });

  it("hides remove/configure affordances when registry_admin is off", () => {
    mount({ instances: [sshInstance()], registryAdmin: false });
    expect(screen.queryByTestId("remove-host-zone")).not.toBeInTheDocument();
    expect(screen.queryByTestId("configure-now")).not.toBeInTheDocument();
  });

  it("hides them for provider-managed hosts even when the flag is on", () => {
    mount({ registryAdmin: true }); // default instance is incus
    expect(screen.queryByTestId("remove-host-zone")).not.toBeInTheDocument();
  });

  it("renders the remove-host zone for an ssh host and gates it on typing the name", () => {
    mount({ instances: [sshInstance()], registryAdmin: true });
    fireEvent.click(screen.getByTestId("remove-host"));
    const confirmButton = screen.getByTestId("remove-host-confirm");
    expect(confirmButton).toBeDisabled();
    fireEvent.input(screen.getByTestId("remove-host-input"), { target: { value: "box" } });
    expect(confirmButton).not.toBeDisabled();
  });

  it("offers Configure now in the capability nudge for an ssh host", () => {
    // Tools predate maintenance: no capability at all, host ok.
    mount({
      instances: [sshInstance({ capability: null })],
      registryAdmin: true,
    });
    expect(screen.getByTestId("capability-nudge")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("configure-now"));
    expect(screen.getByTestId("configure-dialog")).toBeInTheDocument();
    // The consequence copy names the provisioning pass, not a toggle.
    expect(screen.getByTestId("configure-dialog").textContent).toContain("passwordless sudo");
  });

  it("offers Configure now for a freshly added host (status no_remo_host)", () => {
    // The exact host the flow exists for reports `no_remo_host`, not "ok" —
    // and a registry-admin-only deployment has host_admin off. Both must
    // still reach configure, with the server's remediation preferred.
    mount({
      instances: [
        sshInstance({
          status: "no_remo_host",
          capability: null,
          error: {
            code: "no_remo_host",
            message: "remo-host is not installed",
            retryable: false,
            remediation: "Configure the host: remo configure box",
          },
        }),
      ],
      hostAdmin: false,
      registryAdmin: true,
    });
    const nudge = screen.getByTestId("capability-nudge");
    expect(nudge.textContent).toContain("Configure the host: remo configure box");
    fireEvent.click(screen.getByTestId("configure-now"));
    expect(screen.getByTestId("configure-dialog")).toBeInTheDocument();
  });

  it("shows no nudge for a no_remo_host host when registry_admin is off", () => {
    mount({
      instances: [sshInstance({ status: "no_remo_host", capability: null })],
      hostAdmin: false,
      registryAdmin: false,
    });
    expect(screen.queryByTestId("capability-nudge")).not.toBeInTheDocument();
  });
});
