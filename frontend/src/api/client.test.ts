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

describe("host stats + maintenance endpoints", () => {
  it("getHostStats GETs the stats route and returns the snapshot", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ cpu_count: 4, temps: [], disks: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    const stats = await client.getHostStats("inst-1");
    expect(stats.cpu_count).toBe(4);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/hosts/inst-1/stats",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("surfaces the 409 unsupported_host_tools envelope with its remediation", async () => {
    stubLocation();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "unsupported_host_tools",
              message: "host tools predate stats",
              retryable: false,
              remediation: "Run: remo configure box",
            },
          },
          409,
        ),
      ),
    );
    const client = await load();

    await expect(client.getHostStats("inst-1")).rejects.toMatchObject({
      code: "unsupported_host_tools",
      remediation: "Run: remo configure box",
    });
  });

  it("cloneProject POSTs {repo} and omits an absent name", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ job_id: "j1", kind: "clone", project: "repo" }, 202));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    const accepted = await client.cloneProject("inst-1", "owner/repo");
    expect(accepted.job_id).toBe("j1");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/hosts/inst-1/projects");
    expect(JSON.parse(init.body as string)).toEqual({ repo: "owner/repo" });
  });

  it("cloneProject includes name when given", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ job_id: "j1", kind: "clone", project: "other" }, 202));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.cloneProject("inst-1", "owner/repo", "other");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ repo: "owner/repo", name: "other" });
  });

  it("deleteProject DELETEs the encoded project path", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ deleted: true, project: "my app" }));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.deleteProject("inst-1", "my app");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/hosts/inst-1/projects/my%20app",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("rebuildProject POSTs {no_cache}", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ job_id: "j2", kind: "rebuild", project: "app" }, 202));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.rebuildProject("inst-1", "app", true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/hosts/inst-1/projects/app/rebuild");
    expect(JSON.parse(init.body as string)).toEqual({ no_cache: true });
  });

  it("getJobStatus GETs the job route", async () => {
    stubLocation();
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ state: "running", log_tail: "" }));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    const status = await client.getJobStatus("inst-1", "job-9");
    expect(status.state).toBe("running");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/hosts/inst-1/jobs/job-9",
      expect.objectContaining({ method: "GET" }),
    );
  });
});

describe("createTerminal origin union", () => {
  const created = { terminal_id: "t1", ws_token: "tok", ws_subprotocol: "remo-terminal.v1" };

  it("a bare string is the session shorthand (body shape unchanged)", async () => {
    stubLocation();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(created));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.createTerminal("target-1", 80, 24);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({
      session_target_id: "target-1",
      cols: 80,
      rows: 24,
    });
  });

  it("a host_shell origin sends instance_id and NO session_target_id", async () => {
    stubLocation();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(created));
    vi.stubGlobal("fetch", fetchMock);
    const client = await load();

    await client.createTerminal({ kind: "host_shell", instanceId: "inst-1" }, 120, 40);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toEqual({ instance_id: "inst-1", cols: 120, rows: 40 });
  });
});
