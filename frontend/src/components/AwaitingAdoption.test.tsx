// Copy-on-mint for the awaiting-adoption page (#159) plus the unmount session
// end (#158).
//
// The code is never displayed, so a mint the operator doesn't copy is an
// invisible dead end. There is no user gesture on page load, so the automatic
// write is usually refused here — that path has to be loud, not quiet.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AwaitingAdoption } from "./AwaitingAdoption";

const mintPairingCode = vi.fn();
const endPairing = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    mintPairingCode: (...args: unknown[]) => mintPairingCode(...args),
    endPairing: () => endPairing(),
  };
});

vi.mock("../state/health", () => ({
  useHealth: () => ({ retry: vi.fn() }),
}));

const CODE = "hunter2-pairing-code";
const writeText = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  mintPairingCode.mockResolvedValue({ code: CODE, expires_in: 900 });
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
  document.execCommand = vi.fn().mockReturnValue(false);
});

async function ready() {
  await waitFor(() => expect(screen.getByTestId("adopt-copy")).toBeEnabled());
}

describe("AwaitingAdoption", () => {
  it("copies the minted code without waiting for a click", async () => {
    render(<AwaitingAdoption />);

    await ready();

    expect(writeText).toHaveBeenCalledWith(CODE);
    expect(screen.getByTestId("adopt-copy")).toHaveTextContent("Copied ✓");
    expect(screen.getByText(/on your clipboard/i)).toBeInTheDocument();
  });

  it("makes the copy button required when the automatic write is refused", async () => {
    // The expected first render in most browsers: page load is not a gesture.
    writeText.mockRejectedValue(new Error("NotAllowedError"));
    render(<AwaitingAdoption />);

    await ready();

    const button = screen.getByTestId("adopt-copy");
    await waitFor(() => expect(button).toHaveTextContent("Copy pairing code (required)"));
    expect(screen.getByText(/before you run the command/i)).toBeInTheDocument();

    writeText.mockResolvedValue(undefined);
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveTextContent("Copied ✓"));
  });

  it("never renders the code into the DOM", async () => {
    const { container } = render(<AwaitingAdoption />);

    await ready();

    expect(container.innerHTML).not.toContain(CODE);
  });

  it("ends the pairing session on unmount", async () => {
    // Unmount is exactly when adoption completes, and since #158 the CLI's
    // verify call no longer ends the session on our behalf.
    const { unmount } = render(<AwaitingAdoption />);
    await ready();

    unmount();

    expect(endPairing).toHaveBeenCalledTimes(1);
  });

  it("prompts to sign in instead of minting when operator auth refuses", async () => {
    const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
    mintPairingCode.mockRejectedValue(
      new ApiError({
        code: "forbidden",
        message: "nope",
        remediation: "Sign in through your access proxy.",
        retryable: false,
      }),
    );
    render(<AwaitingAdoption />);

    await waitFor(() => expect(screen.getByText(/not signed in/i)).toBeInTheDocument());
    expect(writeText).not.toHaveBeenCalled();
  });
});
