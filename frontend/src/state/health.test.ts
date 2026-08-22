// The health store's reaction to a forward-auth challenge.
//
// Field report: returning to the console after the access-proxy session lapsed
// showed "remo couldn't connect". The readiness poll's challenge had surfaced
// as a network error, and this store turned that into the offline overlay —
// over a service that was perfectly healthy and a page that was one re-auth
// navigation away from working.

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getReady = vi.fn();
const getHealth = vi.fn();

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, getReady, getHealth };
});

// `ApiError` identity matters: both the store's `instanceof` check and
// `isAuthChallenge` compare against the class from THEIR module registry, and
// vi.resetModules() makes a fresh one. So the test builds its errors from the
// very same registry the store under test is using, never from importActual.
async function load(): Promise<{
  health: typeof import("./health");
  apiError: (code: string) => Error;
}> {
  vi.resetModules();
  const client = await import("../api/client");
  const health = await import("./health");
  return {
    health,
    apiError: (code: string) =>
      new client.ApiError({ code, message: `test ${code}`, retryable: false, remediation: "" }),
  };
}

beforeEach(() => {
  getReady.mockReset();
  getHealth.mockReset();
  // Most tests don't care about /health; an unreachable one must simply leave
  // hostAdmin at its false default.
  getHealth.mockRejectedValue(new Error("health unavailable"));
});

describe("health store", () => {
  it("reports offline for a genuine transport failure", async () => {
    const { health, apiError } = await load();
    getReady.mockRejectedValue(apiError("network_error"));
    const { result } = renderHook(() => health.useHealth());

    await waitFor(() => expect(result.current.status).toBe("offline"));
  });

  it.each(["auth_challenge", "auth_required"])(
    "does not claim an outage on a %s challenge",
    async (code) => {
      const { health, apiError } = await load();
      getReady.mockResolvedValueOnce({ ready: true, status: "ready", checks: {} });
      const { result } = renderHook(() => health.useHealth());
      await waitFor(() => expect(result.current.status).toBe("healthy"));

      // The proxy session lapses; the next poll is challenged.
      getReady.mockRejectedValue(apiError(code));
      await act(async () => {
        await result.current.retry();
      });

      // Still "healthy": the service never said otherwise, and the client is
      // handling re-auth. The offline overlay must not flash here.
      expect(result.current.status).toBe("healthy");
    },
  );

  it("defaults hostAdmin to false when /health is unreachable or silent", async () => {
    const { health } = await load();
    getReady.mockResolvedValue({ ready: true, status: "ready", checks: {} });
    const { result } = renderHook(() => health.useHealth());

    await waitFor(() => expect(result.current.status).toBe("healthy"));
    expect(result.current.hostAdmin).toBe(false);
  });

  it("defaults hostAdmin to false when the features field is absent (old service)", async () => {
    const { health } = await load();
    getReady.mockResolvedValue({ ready: true, status: "ready", checks: {} });
    getHealth.mockResolvedValue({ status: "alive", version: "0.0.0" });
    const { result } = renderHook(() => health.useHealth());

    await waitFor(() => expect(result.current.status).toBe("healthy"));
    expect(result.current.hostAdmin).toBe(false);
  });

  it("exposes hostAdmin=true when /health advertises features.host_admin", async () => {
    const { health } = await load();
    getReady.mockResolvedValue({ ready: true, status: "ready", checks: {} });
    getHealth.mockResolvedValue({
      status: "alive",
      version: "0.0.0",
      features: { host_admin: true },
    });
    const { result } = renderHook(() => health.useHealth());

    await waitFor(() => expect(result.current.hostAdmin).toBe(true));
  });

  it("still degrades on an unexpected error shape", async () => {
    const { health } = await load();
    getReady.mockRejectedValue(new Error("something else"));
    const { result } = renderHook(() => health.useHealth());

    await waitFor(() => expect(result.current.status).toBe("degraded"));
  });
});
