import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.mock is hoisted above imports, so everything its factory touches must be
// created in vi.hoisted (also hoisted) — including the WebSocket double and the
// mocked network fns, which the tests below also drive.
const mocks = vi.hoisted(() => {
  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;

    readyState = 0;
    bufferedAmount = 0;
    binaryType = "blob";
    onopen: ((e: Event) => void) | null = null;
    onmessage: ((e: MessageEvent) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    onclose: ((e: CloseEvent) => void) | null = null;
    readonly sent: unknown[] = [];

    send(data: unknown): void {
      this.sent.push(data);
    }
    close(code = 1000): void {
      this.readyState = 3;
      this.onclose?.({ code } as CloseEvent);
    }

    // Test-only drivers:
    ready(): void {
      this.readyState = 1;
      this.onopen?.(new Event("open"));
      this.onmessage?.({ data: JSON.stringify({ v: 1, type: "ready" }) } as MessageEvent);
    }
    drop(code = 1006, reason = ""): void {
      this.readyState = 3;
      this.onclose?.({ code, reason } as CloseEvent);
    }
  }

  const sockets: InstanceType<typeof FakeWebSocket>[] = [];
  const state = { seq: 0 };
  const createTerminal = vi.fn(async () => ({ terminal_id: `t${++state.seq}`, ws_token: "tok" }));
  const closeTerminal = vi.fn(async () => {});
  const openTerminalSocket = vi.fn(() => {
    const s = new FakeWebSocket();
    sockets.push(s);
    return s as unknown as WebSocket;
  });

  return { FakeWebSocket, sockets, state, createTerminal, closeTerminal, openTerminalSocket };
});

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {
    code = "unknown";
    retryable = true;
    remediation = "";
  },
  createTerminal: mocks.createTerminal,
  closeTerminal: mocks.closeTerminal,
  openTerminalSocket: mocks.openTerminalSocket,
}));

import { TerminalConnection } from "./TerminalConnection";

let conn: TerminalConnection | null = null;

beforeEach(() => {
  vi.useFakeTimers();
  mocks.state.seq = 0;
  mocks.sockets.length = 0;
  mocks.createTerminal.mockClear();
  mocks.openTerminalSocket.mockClear();
  (globalThis as unknown as { WebSocket: unknown }).WebSocket =
    mocks.FakeWebSocket as unknown as typeof WebSocket;
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
});

afterEach(async () => {
  await conn?.close(); // detaches wake listeners so tests don't cross-talk
  conn = null;
  vi.useRealTimers();
});

const last = () => mocks.sockets[mocks.sockets.length - 1];

describe("TerminalConnection", () => {
  it("reaches 'ready' after the server's ready control frame", async () => {
    const states: string[] = [];
    conn = new TerminalConnection("s1", 80, 24, { onStateChange: (s) => states.push(s) });
    await conn.connect();
    last().ready();
    expect(conn.currentState).toBe("ready");
    expect(states).toEqual(["connecting", "ready"]);
  });

  it("auto-reconnects after an unexpected close (fresh terminal each time)", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    expect(mocks.createTerminal).toHaveBeenCalledTimes(1);

    last().drop(1006);
    expect(conn.currentState).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(500); // first backoff
    expect(mocks.createTerminal).toHaveBeenCalledTimes(2); // a brand-new terminal_id
  });

  it("strands after the auto-retry budget, then recovers on wake (visibilitychange)", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();

    // Exhaust the 3-attempt budget: each reattach fails immediately.
    for (const backoff of [500, 1500, 3500]) {
      last().drop(1006);
      await vi.advanceTimersByTimeAsync(backoff);
    }
    last().drop(1006); // 4th failure — budget exhausted
    expect(conn.currentState).toBe("disconnected");
    expect(conn.needsManualReconnect).toBe(true);

    const attemptsBefore = mocks.createTerminal.mock.calls.length;
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);

    expect(mocks.createTerminal.mock.calls.length).toBe(attemptsBefore + 1);
    expect(conn.currentState).toBe("reconnecting");
    expect(conn.needsManualReconnect).toBe(false);
  });

  it("wake pings a still-open socket instead of reconnecting", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    const socket = last();
    const attemptsBefore = mocks.createTerminal.mock.calls.length;

    window.dispatchEvent(new Event("focus"));
    await vi.advanceTimersByTimeAsync(0);

    // No new terminal; a ping control frame was sent on the live socket.
    expect(mocks.createTerminal.mock.calls.length).toBe(attemptsBefore);
    expect(socket.sent.some((f) => typeof f === "string" && f.includes('"ping"'))).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Resize re-assertion.
//
// A resize reaches the remote as exactly ONE SIGWINCH, and the chain below the
// service PTY (ssh -> remote PTY -> Zellij -> the `docker exec` TTY under
// `devcontainer exec`) has to be listening at that instant. Miss it and nothing
// regenerates it: TerminalCard sends a given grid only once, so the remote stays
// pinned at the stale size until the operator forces a different one by hand.
// These pin the bounded re-assert that makes such a loss self-heal.
// ---------------------------------------------------------------------------

/** Every `resize` control frame sent on *socket*, oldest first. */
const resizeFrames = (
  socket: { sent: unknown[] },
): Array<{ cols: number; rows: number }> =>
  socket.sent
    .filter((f): f is string => typeof f === "string")
    .map((f) => JSON.parse(f) as { type: string; cols: number; rows: number })
    .filter((f) => f.type === "resize")
    .map(({ cols, rows }) => ({ cols, rows }));

describe("TerminalConnection resize re-assertion", () => {
  it("re-asserts the same dims after a resize, then stops", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    const socket = last();
    const before = resizeFrames(socket).length; // the `ready` handler's re-send

    conn.sendResize(100, 40);
    expect(resizeFrames(socket).length).toBe(before + 1);

    // Three bounded re-asserts, each carrying the SAME dims: an unchanged size
    // still makes the service signal SIGWINCH, which is the whole repair.
    await vi.advanceTimersByTimeAsync(400);
    await vi.advanceTimersByTimeAsync(800); // 1200 total
    await vi.advanceTimersByTimeAsync(1800); // 3000 total
    const afterBurst = resizeFrames(socket);
    expect(afterBurst.length).toBe(before + 4);
    expect(afterBurst.slice(-4)).toEqual([
      { cols: 100, rows: 40 },
      { cols: 100, rows: 40 },
      { cols: 100, rows: 40 },
      { cols: 100, rows: 40 },
    ]);

    // Bounded: no steady-state churn once the burst is spent. Every re-assert
    // costs a remote redraw, so an unbounded loop would be a real cost.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(resizeFrames(socket).length).toBe(before + 4);
  });

  it("a newer resize replaces the pending burst instead of stacking", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    const socket = last();

    // A window drag: many resizes in quick succession. Only the final size
    // deserves a burst — stacking one per intermediate size would fire dozens
    // of SIGWINCHes at the remote for sizes it should never see again.
    conn.sendResize(100, 40);
    await vi.advanceTimersByTimeAsync(100);
    conn.sendResize(101, 41);
    await vi.advanceTimersByTimeAsync(100);
    conn.sendResize(102, 42);

    await vi.advanceTimersByTimeAsync(5000);
    const frames = resizeFrames(socket);
    // The three drag frames, then exactly three re-asserts of the LAST size.
    expect(frames.slice(-4)).toEqual([
      { cols: 102, rows: 42 },
      { cols: 102, rows: 42 },
      { cols: 102, rows: 42 },
      { cols: 102, rows: 42 },
    ]);
    expect(frames.filter((f) => f.cols === 100).length).toBe(1);
    expect(frames.filter((f) => f.cols === 101).length).toBe(1);
  });

  it("reassertSize() re-sends the current dims without re-arming the burst", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    const socket = last();

    conn.sendResize(90, 30);
    await vi.advanceTimersByTimeAsync(5000); // let the burst finish
    const settled = resizeFrames(socket).length;

    conn.reassertSize();
    expect(resizeFrames(socket).length).toBe(settled + 1);

    // Routing reassertSize through sendResize would re-arm the timers and the
    // burst would never terminate.
    await vi.advanceTimersByTimeAsync(60_000);
    expect(resizeFrames(socket).length).toBe(settled + 1);
    expect(resizeFrames(socket).at(-1)).toEqual({ cols: 90, rows: 30 });
  });

  it("cancels pending re-asserts when the terminal is closed", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    const socket = last();

    conn.sendResize(120, 50);
    const atClose = resizeFrames(socket).length;
    await conn.close();
    conn = null;

    await vi.advanceTimersByTimeAsync(60_000);
    expect(resizeFrames(socket).length).toBe(atClose);
  });

  it("re-asserts the dims the remote missed while the socket was down", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();

    // Resize while disconnected: sendControl drops the frame, but cols/rows are
    // still recorded, so the reconnect's `ready` re-send carries the real size
    // rather than the 80x24 the PTY was spawned at.
    last().drop(1006);
    conn.sendResize(140, 60);
    await vi.advanceTimersByTimeAsync(500);
    const reconnected = last();
    reconnected.ready();

    expect(resizeFrames(reconnected)[0]).toEqual({ cols: 140, rows: 60 });
  });
});

// ---------------------------------------------------------------------------
// Diagnostics.
//
// Two facts the connection used to throw away: the close code/reason (read once
// to decide whether to reconnect, then gone) and control frames dropped because
// the socket wasn't OPEN (a silent early return). Both are what a "it just
// disconnected" / "the remote never resized" report turns on.
//
// The redaction rule is pinned here too: the WS token rides as a subprotocol
// value, so the socket's url/protocol must never reach a snapshot.
// ---------------------------------------------------------------------------
describe("TerminalConnection diagnostics", () => {
  it("retains the close code and reason after an unexpected close", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();

    last().drop(1011, "remo-host exited");

    const diag = conn.diagnostics();
    expect(diag.lastClose).toMatchObject({ code: 1011, reason: "remo-host exited" });
    expect(Date.parse(diag.lastClose!.at)).not.toBeNaN();
    // The socket is gone, but the state machine is mid-recovery.
    expect(diag.socket).toBeNull();
    expect(diag.state).toBe("reconnecting");
  });

  it("truncates a long server-authored close reason", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();

    last().drop(1011, "x".repeat(400));

    expect(conn.diagnostics().lastClose!.reason).toHaveLength(120);
  });

  it("counts control frames dropped before the socket is open, and still tracks the grid", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect(); // socket exists but is CONNECTING, not OPEN

    conn.sendResize(140, 60);

    const diag = conn.diagnostics();
    expect(diag.droppedControlFrames).toBeGreaterThan(0);
    // The frame never left, but the dims are recorded — which is what the
    // `ready` handler re-sends.
    expect(diag.lastSentGrid).toEqual({ cols: 140, rows: 60 });
  });

  it("reports a live socket by readyState only — never its url or protocol", async () => {
    conn = new TerminalConnection("s1", 80, 24);
    await conn.connect();
    last().ready();
    // Plant what redaction must exclude on the socket the connection holds.
    Object.assign(last(), {
      url: "ws://host/api/v1/terminals/t1?sentinel=SUPERSECRET",
      protocol: "remo-terminal.v1.token.SUPERSECRET",
    });

    const serialized = JSON.stringify(conn.diagnostics());

    expect(serialized).not.toContain("SUPERSECRET");
    expect(serialized).not.toContain("url");
    expect(serialized).not.toContain("protocol");
    expect(conn.diagnostics().socket).toEqual({ readyState: 1, bufferedAmount: 0 });
  });
});
