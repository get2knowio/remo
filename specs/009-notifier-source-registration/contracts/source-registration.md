# Contract: Source Registration Control Plane

The bridge-bound, **unauthenticated** notifier surface that accepts and holds
source **presence connections**, plus the read-only status surface. Consistent
with spec 007's trust model (FR-010): bound to the host container bridge,
reachable only by co-located devcontainers; no caller authentication. The open
connection **is** the registration; its drop is the de-registration (FR-006/FR-007).

**Transport** (clarified): a held-open HTTP/1.1 streaming request with
application-level keepalive ticks — no HTTP/2, no new server deps. Bind/port are
unchanged from 008 (default bridge `172.17.0.1:18181`).

---

## `POST /v1/sources` — register + hold the presence connection

The source opens this request and **keeps it open**. While it is open the source
is registered and polled; when it drops the source is removed.

**Request body** (`application/json`, `SourceRegistration`):

```json
{
  "source_id": "proj-a",
  "api_url": "http://proj-a:8080",
  "api_key": "<approver X-API-Key, inline>",
  "labels": {"project": "proj-a", "owner": "paul"}
}
```

- `source_id` — stable, `^[A-Za-z0-9._-]{1,64}$`, 1:1 with the devcontainer.
- `api_url` — **notifier-reachable** agentsh approvals base URL (requires a shared
  network path; deployment prerequisite).
- `api_key` — approver-role key, sent **inline** (clarified); held in-memory only,
  never logged or persisted.
- `labels` — optional, ≤16 string→string entries, for the status surface.

**Response (success)**: `200 OK`, `Content-Type: text/event-stream` (or
`text/plain`), **streamed and held open**. The body is a sequence of keepalive
ticks (one comment/newline line every `keepalive_interval_seconds`, default 15):

```
: keepalive 2026-06-08T12:00:00Z
: keepalive 2026-06-08T12:00:15Z
...
```

The client MUST consume the stream (e.g. `curl --no-buffer`) and treat the first
bytes as registration-confirmed. The connection stays open indefinitely; the
notifier polls `api_url` for the lifetime of the stream.

**Registration semantics**:

- On accept: the notifier registers the source and begins polling its
  `GET /api/v1/approvals` **within one poll interval** (FR-007, US1#1).
- **Duplicate `source_id`** (already registered): reconciled to a single source,
  latest connection wins (the prior connection is superseded and its poll loop
  cancelled) — never two loops for one `source_id` (FR-003, US1#4). Implemented
  via a per-`source_id` epoch.
- **De-registration**: when the stream drops — graceful close (FIN), reset (RST),
  a failed keepalive write, or no liveness within `idle_timeout_seconds`
  (default 45) — the notifier stops polling and removes the source promptly
  (FR-007). Graceful and ungraceful drops are the same outcome; only latency
  differs (instant on FIN/RST, ≤ idle timeout otherwise) (FR-008, US2#2/#3).
- **In-flight drain**: any pending approval for a removed source is locally
  abandoned to a fail-secure deny (no allow ever delivered); a best-effort `POST`
  deny to that source's agentsh is attempted only if still reachable (FR-009,
  clarified).

**Response (capacity rejection)**: `503 Service Unavailable`, JSON, returned
**before** any stream is held open (FR-004, clarified):

```json
{"error": "at_capacity", "detail": "max_sources=64 reached", "max_sources": 64}
```

The source treats this as a **retry-with-backoff** condition (non-terminal) and
keeps retrying with jitter so a freed slot is eventually claimed.

**Response (bad payload)**: `400 Bad Request`, JSON `ErrorResponse`; the
connection is not registered.

---

## `GET /v1/sources` — status surface (read-only)

Lists the live set of connected sources (FR-020, US4). No auth (bridge-only).

**Response** `200 OK`:

```json
{
  "count": 2,
  "sources": [
    {"source_id": "proj-a", "labels": {"project": "proj-a"},
     "poll_state": "polling", "last_success_at": "2026-06-08T12:00:10Z",
     "consecutive_failures": 0, "permanent": false},
    {"source_id": "seed", "labels": {},
     "poll_state": "backing_off", "last_success_at": null,
     "consecutive_failures": 3, "permanent": true}
  ]
}
```

- **Never includes** `api_key` or `api_url`.
- A source whose connection has dropped no longer appears (US4#3).
- A connected source whose agentsh endpoint is unreachable shows
  `poll_state: "backing_off"` while remaining listed (its connection is up) (US4#2).

---

## `GET /v1/health` — extended

Unchanged from 008 except: add `"sources": <count>`; `agentsh_connected` now means
"≥1 source is currently polling successfully" (back-compat for the deploy
health probe).

---

## Liveness & keepalive (the only timer)

- The notifier writes a keepalive tick every `keepalive_interval_seconds`
  (default 15). A write to a dead socket raises and triggers de-registration.
- Independently, the notifier checks `request.is_disconnected()` so an ungraceful
  drop is detected within `idle_timeout_seconds` (default 45, ≈3 missed ticks)
  even with no tick due (FR-008).
- There is **no** application heartbeat, lease TTL, or periodic re-register
  (FR-007). The source is responsible only for re-opening the connection if it
  drops (FR-012; see `devcontainer-feature.md`).

## Security model (accepted residual risk)

Open, bridge-only, no caller auth (007). A hostile co-located container could open
spurious connections, consume `max_sources` capacity, or attempt to disrupt
another source. This is an accepted residual risk of the 007 trust model and can
only cause **fail-secure denial**, never a wrongful allow (FR-010, spec Edge
Cases). Cross-source decisions never mix: each source resolves only against its
own agentsh via its own key (FR-002).
