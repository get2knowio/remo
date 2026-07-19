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
    drop(code = 1006): void {
      this.readyState = 3;
      this.onclose?.({ code } as CloseEvent);
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
