// The rebuild confirm's explicit-consequence contract: the copy says what is
// kept and what is lost, the git warning appears exactly when work exists only
// on that host, and the no-cache choice travels with the confirmation.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SessionTarget } from "../api/client";
import { RebuildConfirmDialog } from "./RebuildConfirmDialog";

function target(overrides: Partial<SessionTarget> = {}): SessionTarget {
  return {
    id: "t-1",
    instance_type: "incus",
    instance_name: "box",
    project: "widget",
    zellij_state: "active",
    has_devcontainer: true,
    devcontainer_running: "running",
    discovered_at: "",
    git_tracked: true,
    git_dirty: false,
    git_ahead: 0,
    git_behind: 0,
    ...overrides,
  } as SessionTarget;
}

describe("RebuildConfirmDialog", () => {
  it("spells out the consequences and confirms without cache by default", () => {
    const onConfirm = vi.fn();
    render(
      <RebuildConfirmDialog
        project="widget"
        target={target()}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByText(/workspace folder are kept/i)).toBeInTheDocument();
    expect(screen.getByText(/running\s+session will close/i)).toBeInTheDocument();
    expect(screen.queryByTestId("rebuild-git-warning")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("rebuild-confirm"));
    expect(onConfirm).toHaveBeenCalledWith(false);
  });

  it("warns when the target is git-dirty", () => {
    render(
      <RebuildConfirmDialog
        project="widget"
        target={target({ git_dirty: true })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("rebuild-git-warning").textContent).toContain("uncommitted changes");
  });

  it("warns when the target has unpushed commits", () => {
    render(
      <RebuildConfirmDialog
        project="widget"
        target={target({ git_ahead: 3 })}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByTestId("rebuild-git-warning").textContent).toContain("3 unpushed commits");
  });

  it("passes the no-cache choice through", () => {
    const onConfirm = vi.fn();
    render(
      <RebuildConfirmDialog
        project="widget"
        target={target()}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("rebuild-no-cache"));
    fireEvent.click(screen.getByTestId("rebuild-confirm"));
    expect(onConfirm).toHaveBeenCalledWith(true);
  });
});
