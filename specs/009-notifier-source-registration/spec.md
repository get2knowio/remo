# Feature Specification: Notifier Source Registration — dynamic multi-agentsh polling

**Feature Branch**: `009-notifier-source-registration`  
**Created**: 2026-06-08  
**Status**: Draft  
**Input**: User description: "A host runs many devcontainers, each with its own agentsh exposing an approval API; there is one notifier container per host. Each devcontainer opens a persistent connection to the notifier to register itself. While that connection is open the notifier polls that devcontainer's agentsh for approvals and posts decisions back. If the notifier sees the connection drop it stops polling that source; if the source sees the connection drop it keeps retrying to re-establish it. Registration is driven by an opt-in devcontainer Feature; the control plane is bridge-only with no caller auth (007 trust model). The notifier also needs a configurable backoff policy for an agentsh endpoint that is reachable-but-failing while its connection is still up."

## Overview

Spec 008 re-pointed the notifier at agentsh's real approval REST API, but wired it to a **single, static** agentsh endpoint (`[agentsh] api_url`, one poll loop). The real topology is different: a host runs **many devcontainers, each running its own agentsh**, and there is **exactly one notifier container per host**. There is no agentsh multiplexer — each agentsh owns its own approval queue and must be polled (and resolved) independently.

This feature turns the notifier's single static poller into a **dynamic registry of sources**. A *source* is one registered agentsh approval endpoint (1:1 with a devcontainer). The defining mechanism is a **persistent connection whose existence *is* the registration**: a devcontainer opens a long-lived connection to the notifier (carrying its `source_id`, a notifier-reachable agentsh `api_url`, an approver key, and labels). **While that connection is open, the notifier polls that source's agentsh** and resolves decisions back to it. **When the notifier sees the connection drop, it stops polling and removes the source.** **When the source sees the connection drop, it keeps retrying to re-establish it** — which is what makes a notifier restart self-heal.

This single signal — connection up/down — replaces the heartbeat, lease, and reconcile machinery that a poll-only design would need. Graceful shutdown and ungraceful death are the *same event* (the socket is gone), so de-registration is automatic when a devcontainer just dies. A notifier restart drops every connection; each source's retry loop reconnects and re-registers, with no operator action and no persisted state (007 FR-009).

Registration is performed by an **opt-in devcontainer Feature**, so a project chooses to participate by adding the Feature — nothing is forced on projects that do not. Everything inherits 007/008's **fail-secure** invariant: a source that is unconnected, dropped, or whose agentsh is backed-off simply has no approvals delivered, which can never produce a wrongful *allow*.

## Clarifications

### Session 2026-06-08

- Q: What is the unit the notifier registry tracks and polls, given there is no agentsh multiplexer? → A: A **source** — one registered agentsh approval endpoint, 1:1 with a devcontainer. The notifier runs one independent poll/resolve loop per source.
- Q: Who registers, and how? → A: An **opt-in devcontainer Feature** opens a **persistent connection** to the notifier carrying the source's identity and agentsh endpoint. The open connection *is* the registration; the Feature is responsible for opening and re-establishing it. agentsh is not modified.
- Q: How is registration kept alive and how is a dead source detected — heartbeat, lease, or polling? → A: **None of those.** The persistent connection is the liveness signal. While it is open the source is registered and polled; when it drops the source is de-registered. There is no application heartbeat, no lease TTL, and no periodic re-register.
- Q: What happens when a devcontainer dies ungracefully (no graceful close)? → A: Identical to a graceful close — the connection drops and the notifier stops polling. Detection of an ungraceful drop (no FIN, e.g. `kill -9` / partition) is bounded by a configurable **connection keepalive/idle timeout** (transport-level PING), the only liveness timer in the design.
- Q: What happens on a notifier restart, given no persistence? → A: Every connection drops; each source's retry loop re-establishes its connection and re-registers automatically. The registry self-heals within a reconnect interval; meanwhile no approvals are delivered (fail-secure).
- Q: If a source's agentsh endpoint is failing while its connection is still up, does that de-register it? → A: No. The connection is the authoritative registration/liveness signal. agentsh poll failures while connected only **throttle the poll** via a configurable exponential-backoff policy; they never de-register. De-registration happens **iff the connection drops**.
- Q: How is the control plane secured? → A: **Open, bridge-only**, consistent with 007's trust model (bound to the host container bridge, reachable only by co-located devcontainers; no caller authentication). Cross-source interference by a hostile co-located container is an accepted residual risk that can only cause fail-secure denial.
- Q: How does the notifier reach each devcontainer's agentsh, when the presence connection runs the other way? → A: Two directions. The **presence connection** runs source → notifier (to the bridge address, as in 007). The **approval poll** runs notifier → that source's agentsh `api_url`, which requires a shared network path (a user-defined Docker network or published ports). The reachable `api_url` is part of the registration payload.
- Q: Does the static `[agentsh]` single endpoint from 008 go away? → A: It is retained as an optional **seed source** (no presence connection; always present) for back-compat and single-devcontainer setups. The dynamic connection-based registry is the primary mechanism.
- Q: What transport carries the presence connection? → A: A **held-open HTTP/1.1 streaming request** (chunked/SSE-style) with **application-level keepalive ticks**. Reuses the existing FastAPI/uvicorn stack (no HTTP/2 server/client deps); drop is detected via write failure or an idle timeout on missed ticks. This is the keepalive/idle-timeout mechanism of FR-008.
- Q: How is the approver `api_key` conveyed over the unauthenticated control plane? → A: **Inline** in the registration payload, sent verbatim by the source; the notifier holds it in-memory for that source only. The bridge is the same trust boundary the key already lives behind, and no separate secret-distribution/reference scheme is introduced (it would be out of scope). FR-017's "or a reference to it" is resolved to inline.
- Q: What does a source do when rejected because the notifier is at capacity? → A: The notifier returns an **explicit, distinguishable rejection** (e.g. HTTP `503` with a typed reason, before holding the stream open) and logs it. The Feature treats it as a **retry-with-backoff** condition — not terminal — so a slot freed later is eventually taken, without a tight reconnect loop on a saturated notifier.
- Q: What does "drain to a fail-secure deny" mean when a source is removed (FR-009)? → A: **Local abandon** — cancel that source's pending channel-side approval so no allow is ever delivered (the fail-secure guarantee is purely local and does not depend on agentsh reachability). An active `POST` deny to the source's agentsh is attempted only **best-effort** when that endpoint still happens to be reachable; its failure does not affect removal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A devcontainer's agentsh is polled while its connection is open (Priority: P1) 🎯 MVP

A devcontainer starts on a host that already runs the notifier. It opens a persistent connection to the notifier, supplying its agentsh's notifier-reachable URL and approver key. From that point — and only while the connection stays open — the notifier polls that source's `GET /api/v1/approvals`, delivers any pending approval to the human via the installed channel, and resolves the decision back to **that** source's `POST /api/v1/approvals/{id}`. Multiple devcontainers connect independently and are polled concurrently, each resolved against its own agentsh.

**Why this priority**: This is the entire reason the feature exists — one notifier serving many independent agentsh instances, gated by per-source connections. It is the MVP.

**Independent Test**: With the notifier running, open two source connections pointing at two fake agentsh endpoints; raise a pending approval on each; confirm each is delivered and each decision is resolved against the correct source, concurrently and independently.

**Acceptance Scenarios**:

1. **Given** a running notifier with no sources, **When** a devcontainer opens a source connection, **Then** the notifier begins polling that source's approval API within one poll interval.
2. **Given** two connected sources each with a distinct pending approval, **When** the notifier polls, **Then** both approvals are delivered and each decision is resolved against the source it came from (never cross-routed).
3. **Given** a delivered approval for a specific source, **When** the human taps a decision, **Then** the notifier resolves it via that source's `POST /api/v1/approvals/{id}` with that source's approver key, and the decision reaches that agentsh only through the notifier.
4. **Given** a source connection carrying a `source_id` that is already connected, **When** it is received, **Then** it is reconciled to a single source (latest connection wins / idempotent), never two concurrent poll loops for one source.

---

### User Story 2 - Connection lifecycle is the registration lifecycle (Priority: P1)

A source is registered for exactly as long as its connection is open. A graceful shutdown closes the connection; an ungraceful death drops it; in both cases the notifier stops polling and removes the source — no heartbeat or lease needed. When the source's connection drops for any reason (including a notifier restart), the source keeps retrying until it reconnects and is served again.

**Why this priority**: The connection-as-registration contract is the core correctness property. Equal priority to US1 because dynamic registration is meaningless if connection state and registration state can diverge.

**Independent Test**: Open a source connection and confirm it is polled; close it and confirm polling stops promptly. Kill the source without a graceful close and confirm the notifier removes it within the keepalive-timeout window. Restart the notifier and confirm the source's retry loop reconnects and is served again.

**Acceptance Scenarios**:

1. **Given** a connected source, **When** its connection stays open, **Then** it remains registered and polled with no heartbeat or periodic re-register required.
2. **Given** a connected source, **When** it closes the connection gracefully, **Then** the notifier stops its poll loop immediately and frees the slot; any in-flight approval for that source is locally abandoned (no allow ever delivered), with a best-effort `POST` deny only if its agentsh is still reachable.
3. **Given** a connected source, **When** it dies ungracefully (no FIN), **Then** the notifier detects the dead connection within the configured keepalive/idle timeout and removes the source.
4. **Given** connected sources, **When** the notifier process restarts, **Then** all connections drop, the registry starts empty, and each source is served again only after its retry loop reconnects — with no approval auto-allowed in the interim.

---

### User Story 3 - Opt-in devcontainer Feature maintains the connection (Priority: P2)

A developer who wants their project's agentsh approvals delivered adds a reusable **devcontainer Feature** to the project's `.devcontainer`. On container start the Feature opens the persistent connection to the host's notifier and, whenever that connection drops, keeps retrying to re-establish it. A project that does not add the Feature is never connected and is unaffected — participation is opt-in per project.

**Why this priority**: The Feature is how the connection is actually opened and kept up in practice, and keeps participation opt-in and agentsh-unmodified. P2 because the registry (US1/US2) is independently testable by driving connections directly.

**Independent Test**: Build a devcontainer that includes the Feature against a running notifier; confirm a source connection is established shortly after start, survives notifier restarts via reconnect, and ends (source removed) when the container stops — with no connection for a devcontainer that omits the Feature.

**Acceptance Scenarios**:

1. **Given** a project whose devcontainer includes the Feature, **When** the container starts, **Then** the Feature opens the source connection and the notifier begins polling that agentsh.
2. **Given** a running Feature-enabled devcontainer, **When** the connection drops (e.g. notifier restart), **Then** the Feature keeps retrying and re-establishes registration without manual action.
3. **Given** a Feature-enabled devcontainer, **When** it stops, **Then** the connection ends (immediately on graceful stop, or by keepalive timeout otherwise) and the notifier removes the source.
4. **Given** a project that does not include the Feature, **When** its devcontainer runs, **Then** no source connection is opened and the notifier is unaffected.

---

### User Story 4 - Operator observes connected sources and their health (Priority: P3)

An operator wants to see which sources a host's notifier is currently serving and their health — how many are connected, which agentsh endpoints are healthy vs backing-off, and when each was last successfully polled. The CLI surfaces this for a named host, consistent with the rest of `remo notifier`.

**Why this priority**: Day-2 operability. The registry functions without it, but visibility into a dynamic, connection-driven set of sources is what makes it diagnosable. Lowest priority.

**Independent Test**: With several sources connected (some healthy, one whose agentsh endpoint is unreachable), run the status surface and confirm it reports the count and each source's id, labels, poll state, and last-success time.

**Acceptance Scenarios**:

1. **Given** connected sources, **When** the operator runs the notifier status surface, **Then** it lists each source's id, labels, poll state (polling/backing-off), and last-success time.
2. **Given** a connected source whose agentsh endpoint is unreachable, **When** status is shown, **Then** that source is reported as backing-off rather than healthy, while remaining registered (its connection is up).
3. **Given** a source whose connection has dropped, **When** status is shown afterward, **Then** it no longer appears.

---

### Edge Cases

- **Graceful vs ungraceful end**: A clean close and a `kill -9`/partition are the same outcome — the source is removed. The only difference is latency: instant on FIN/RST, bounded by the keepalive/idle timeout otherwise.
- **Notifier restart**: All connections drop, registry starts empty (no persistence). Sources reconnect via their retry loops; until then nothing is delivered for them (fail-secure). No operator action.
- **Reconnect storm**: Many sources reconnect simultaneously after a notifier restart. Reconnect logic SHOULD jitter/backoff so the notifier is not thundering-herded; capacity limits still apply.
- **Duplicate connection for one source_id**: Reconciled to a single source (latest wins / idempotent); never two poll loops for one source.
- **Capacity exhaustion**: Connected sources reach the configured maximum. Further connections are rejected with an explicit, distinguishable signal (e.g. HTTP `503` + typed reason) and logged; existing sources are unaffected; the limit is never silently exceeded. A rejected source backs off and keeps retrying (non-terminal), claiming a slot if one frees up.
- **Reachable devcontainer, wedged agentsh**: The connection stays up (source alive) but `GET /api/v1/approvals` errors. The source remains registered; its poll backs off exponentially and keeps retrying. It is NOT de-registered — only a connection drop does that.
- **Source key rejected (auth)**: That source's poll/resolve fails-secure and it enters backoff while connected; other sources are unaffected; an auth failure is never treated as an allow.
- **Hostile co-located container (accepted risk)**: Under open bridge-only, a co-located container could open spurious connections or attempt to disrupt another source. This is an accepted residual risk of the 007 trust model; it can only cause fail-secure denial, never a wrongful allow.

## Requirements *(mandatory)*

### Functional Requirements

#### Source registry and dynamic polling

- **FR-001**: The notifier MUST maintain an in-memory registry of **sources**, where a source is one agentsh approval endpoint identified by a stable `source_id` and carrying at least: a notifier-reachable `api_url`, an approver `api_key`, optional human-facing `labels`, and poll-health bookkeeping. The registry MUST NOT be persisted (007 FR-009).
- **FR-002**: The notifier MUST run one independent poll/resolve loop per registered source: polling that source's `GET /api/v1/approvals`, delivering pending approvals via the installed channel, and resolving each decision against **that same source's** `POST /api/v1/approvals/{id}` with that source's approver key. Decisions MUST NOT be cross-routed between sources.
- **FR-003**: A second connection for an already-registered `source_id` MUST be reconciled to a single source (idempotent / latest-connection-wins), never a second concurrent poll loop for the same source.
- **FR-004**: The notifier MUST enforce a configurable maximum number of concurrently registered sources (open connections). Connections beyond the limit MUST be rejected with an **explicit, distinguishable signal** (e.g. HTTP `503` plus a typed reason, returned before the stream is held open) and MUST be logged; existing sources MUST be unaffected. The Feature MUST treat a capacity rejection as a retry-with-backoff condition (non-terminal), so a slot freed later is eventually taken without a tight reconnect loop (relates to FR-012).
- **FR-005**: The static single-endpoint configuration from spec 008 (`[agentsh] api_url`) MUST be retained as an optional **seed source**: when configured, the notifier registers it at startup as a source with no presence connection (always present), so single-devcontainer/back-compat setups work with no connection step.

#### Connection-as-registration (presence)

- **FR-006**: Registration MUST be expressed as a **persistent connection** opened by the source to the notifier's bridge-bound control plane. The connection MUST carry the source's registration data (`source_id`, `api_url`, `api_key`, `labels`). The open connection — not any heartbeat, lease, or periodic call — is the authoritative registration and liveness signal. The transport is a **held-open HTTP/1.1 streaming request** (chunked/SSE-style) on the existing FastAPI/uvicorn stack. (Concrete shape: contracts/source-registration.md.)
- **FR-007**: While a source's connection is open the notifier MUST poll/serve that source (subject to the poll-health policy, FR-013). When the notifier observes the connection has dropped (graceful close, reset, or keepalive/idle timeout) it MUST stop polling and remove the source promptly. There MUST be no application-level heartbeat, no lease TTL, and no periodic re-register.
- **FR-008**: The notifier MUST apply a configurable **connection keepalive/idle timeout** implemented as application-level keepalive ticks over the held-open HTTP/1.1 stream (server writes/expects periodic ticks; a write failure or an idle interval with no tick marks the connection dropped) so that an ungraceful drop with no FIN (e.g. `kill -9`, network partition) is detected within a bounded time. This timeout is the only liveness timer and MUST be configurable.
- **FR-009**: Removing a source (connection dropped, or capacity rejection) MUST drain any in-flight approval for that source to a fail-secure deny by **locally abandoning** the pending channel-side approval (cancel the prompt; never deliver an allow) — a guarantee that holds regardless of agentsh reachability. The notifier MAY attempt a best-effort `POST` deny to that source's agentsh only if the endpoint is still reachable; failure of that attempt MUST NOT affect removal or the local deny. No terminal outcome from a dropped connection may be an allow.
- **FR-010**: The control plane MUST be bound to the host container bridge only (reachable by co-located devcontainers, not externally) and MUST NOT require caller authentication, consistent with spec 007's trust model. The residual cross-source-interference risk under this model is accepted and MUST be documented.

#### Reconnection and restart recovery

- **FR-011**: The notifier MUST treat a dropped source connection as de-registration only (stop + remove). It MUST NOT attempt to reach back to a source out-of-band; recovery is the source's responsibility (FR-012).
- **FR-012**: The source side (the devcontainer Feature, FR-016) MUST, on observing its connection drop for any reason — including a notifier restart — keep retrying to re-establish the connection (with jitter/backoff to avoid a reconnect storm). On reconnect the source is re-registered and served again, with no operator action.
- **FR-013**: On notifier restart the registry MUST start empty (no persistence). The system MUST self-heal as sources reconnect; no approval may be auto-allowed during the gap.

#### Poll-health policy (per source, while connected)

- **FR-014**: For a source whose connection is up but whose agentsh endpoint is failing (connection errors, non-success responses, auth rejections on the poll), the notifier MUST apply a configurable exponential-backoff policy to the poll: a base interval, a backoff factor, a configurable maximum interval (cap), and jitter. The source MUST remain registered while its connection is up — poll failures MUST NOT de-register it.
- **FR-015**: Every poll-health state (polling, backing-off) MUST be fail-secure: a source not currently being successfully polled simply has no approvals delivered and MUST never produce an allow. A successful poll after backoff MUST resume normal delivery and reset the backoff.

#### Devcontainer Feature

- **FR-016**: The project MUST provide an opt-in devcontainer **Feature** that, on container start, opens the persistent source connection to the host notifier and maintains it (reconnecting on drop). A devcontainer that does not include the Feature MUST NOT be connected and MUST be unaffected.
- **FR-017**: The Feature MUST be parameterizable with at least: the notifier control-plane address (defaulting to the standard bridge address/port), the source's notifier-reachable agentsh `api_url`, the approver `api_key`, and optional `labels`. The `api_key` MUST be sent **inline** in the registration payload (verbatim, held in-memory by the notifier for that source); no key-reference/secret-distribution indirection is introduced. It MUST NOT require modifying agentsh.

#### Channel / fail-secure inheritance and observability

- **FR-018**: All spec 007/008 invariants MUST carry forward unchanged: fail-secure decision logic lives in the core; the human's decision flows human → channel → notifier → source-agentsh (the human never calls agentsh directly); approval content is agentsh's `Request`; and no terminal outcome other than an explicit authorized human approval yields allow — now across *all* connected sources.
- **FR-019**: This feature MUST be additive to the channel model (spec 008): channels are unaware of sources; the core fans approvals from many sources into the single installed channel and routes each human decision back to the originating source.
- **FR-020**: The notifier health/status surface MUST report the connected sources: the count, and per source at least the `source_id`, `labels`, current poll state (polling/backing-off), and last-success time. Removed sources MUST no longer appear.

### Key Entities *(include if feature involves data)*

- **Source**: One agentsh approval endpoint (1:1 with a devcontainer). Fields: `source_id` (stable), `api_url` (notifier-reachable), `api_key` (approver role), `labels` (optional), plus poll-health state (poll state, consecutive failures, current backoff, last-success). In-memory only; lives exactly as long as its presence connection.
- **Presence Connection**: The persistent source → notifier connection whose existence is the registration and whose drop is the de-registration. Guarded by a transport keepalive/idle timeout for ungraceful-drop detection.
- **Source Registry**: The in-memory set of sources on a notifier, bounded by a configured maximum. Supervises one poll/resolve loop per connected source; removes a source when its connection drops; never persisted.
- **Poll-Health Policy**: The per-source backoff policy for a reachable-but-failing agentsh endpoint — base interval, exponential backoff (factor, cap, jitter). Governs poll cadence only; never de-registers (the connection does).
- **Registration Control Plane**: The bridge-bound, unauthenticated notifier surface that accepts and holds source presence connections (contracts/source-registration.md).
- **Devcontainer Feature**: The opt-in reusable Feature that opens and maintains a source's presence connection (including reconnect) from inside a participating devcontainer.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: One notifier on a host concurrently serves N independent agentsh sources: each connected source is polled, its approvals delivered, and its decisions resolved against the correct source — verifiable with multiple connections and no cross-routing.
- **SC-002**: A source whose connection drops (graceful or ungraceful) is removed and its poll loop stops — within the keepalive-timeout window in the ungraceful case — with no operator action.
- **SC-003**: After a notifier restart, all sources are re-served once their retry loops reconnect, with no manual re-wiring and no approval auto-allowed during the gap.
- **SC-004**: A connected source whose agentsh endpoint is failing is not polled more aggressively than the configured backoff allows, remains registered while its connection is up, and resumes normal delivery on recovery — without affecting any other source.
- **SC-005**: Adding the devcontainer Feature to a project causes its agentsh to be connected and served and to survive notifier restarts via reconnect; omitting the Feature leaves the devcontainer unconnected and the notifier unaffected.
- **SC-006**: The notifier status surface accurately reflects the live set of connected sources and their poll health, with dropped sources absent.
- **SC-007**: No terminal outcome other than an explicit authorized human approval yields allow, across all sources and across connection churn, ungraceful death, poll-health backoff, capacity rejection, and notifier restart.

## Assumptions

- Spec 008 is the baseline: the channel-agnostic core, the agentsh approver-client (poll/resolve), and the in-memory state model. This feature generalizes the single static agentsh client into a dynamic per-source registry keyed on presence connections; it does not change the channel model or the agentsh wire contract.
- Each devcontainer runs its own agentsh in `approvals.mode=api` with auth enabled and an approver key issued to the notifier; there is no host-level agentsh multiplexer.
- The notifier and participating devcontainers share a network path (a user-defined Docker network or published ports) such that the notifier can reach each source's `api_url`, and each source can reach the notifier's bridge-bound control plane. Establishing that topology is a deployment prerequisite, surfaced in the Feature/role docs.
- The single-human / single-authorized-recipient model and the installed-single-channel-per-host model carry forward from 007/008; many sources fan into that one channel.
- All notifier state remains in-memory (007 FR-009): registry, poll-health state, pending approvals, and grants are lost on restart by design; recovery is by reconnection, not persistence.
- The open bridge-only trust model from 007 is acceptable for the control plane; interference by a co-located container can only cause fail-secure denial.

## Out of Scope

- Modifying agentsh, or defining any new approval protocol: the notifier consumes agentsh's existing approval API per source.
- Authenticated/per-source-token control plane, mutual TLS, or signed registration: explicitly deferred (open bridge-only is the chosen model).
- Persisting the registry or any approval state across restarts; recovery is by reconnection, by design.
- Cross-host source registration or a central registry spanning hosts (one notifier serves its own host's devcontainers only).
- Multi-recipient / identity-aware routing, or per-source channel selection (the host's single installed channel serves all sources).
- Building additional channels (spec 008 concern) or changing channel delivery behavior.
- Publishing the devcontainer Feature to a public registry (it may ship in-repo first; publication is a later concern).
- HTTP/3/QUIC or HTTP/2 transport for the presence connection: unnecessary on a stable local bridge (multiplexing/migration/0-RTT benefits do not apply); a held-open HTTP/1.1 streaming request with application-level keepalive is the chosen transport.
