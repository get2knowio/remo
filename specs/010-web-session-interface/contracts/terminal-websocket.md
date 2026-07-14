# Contract: Terminal WebSocket (`WS /api/v1/terminals/{terminal_id}`)

One WebSocket ↔ one server-side PTY + SSH attachment ↔ one browser terminal. Subprotocol:
`remo-terminal.v1`.

## Handshake / authorization

1. Client opens the WS with two subprotocol tokens: the protocol id `remo-terminal.v1` and the
   single-use `ws_token` from `POST /terminals`. The token travels **only** in the
   `Sec-WebSocket-Protocol` header — never in the URL/query (FR-049).
2. Server validates: `Origin` ∈ configured origins and `Host` ∈ allowlist (FR-048); token exists, is
   unexpired (≤30 s default), unconsumed, and bound to this `terminal_id`; the bound `session_target`
   is still present in the current registry+discovery cache (FR-050).
3. On success the token is **atomically consumed** (single-use; replay after this fails) and the server
   spawns the PTY + `ssh -tt <opts> <target> "remo-host sessions attach --project <quoted>"`.
4. On any failure the server closes with a typed close code + control `error` frame (see below) and
   reaps any partially-created PTY/SSH (FR-023, no orphans).

## Frame types

| Direction | Frame | Meaning |
|---|---|---|
| Browser → Server | **binary** | Terminal input bytes → PTY stdin (bracketed paste included). |
| Server → Browser | **binary** | PTY output bytes → renderer (byte-safe, arbitrary). |
| Both | **text (JSON)** | Versioned control messages (below). |

Binary carries PTY data; text carries control (FR-017). Max frame and total message sizes are bounded
(configurable) — oversized frames close the socket with `policy_violation`.

## Control messages (text, JSON, `"v":1`)

Browser → Server:
```json
{"v":1,"type":"resize","cols":120,"rows":32}   // cols/rows clamped to safe bounds (FR-060) → TIOCSWINSZ
{"v":1,"type":"ping"}
```
Server → Browser:
```json
{"v":1,"type":"ready"}                                   // PTY+ssh up; first output imminent
{"v":1,"type":"exit","code":0}                           // remote/ssh process ended
{"v":1,"type":"error","class":"auth","message":"…"}      // class ∈ auth|network|remote_capability|missing_project|remote_launch
{"v":1,"type":"pong"}
```
Error `message` is human-safe and secret-free (no keys, tokens, or full proxy command — FR-028).

## Backpressure & limits

- Server maintains a bounded per-terminal output buffer with a configurable byte cap (FR-021). When the
  browser cannot keep up, the server pauses the PTY reader (flow control) rather than growing memory
  unbounded; on prolonged stall it may close with `try_again_later`.
- Input is streamed to the PTY without unbounded server buffering.

## Disconnect / reconnect

- Browser close or transport loss → server reaps the PTY/SSH process group (SIGTERM→SIGKILL), leaving
  the **remote Zellij session running** (FR-019). Killing the local ssh only detaches.
- Reconnect is **not** a resume of this socket: the client calls `POST /terminals` again (fresh
  single-use token) and opens a new WS to the new `terminal_id`, reaching the same still-running Zellij
  session (FR-020, Clarifications Q2 bounded auto-reconnect → manual fallback).
- Browser scrollback persistence across a full page reload is **not** required in the MVP.

## Isolation guarantee

Output and errors are delivered only to their own terminal's socket; a failure in one terminal is never
rendered in another's stream (FR-023, SC-003), even when project names repeat across instances.

## Close codes (summary)

| Code | Reason |
|---|---|
| 1000 | Normal (client close / `exit`) |
| 1008 (policy_violation) | Bad Origin/Host, bad/expired/replayed token, oversized frame |
| 1011 (internal_error) | Server-side setup failure (typed `error` frame precedes) |
| 1013 (try_again_later) | Backpressure stall / cap reached |
