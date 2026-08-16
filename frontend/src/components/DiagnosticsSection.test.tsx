// Settings ▸ Diagnostics: the copy affordance.
//
// What matters here is that the button hands over the WHOLE snapshot (the
// point is a paste into a bug report) with the service version resolved first,
// and that a browser refusing the clipboard says so rather than silently
// reporting success — this console commonly runs over plain http on a LAN,
// where the async clipboard API is unavailable.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getHealth = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, getHealth: () => getHealth() };
});

const writeText = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  getHealth.mockResolvedValue({ status: "alive", version: "4.3.6" });
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  // The legacy execCommand fallback must not silently "succeed" in jsdom.
  document.execCommand = vi.fn().mockReturnValue(false);
});

/** The diagnostics module caches the resolved version for the page's lifetime,
 * so each test needs a fresh module graph to start from "unknown". */
async function mountSection(): Promise<void> {
  vi.resetModules();
  const { DiagnosticsSection } = await import("./DiagnosticsSection");
  render(<DiagnosticsSection />);
}

describe("DiagnosticsSection", () => {
  it("copies a full snapshot and names the service build", async () => {
    await mountSection();

    fireEvent.click(screen.getByTestId("copy-diagnostics"));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const payload = JSON.parse(writeText.mock.calls[0][0] as string);
    expect(payload).toMatchObject({ versions: { service: "4.3.6" } });
    expect(payload).toHaveProperty("generatedAt");
    expect(payload).toHaveProperty("env");
    expect(payload).toHaveProperty("layout");
    expect(payload.panes).toEqual([]);

    expect(screen.getByTestId("copy-diagnostics")).toHaveTextContent("Copied ✓");
    await waitFor(() => expect(screen.getByText(/Service v4\.3\.6/)).toBeInTheDocument());
  });

  it("says so when the browser refuses the clipboard, and points at devtools", async () => {
    writeText.mockRejectedValue(new Error("NotAllowedError"));
    await mountSection();

    fireEvent.click(screen.getByTestId("copy-diagnostics"));

    await waitFor(() =>
      expect(screen.getByTestId("copy-diagnostics")).toHaveTextContent(/refused/i),
    );
    // The escape hatch is always named, refusal or not.
    expect(screen.getByText(/__remo\.diagnostics\(\)/)).toBeInTheDocument();
  });

  it("still copies when the service version cannot be resolved", async () => {
    getHealth.mockRejectedValue(new Error("network"));
    await mountSection();

    fireEvent.click(screen.getByTestId("copy-diagnostics"));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(JSON.parse(writeText.mock.calls[0][0] as string).versions.service).toBeNull();
    expect(screen.getByText(/Service version unavailable/)).toBeInTheDocument();
  });
});
