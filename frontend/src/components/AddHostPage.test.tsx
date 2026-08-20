// Add-host wizard (023): step transitions, the mismatch hard stop, the
// unreachable retry, and the auth_failed verify loop. The client fns are
// mocked; ApiError stays real so error rendering is exercised.

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const addHost = vi.fn();
const scanHostKey = vi.fn();
const trustHostKey = vi.fn();
const verifyHost = vi.fn();
const startConfigure = vi.fn();
const getRegistryJobStatus = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    addHost: (...a: unknown[]) => addHost(...a),
    scanHostKey: (...a: unknown[]) => scanHostKey(...a),
    trustHostKey: (...a: unknown[]) => trustHostKey(...a),
    verifyHost: (...a: unknown[]) => verifyHost(...a),
    startConfigure: (...a: unknown[]) => startConfigure(...a),
    getRegistryJobStatus: (...a: unknown[]) => getRegistryJobStatus(...a),
  };
});

const refresh = vi.fn();
vi.mock("../state/discovery", () => ({
  useDiscovery: () => ({ instances: [], targets: [], refresh, isRefreshing: false }),
}));

import { AddHostPage } from "./AddHostPage";

const ADDED = {
  instance_id: "iid-1",
  name: "mbp",
  host: "10.0.0.9",
  user: "paul",
  port: 22,
  public_key: "ssh-ed25519 AAAA remo-web@dep1",
  authorize_command: "set -e; umask 077; … authorized_keys",
};

const KEY_LINE = "10.0.0.9 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKey";

function scanResult(status: string, overrides: Record<string, unknown> = {}) {
  return {
    status,
    detail: `detail-${status}`,
    fingerprints: ["256 SHA256:abc box (ED25519)"],
    lines: [KEY_LINE],
    ...overrides,
  };
}

const onClose = vi.fn();

async function registerThrough(): Promise<void> {
  render(<AddHostPage onClose={onClose} />);
  fireEvent.input(screen.getByTestId("add-host-name"), { target: { value: "mbp" } });
  fireEvent.input(screen.getByTestId("add-host-target"), { target: { value: "paul@10.0.0.9" } });
  fireEvent.click(screen.getByTestId("add-host-submit"));
  await waitFor(() => expect(addHost).toHaveBeenCalled());
}

beforeEach(() => {
  vi.clearAllMocks();
  addHost.mockResolvedValue(ADDED);
});

describe("AddHostPage", () => {
  it("register → trust: shows the authorize one-liner and the scanned fingerprints", async () => {
    scanHostKey.mockResolvedValue(scanResult("no_trust"));
    await registerThrough();

    expect(addHost).toHaveBeenCalledWith({
      name: "mbp",
      target: "paul@10.0.0.9",
      user: null,
      port: null,
    });
    await waitFor(() => expect(screen.getByTestId("scan-fingerprints")).toBeInTheDocument());
    expect(screen.getByTestId("authorize-command").textContent).toContain("authorized_keys");
    expect(screen.getByTestId("add-host-step").textContent).toContain("step 2");
    // The explicit-consequence copy is present before the trust button.
    expect(screen.getByTestId("scan-fingerprints").textContent).toContain("whoever holds these keys");
  });

  it("confirming trust records exactly the scanned lines, then verify → done", async () => {
    scanHostKey.mockResolvedValue(scanResult("no_trust"));
    trustHostKey.mockResolvedValue({ trusted: true });
    verifyHost.mockResolvedValue({ status: "ok", detail: "service key accepted" });
    await registerThrough();

    await waitFor(() => screen.getByTestId("trust-confirm"));
    fireEvent.click(screen.getByTestId("trust-confirm"));
    await waitFor(() => expect(trustHostKey).toHaveBeenCalledWith("iid-1", [KEY_LINE]));

    fireEvent.click(screen.getByTestId("trust-continue"));
    expect(screen.getByTestId("add-host-step").textContent).toContain("step 3");
    fireEvent.click(screen.getByTestId("verify-run"));
    await waitFor(() => expect(screen.getByTestId("add-host-done")).toBeInTheDocument());
    expect(refresh).toHaveBeenCalledWith("iid-1");
    expect(screen.getByTestId("configure-now")).toBeInTheDocument();
  });

  it("a key mismatch is a hard stop — no trust affordance at all", async () => {
    scanHostKey.mockResolvedValue(scanResult("mismatch"));
    await registerThrough();

    await waitFor(() => expect(screen.getByTestId("scan-mismatch")).toBeInTheDocument());
    expect(screen.getByTestId("scan-mismatch").textContent).toContain("machine-in-the-middle");
    expect(screen.queryByTestId("trust-confirm")).not.toBeInTheDocument();
    expect(screen.queryByTestId("trust-continue")).not.toBeInTheDocument();
  });

  it("an unreachable scan offers retry", async () => {
    scanHostKey.mockResolvedValueOnce(scanResult("unreachable"));
    scanHostKey.mockResolvedValue(scanResult("no_trust"));
    await registerThrough();

    await waitFor(() => screen.getByTestId("scan-retry"));
    fireEvent.click(screen.getByTestId("scan-retry"));
    await waitFor(() => expect(screen.getByTestId("scan-fingerprints")).toBeInTheDocument());
    expect(scanHostKey).toHaveBeenCalledTimes(2);
  });

  it("auth_failed keeps the verify step alive with guidance", async () => {
    scanHostKey.mockResolvedValue(scanResult("trusted"));
    verifyHost.mockResolvedValueOnce({
      status: "auth_failed",
      detail: "run the authorize command, then retry",
    });
    verifyHost.mockResolvedValue({ status: "ok", detail: "service key accepted" });
    await registerThrough();

    // Already-trusted keys skip the confirmation.
    await waitFor(() => screen.getByTestId("trust-continue"));
    fireEvent.click(screen.getByTestId("trust-continue"));

    fireEvent.click(screen.getByTestId("verify-run"));
    await waitFor(() => expect(screen.getByTestId("verify-failure")).toBeInTheDocument());
    expect(screen.getByTestId("verify-failure").textContent).toContain("Not authorized yet");
    expect(screen.getByTestId("add-host-step").textContent).toContain("step 3");

    fireEvent.click(screen.getByTestId("verify-run"));
    await waitFor(() => expect(screen.getByTestId("add-host-done")).toBeInTheDocument());
  });

  it("user and port ride into the request when given", async () => {
    scanHostKey.mockResolvedValue(scanResult("no_trust"));
    render(<AddHostPage onClose={onClose} />);
    fireEvent.input(screen.getByTestId("add-host-name"), { target: { value: "mbp" } });
    fireEvent.input(screen.getByTestId("add-host-target"), { target: { value: "10.0.0.9" } });
    fireEvent.input(screen.getByTestId("add-host-user"), { target: { value: "paul" } });
    fireEvent.input(screen.getByTestId("add-host-port"), { target: { value: "2222" } });
    fireEvent.click(screen.getByTestId("add-host-submit"));
    await waitFor(() =>
      expect(addHost).toHaveBeenCalledWith({
        name: "mbp",
        target: "10.0.0.9",
        user: "paul",
        port: 2222,
      }),
    );
  });
});
