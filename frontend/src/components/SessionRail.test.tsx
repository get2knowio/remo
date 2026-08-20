// The rail header's click-target contract (Part 1 + Part 2): the caret and
// the header background toggle collapse; the host NAME is a separate control
// that opens the host detail page — and must NOT also collapse the group.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { DiscoveryInstance, SessionTarget } from "../api/client";
import type { UseWorkspaceResult } from "../state/workspace";
import { buildRailModel, type RailFilters } from "./railModel";
import { SessionRail } from "./SessionRail";

const NO_FILTERS: RailFilters = { search: "", providerFilter: null, sessionOnly: false };

function instance(overrides: Partial<DiscoveryInstance> = {}): DiscoveryInstance {
  return {
    instance_id: "inst-1",
    instance_type: "incus",
    instance_name: "lab/dev1",
    status: "ok",
    region: "",
    capability: null,
    error: null,
    refreshed_at: "2026-08-02T00:00:00Z",
    ...overrides,
  } as DiscoveryInstance;
}

function target(overrides: Partial<SessionTarget> = {}): SessionTarget {
  return {
    id: "t-1",
    instance_type: "incus",
    instance_name: "lab/dev1",
    project: "remo",
    zellij_state: "absent",
    devcontainer_running: "no",
    git_dirty: false,
    git_ahead: 0,
    git_behind: 0,
    ...overrides,
  } as SessionTarget;
}

const workspace = {
  attached: [],
  visible: [],
  focusedId: null,
  selectOnly: vi.fn(),
  addSession: vi.fn(),
  openMany: vi.fn(),
  closeTerm: vi.fn(),
} as unknown as UseWorkspaceResult;

function mountRail(
  options: { registryAdmin?: boolean; noRegistry?: boolean; onAddHost?: () => void } = {},
): {
  onOpenHostDetail: ReturnType<typeof vi.fn>;
  onToggleHostCollapsed: ReturnType<typeof vi.fn>;
} {
  const onOpenHostDetail = vi.fn();
  const onToggleHostCollapsed = vi.fn();
  const noRegistry = options.noRegistry ?? false;
  const model = buildRailModel(
    noRegistry ? [] : [instance()],
    noRegistry ? [] : [target()],
    NO_FILTERS,
    new Set(),
  );
  render(
    <SessionRail
      model={model}
      filters={NO_FILTERS}
      providers={["incus"]}
      isLoading={false}
      noRegistry={noRegistry}
      noCredentials={false}
      workspace={workspace}
      onSearch={vi.fn()}
      onToggleProvider={vi.fn()}
      onToggleSessionOnly={vi.fn()}
      onOpenAllAvailable={vi.fn()}
      onToggleHostCollapsed={onToggleHostCollapsed}
      onToggleFavorite={vi.fn()}
      onOpenHostDetail={onOpenHostDetail}
      registryAdmin={options.registryAdmin ?? false}
      onAddHost={options.onAddHost ?? vi.fn()}
    />,
  );
  return { onOpenHostDetail, onToggleHostCollapsed };
}

describe("SessionRail host-name click", () => {
  it("fires onOpenHostDetail and does NOT toggle collapse", () => {
    const { onOpenHostDetail, onToggleHostCollapsed } = mountRail();

    fireEvent.click(screen.getByTestId("host-name-inst-1"));

    expect(onOpenHostDetail).toHaveBeenCalledWith("inst-1");
    expect(onToggleHostCollapsed).not.toHaveBeenCalled();
  });

  it("keeps the caret as the collapse toggle (Part 1 contract intact)", () => {
    const { onOpenHostDetail, onToggleHostCollapsed } = mountRail();

    fireEvent.click(screen.getByTestId("collapse-toggle-inst-1"));

    expect(onToggleHostCollapsed).toHaveBeenCalledWith("inst-1");
    expect(onOpenHostDetail).not.toHaveBeenCalled();
  });

  it("still collapses via the header background", () => {
    const { onToggleHostCollapsed } = mountRail();
    const name = screen.getByTestId("host-name-inst-1");
    // The header is the name button's parent .rail-inst-head.
    fireEvent.click(name.closest(".rail-inst-head")!);
    expect(onToggleHostCollapsed).toHaveBeenCalledWith("inst-1");
  });
});

describe("registry-admin affordances (023)", () => {
  it("hides the add-host button when the flag is off", () => {
    mountRail({ registryAdmin: false });
    expect(screen.queryByTestId("rail-add-host")).not.toBeInTheDocument();
  });

  it("shows the header add-host button when the flag is on", () => {
    const onAddHost = vi.fn();
    mountRail({ registryAdmin: true, onAddHost });
    fireEvent.click(screen.getByTestId("rail-add-host"));
    expect(onAddHost).toHaveBeenCalledTimes(1);
  });

  it("empty registry becomes an add-host CTA when the flag is on", () => {
    const onAddHost = vi.fn();
    mountRail({ registryAdmin: true, noRegistry: true, onAddHost });
    fireEvent.click(screen.getByTestId("empty-add-host-button"));
    expect(onAddHost).toHaveBeenCalledTimes(1);
  });

  it("empty registry keeps the CLI copy when the flag is off", () => {
    mountRail({ registryAdmin: false, noRegistry: true });
    expect(screen.queryByTestId("empty-add-host")).not.toBeInTheDocument();
    expect(screen.getByText("Empty registry")).toBeInTheDocument();
  });
});
