// The delete confirm's type-the-name arming, and the destroyed-changes line
// that appears exactly when the project has work only that host holds.

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DeleteProjectDialog } from "./DeleteProjectDialog";

function mount(overrides: Partial<Parameters<typeof DeleteProjectDialog>[0]> = {}): {
  onConfirm: ReturnType<typeof vi.fn>;
} {
  const onConfirm = vi.fn();
  render(
    <DeleteProjectDialog
      project="widget"
      dirty={false}
      busy={false}
      error={null}
      onConfirm={onConfirm}
      onCancel={vi.fn()}
      {...overrides}
    />,
  );
  return { onConfirm };
}

describe("DeleteProjectDialog", () => {
  it("keeps the delete button disarmed until the exact name is typed", () => {
    const { onConfirm } = mount();
    const confirm = screen.getByTestId("delete-confirm");
    expect(confirm).toBeDisabled();

    fireEvent.input(screen.getByTestId("delete-name-input"), { target: { value: "widge" } });
    expect(confirm).toBeDisabled();

    fireEvent.input(screen.getByTestId("delete-name-input"), { target: { value: "widget" } });
    expect(confirm).toBeEnabled();

    fireEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("says uncommitted/unpushed changes will be destroyed when dirty", () => {
    mount({ dirty: true });
    expect(screen.getByTestId("delete-dirty-warning").textContent).toMatch(
      /uncommitted or unpushed changes.*destroyed/i,
    );
  });

  it("shows no dirty warning for a clean project", () => {
    mount();
    expect(screen.queryByTestId("delete-dirty-warning")).not.toBeInTheDocument();
  });

  it("stays disarmed while the delete is in flight", () => {
    mount({ busy: true });
    fireEvent.input(screen.getByTestId("delete-name-input"), { target: { value: "widget" } });
    expect(screen.getByTestId("delete-confirm")).toBeDisabled();
    expect(screen.getByTestId("delete-confirm").textContent).toContain("Deleting…");
  });
});
