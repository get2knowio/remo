// Forward-auth (SSO proxy) challenge handling.
//
// Regression coverage for the field report: after leaving the console and
// coming back, an expired access-proxy session produced
//   - "Fetch API cannot load https://auth…/application/authorize/…" (a plain
//     fetch following the IdP redirect into the CSP wall), and
//   - a "remo couldn't connect" overlay (that CSP failure read as network_error),
//   - plus "the access proxy did not restore a session" from a SECOND
//     concurrent request racing the first one's re-auth,
// and the page only recovered on a manual browser refresh.

import { beforeEach, describe, expect, it, vi } from "vitest";

const REAUTH_KEY = "remo:last-reauth";

/** jsdom won't let `location.reload` be spied on directly ("Cannot redefine
 * property"), but the whole `location` object can be replaced. */
function stubLocation(): { reload: ReturnType<typeof vi.fn> } {
  const reload = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { href: "http://console.example/", assign: vi.fn(), reload },
  });
  return { reload };
}

/** A proxy 302 to a cross-origin IdP, as `redirect: "manual"` surfaces it. */
function opaqueRedirect(): Response {
  return { type: "opaqueredirect", status: 0, ok: false } as unknown as Response;
}

function jsonResponse(body: unknown, status = 200): Response {
  return {
    type: "basic",
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as unknown as Response;
}

async function load(): Promise<typeof import("./client")> {
  vi.resetModules();
  return import("./client");
}

beforeEach(() => {
  sessionStorage.clear();
  vi.restoreAllMocks();
});

describe("forward-auth challenge", () => {
  it("reloads the document on an opaque redirect instead of reporting a network error", async () => {
    const { reload } = stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    await expect(client.getHosts()).rejects.toMatchObject({ code: "auth_challenge" });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("treats a bare 401 as a proxy challenge (the service never issues one)", async () => {
    const { reload } = stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({}, 401)));
    const client = await load();

    await expect(client.getHosts()).rejects.toMatchObject({ code: "auth_challenge" });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  // The reported "did not restore a session" error: returning to a tab fires
  // several requests at once, and all of them get challenged.
  it("navigates once for concurrent challenges, and never cries misconfiguration", async () => {
    const { reload } = stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    const results = await Promise.allSettled([
      client.getHosts(),
      client.getSessions(),
      client.getReady(),
    ]);

    expect(reload).toHaveBeenCalledTimes(1);
    for (const r of results) {
      expect(r.status).toBe("rejected");
      const code = (r as PromiseRejectedResult).reason.code;
      // Every one of them is the benign "a reload is coming" error...
      expect(code).toBe("auth_challenge");
      // ...and specifically NOT the alarming one, which claims re-auth already
      // ran and failed.
      expect(code).not.toBe("auth_required");
    }
  });

  // The cooldown's real job: we already reloaded once and got challenged again.
  it("stops looping when a completed reload is challenged again", async () => {
    const { reload } = stubLocation();
    sessionStorage.setItem(REAUTH_KEY, String(Date.now()));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    await expect(client.getHosts()).rejects.toMatchObject({
      code: "auth_required",
      message: "Sign-in is required, but the access proxy did not restore a session.",
    });
    expect(reload).not.toHaveBeenCalled();
  });

  // A permanently broken proxy must not reload the page every 10 seconds.
  it("gives up for good once re-auth has failed, rather than looping on a timer", async () => {
    const { reload } = stubLocation();
    sessionStorage.setItem(REAUTH_KEY, String(Date.now()));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    await expect(client.getHosts()).rejects.toMatchObject({ code: "auth_required" });

    // Even after the cooldown window passes, this document stays put.
    sessionStorage.setItem(REAUTH_KEY, String(Date.now() - 11_000));
    await expect(client.getHosts()).rejects.toMatchObject({ code: "auth_required" });
    expect(reload).not.toHaveBeenCalled();
  });

  it("re-auths again once the cooldown has elapsed (no prior failure)", async () => {
    const { reload } = stubLocation();
    sessionStorage.setItem(REAUTH_KEY, String(Date.now() - 11_000));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    await expect(client.getHosts()).rejects.toMatchObject({ code: "auth_challenge" });
    expect(reload).toHaveBeenCalledTimes(1);
  });
});

describe("getReady", () => {
  // The specific defect: /ready used a bare fetch, so the browser followed the
  // proxy's cross-origin 302 and CSP killed it — which the health store then
  // showed as "the Remo web service is unreachable".
  it("sends redirect:manual so the IdP redirect is never followed into the CSP", async () => {
    stubLocation();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ status: "ready", checks: {} }));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.getReady();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/ready", expect.objectContaining({
      redirect: "manual",
    }));
  });

  it("re-auths on a challenge rather than reporting the service unreachable", async () => {
    const { reload } = stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(opaqueRedirect()));
    const client = await load();

    await expect(client.getReady()).rejects.toMatchObject({ code: "auth_challenge" });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("still reports a genuine transport failure as network_error", async () => {
    stubLocation();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const client = await load();

    await expect(client.getReady()).rejects.toMatchObject({ code: "network_error" });
  });
});

describe("isAuthChallenge", () => {
  it("matches both challenge codes and nothing else", async () => {
    stubLocation();
    const client = await load();
    const err = (code: string) =>
      new client.ApiError({ code, message: "", retryable: false, remediation: "" });

    expect(client.isAuthChallenge(err("auth_challenge"))).toBe(true);
    expect(client.isAuthChallenge(err("auth_required"))).toBe(true);
    expect(client.isAuthChallenge(err("network_error"))).toBe(false);
    expect(client.isAuthChallenge(new Error("boom"))).toBe(false);
    expect(client.isAuthChallenge(undefined)).toBe(false);
  });
});
