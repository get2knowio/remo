// Copy-on-mint for the Settings re-sync affordance (#159).
//
// The pairing code is deliberately never displayed, so the clipboard IS the
// only channel. Minting used to rotate the previous code while only an explicit
// Copy click wrote the clipboard, which left a dead code there — invisibly.

import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PairToSync } from "./PairToSync";

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
  // The legacy execCommand fallback must not silently "succeed" in jsdom.
  document.execCommand = vi.fn().mockReturnValue(false);
});

afterEach(() => {
  vi.useRealTimers();
});

async function open() {
  fireEvent.click(screen.getByTestId("pair-to-sync"));
  await waitFor(() => expect(screen.getByTestId("pairsync-copy")).toBeEnabled());
}

describe("PairToSync", () => {
  it("copies the freshly minted code without waiting for a click", async () => {
    render(<PairToSync />);

    await open();

    expect(writeText).toHaveBeenCalledWith(CODE);
    expect(screen.getByTestId("pairsync-copy")).toHaveTextContent("Copied ✓");
    expect(screen.getByText(/on your clipboard now/i)).toBeInTheDocument();
  });

  it("demands an explicit copy when the browser refuses the automatic one", async () => {
    writeText.mockRejectedValue(new Error("NotAllowedError"));
    render(<PairToSync />);

    await open();

    const button = screen.getByTestId("pairsync-copy");
    expect(button).toHaveTextContent("Copy pairing code (required)");
    expect(screen.getByText(/refused the automatic copy/i)).toBeInTheDocument();

    // The explicit click is the recovery path and must still work.
    writeText.mockResolvedValue(undefined);
    fireEvent.click(button);
    await waitFor(() => expect(button).toHaveTextContent("Copied ✓"));
  });

  it("never carries a stale 'Copied ✓' across a re-mint", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    render(<PairToSync />);

    await open();
    expect(screen.getByTestId("pairsync-copy")).toHaveTextContent("Copied ✓");

    // Close and reopen: the second mint ROTATES the first code away, so the
    // success state from the first must not survive into it.
    fireEvent.click(screen.getByTestId("pair-to-sync"));
    let resolveMint: (value: unknown) => void = () => {};
    mintPairingCode.mockReturnValue(new Promise((resolve) => (resolveMint = resolve)));
    fireEvent.click(screen.getByTestId("pair-to-sync"));

    expect(screen.getByTestId("pairsync-copy")).toHaveTextContent("Minting…");
    // Any pending "Copied ✓" reset timer from the first mint is cleared, so it
    // cannot fire mid-flight and desync the label either.
    act(() => vi.advanceTimersByTime(5_000));
    expect(screen.getByTestId("pairsync-copy")).toHaveTextContent("Minting…");

    await act(async () => {
      resolveMint({ code: "second-code", expires_in: 900 });
    });
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith("second-code"));
  });

  it("never renders the code into the DOM", async () => {
    const { container } = render(<PairToSync />);

    await open();

    expect(container.innerHTML).not.toContain(CODE);
  });

  it("ends the pairing session on close and on unmount while open", async () => {
    const { unmount } = render(<PairToSync />);

    await open();
    fireEvent.click(screen.getByText("Done"));
    expect(endPairing).toHaveBeenCalledTimes(1);

    await open();
    unmount();
    expect(endPairing).toHaveBeenCalledTimes(2);
  });
});
