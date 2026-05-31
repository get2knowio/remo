# Feature Specification: Notifier Sidecar — Telegram approval bridge for agentsh

**Feature Branch**: `007-notifier-sidecar`  
**Created**: 2026-05-31  
**Status**: Draft  
**Input**: User description: "Notifier Sidecar: Telegram approval bridge for agentsh — a long-running HTTP daemon in an OCI container on each remo-provisioned instance that receives agentsh approval webhooks, delivers them to a human via a Telegram bot, and returns the human's decision back to agentsh within the approval timeout."

## Overview

When the future agentsh execution-layer security model decides an operation needs human approval, it must reach a human who is not watching the terminal. The notifier is the push channel that closes that gap: it accepts an approval request from a devcontainer on the same instance, alerts a human on Telegram, and returns the human's allow/deny decision synchronously — failing secure (deny) if no one answers in time.

This spec establishes the notifier service and, critically, the **durable wire protocol** that future approval emitters integrate against. Telegram is the only delivery channel implemented in v1, but the request/response contract and the operator workflow (deploy, test, observe) are the lasting deliverables.

## Clarifications

### Session 2026-05-31

- Q: When the notifier restarts while approvals are in flight, what happens to those pending approvals? → A: They are lost; the caller's open connection drops and the caller (agentsh) treats a dropped connection as a fail-secure deny. No persistence.
- Q: Who is authorized to answer an approval? → A: Exactly one pre-configured Telegram chat per instance (single human/chat). Multi-recipient and identity-aware routing are out of scope for v1.
- Q: What is the network exposure of the approval endpoint? → A: Bound to the host's container-bridge address only — reachable by co-located devcontainers but not from outside the host; no transport encryption or caller authentication in v1.
- Q: Is there a bound on how many approvals can be pending at once (any bridge container can POST with no caller auth)? → A: Yes — a configurable maximum concurrent pending approvals (default 50); beyond it, new requests are rejected with a service-unavailable signal until capacity frees up.
- Q: What happens when delivering the notification for one specific request fails while the transport is otherwise up? → A: Fail that request immediately with a service-unavailable signal and hold no pending slot; the caller retries or applies its own deny.
- Q: What happens when a request arrives carrying an approval_id that is already pending? → A: Reject the duplicate with a client-error signal while the original is still pending; the original keeps running.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Human approves or denies an operation from their phone (Priority: P1)

A devcontainer's security layer encounters an operation its policy flags for human approval and sends an approval request to the notifier. The human receives a Telegram message describing the operation (project, command, rule, instance) with **Approve** and **Deny** buttons. They tap one; the decision is delivered back to the waiting caller, and the Telegram message updates to show who decided and when.

**Why this priority**: This is the entire reason the service exists. Without it there is no human-in-the-loop approval channel and the agentsh security model cannot function. It is the MVP — everything else supports this loop.

**Independent Test**: Send a single approval request to a running notifier; confirm a Telegram message with two buttons arrives, tap a button, and confirm the caller receives the matching decision within seconds and the Telegram message reflects the outcome.

**Acceptance Scenarios**:

1. **Given** a running, configured notifier, **When** an approval request arrives and the human taps **Approve** within the timeout, **Then** the caller receives an "allow" decision identifying the responder and the elapsed time, and the original Telegram message is edited to show it was approved.
2. **Given** a running, configured notifier, **When** an approval request arrives and the human taps **Deny**, **Then** the caller receives a "deny" decision and the Telegram message is edited to show it was denied.
3. **Given** an approval request with a 5-second timeout, **When** no human responds, **Then** the caller receives a fail-secure "deny" decision marked as a timeout within roughly 5–6 seconds, and the Telegram message is edited to show it timed out.

---

### User Story 2 - Operator deploys the notifier to an instance (Priority: P1)

An operator who already manages remo-provisioned hosts wants the approval channel running on a specific host. From their laptop they run a single deploy command naming the host. The notifier is built/installed and started on that host as a managed background service, bound so that only co-located devcontainers can reach it. The command fails loudly if the required Telegram credentials are not configured.

**Why this priority**: An approval loop nobody can stand up has no value. Deployment must be a one-command operator action consistent with the rest of the remo CLI, and it is a prerequisite for Story 1 to run anywhere real.

**Independent Test**: Run the deploy command against a fresh host that has only the base container runtime, then confirm the service is active and answering its health probe, with no manual steps in between.

**Acceptance Scenarios**:

1. **Given** a reachable host and valid Telegram credentials available to the operator, **When** the operator runs the deploy command for that host, **Then** the notifier service ends up active and running and the deploy reports success.
2. **Given** missing or empty Telegram credentials, **When** the operator runs the deploy command, **Then** it fails with a clear message naming what is missing and does not start a broken service.
3. **Given** an already-deployed host, **When** the operator runs deploy again with a rebuild flag, **Then** the service is rebuilt and restarted and ends up active and running.

---

### User Story 3 - Operator verifies wiring end-to-end (Priority: P2)

After deploying (or when first setting up Telegram), the operator runs a test command for the host. This sends a clearly test-labeled approval through the full path; the human receives a test Telegram message and taps a button; the command reports the round-trip succeeded and shows the decision. This confirms the bot token, chat ID, network binding, and decision return path all work without waiting for a real agentsh event.

**Why this priority**: First-time Telegram setup has several independent failure points (wrong token, wrong chat ID, bot never messaged, port binding). A dedicated test command turns a frustrating multi-step debug into one observable action. Valuable but not required for the core loop to function.

**Independent Test**: Run the test command against a deployed host; confirm a test-labeled Telegram message arrives, tap a button, and confirm the command prints the returned decision.

**Acceptance Scenarios**:

1. **Given** a deployed, healthy notifier, **When** the operator runs the test command, **Then** a test-labeled approval arrives on Telegram and the operator's tapped decision is reported back by the command.
2. **Given** a host where the notifier is not running, **When** the operator runs the test command, **Then** it reports the service is unreachable rather than hanging indefinitely.

---

### User Story 4 - Operator observes and controls a running notifier (Priority: P3)

An operator wants to check whether a host's notifier is healthy, read its logs to diagnose an issue, or restart it after a configuration change. The CLI exposes status, logs (optionally followed), and restart subcommands for a named host, matching the host-selection UX of the rest of remo (including fuzzy host picking when no host is named).

**Why this priority**: Day-2 operability. The service can be deployed and exercised without these, but they make ongoing operation and debugging practical. Lowest priority because each is a thin convenience over what an operator could otherwise do by hand.

**Independent Test**: Against a deployed host, run status and confirm it reports the health summary; run logs and confirm service log lines stream; run restart and confirm the service returns to active.

**Acceptance Scenarios**:

1. **Given** a deployed notifier, **When** the operator runs status, **Then** the current health summary (status, version, transport, uptime, count of pending approvals) is displayed.
2. **Given** a deployed notifier, **When** the operator runs logs with the follow option, **Then** service log output streams until the operator stops it.
3. **Given** a deployed notifier, **When** the operator runs restart, **Then** the service stops and comes back to active and running.
4. **Given** no host is named on any of these subcommands, **When** the operator runs it, **Then** they are offered an interactive fuzzy picker of known hosts, consistent with other remo commands.

---

### Edge Cases

- **Late or duplicate response**: A human taps a button for an approval that already timed out, or taps twice. The notifier ignores responses for approvals that are no longer pending and does not change an already-decided outcome.
- **Unauthorized responder**: A button tap or message arrives from a chat that is not the configured authorized chat. It is ignored and has no effect on any approval.
- **Malformed request**: An approval request that fails schema validation is rejected with a client error and never produces a Telegram message.
- **Transport unavailable**: The Telegram channel cannot be reached, or no human-side configuration is loaded. New approval requests are refused with a service-unavailable signal rather than silently hanging.
- **Shutdown with in-flight approvals**: The service is stopping while approvals await a human. In-flight callers are released (their connections drop / they receive a service-unavailable signal); no decision is fabricated as "allow". Restart loses all pending approvals (no persistence), and callers treat the dropped connection as deny.
- **Oversized timeout**: A request asks for a timeout beyond the configured maximum. The notifier clamps to the maximum rather than honoring an unbounded wait.
- **Secret rotation**: The Telegram bot token changes. The operator rotates it by updating the stored secret and restarting (or signaling) the service; the token is never read from the main config file or emitted in logs.
- **Concurrent approvals**: Multiple approval requests are pending at once. Each is tracked independently by its own identifier; a decision on one never resolves another.
- **Duplicate request identifier**: A request arrives bearing an approval identifier already in flight. It is rejected with a client error and the original pending approval is untouched; no second notification is sent.
- **Capacity reached**: The maximum number of concurrent pending approvals is already in flight. New requests are refused with a service-unavailable signal and no notification, until a pending approval resolves.
- **Notification send failure**: Delivery of a specific request's notification fails though the transport is otherwise up. That request fails immediately with a service-unavailable signal and never occupies a pending slot.

## Requirements *(mandatory)*

### Functional Requirements

#### Approval intake and response

- **FR-001**: The notifier MUST accept approval requests over HTTP at a versioned endpoint and validate them against the published request schema, rejecting malformed requests with a client-error status and no notification.
- **FR-002**: The notifier MUST hold the caller's request open and return a single response only when a decision exists — a human decision, a timeout, or service shutdown.
- **FR-003**: For each request, the notifier MUST generate an approval identifier if the caller did not supply one, and echo the approval identifier in the response.
- **FR-003a**: If a request supplies an approval identifier that matches an approval already pending, the notifier MUST reject the duplicate with a client-error signal and leave the original pending approval running undisturbed (one live approval per identifier).
- **FR-004**: On a human allow/deny, the notifier MUST return a success status with the decision, the responder's identity, an optional reason, the decision timestamp, and the elapsed latency.
- **FR-005**: On expiry of the request's timeout with no human response, the notifier MUST return a distinct timeout status carrying a fail-secure "deny" decision marked with a timeout reason.
- **FR-006**: The notifier MUST clamp any requested timeout to a configured maximum and apply a configured default when the request omits a timeout.
- **FR-007**: When the transport is unreachable, no human-side configuration is loaded, or the service is shutting down, the notifier MUST refuse new approval requests with a service-unavailable signal rather than hanging or fabricating an allow.
- **FR-034**: The notifier MUST enforce a configurable maximum number of concurrent pending approvals (default 50). While that limit is reached, the notifier MUST reject further approval requests with a service-unavailable signal — without sending a notification — until a pending approval resolves and frees capacity. This bounds memory use and protects the single human channel from notification flooding by a co-located container.

#### Fail-secure guarantees

- **FR-008**: The notifier MUST NEVER return "allow" except as the direct result of an authorized human explicitly approving. Every other terminal outcome (timeout, error, shutdown, lost connection) MUST resolve to deny or to no decision at all.
- **FR-009**: The notifier MUST NOT persist approval state; a restart loses all in-flight approvals, and callers whose connection drops MUST be able to treat that as a deny.

#### Telegram delivery

- **FR-010**: For each accepted request, the notifier MUST send a message to the single configured authorized chat describing the project, the operation (kind, command, arguments), the triggering rule, the human-readable policy message, the instance identifier, and the decision deadline, with inline **Approve** and **Deny** controls.
- **FR-010a**: If delivering the notification for a specific request fails while the transport is otherwise available, the notifier MUST fail that request immediately with a service-unavailable signal and MUST NOT hold a pending slot for it. A request is registered as pending only once its notification has been delivered.
- **FR-011**: The notifier MUST accept a decision only from the configured authorized chat and MUST ignore responses originating from any other chat.
- **FR-012**: The notifier MUST correlate each response to its originating approval by identifier and MUST ignore responses for approvals that are no longer pending (already decided, timed out, or unknown).
- **FR-013**: After an outcome (approved, denied, timed out, or cancelled), the notifier MUST update the original Telegram message to reflect that outcome, including the responder and time for human decisions.
- **FR-014**: The notifier MUST use a delivery mode that requires no publicly reachable inbound URL for the bot, so the service needs no public endpoint.
- **FR-015**: The notification channel MUST be pluggable behind a single transport abstraction so additional channels can be added later without changing the intake or state logic. Only the Telegram channel is implemented in v1, and the abstraction MUST also expose a cancellation path for an approval resolved by other means.

#### Health and observability

- **FR-016**: The notifier MUST expose an unauthenticated health endpoint returning status, service version, active transport name, uptime, and the count of currently pending approvals.
- **FR-017**: The notifier MUST NOT emit secrets or sensitive request content at normal log levels; the bot token, raw request bodies, and workspace paths MUST appear only at debug level, while normal levels carry structural metadata only (such as approval identifier, decision, and latency).

#### Configuration and secrets

- **FR-018**: The notifier MUST read its configuration from a single file, validate it strictly, and reject unknown configuration keys with a clear error.
- **FR-019**: The notifier MUST read the Telegram bot token from a separate secret file at startup (never from the main configuration file) so the secret can be rotated by editing that file and restarting or signaling the service.
- **FR-020**: Configuration MUST include the listening parameters, default and maximum approval timeouts, the maximum number of concurrent pending approvals, the selected transport, the transport's authorized chat identifier and secret location, and the instance identifier shown to humans.

#### Network exposure

- **FR-021**: The deployed notifier MUST be reachable by devcontainers co-located on the same host but MUST NOT be reachable from outside the host; it is bound to the host's container-bridge address.

#### Operator deployment and lifecycle

- **FR-022**: The remo CLI MUST provide a command to deploy the notifier to a named known host — including a fresh host that has only the base container runtime — ending with the service running as a managed, auto-restarting background unit.
- **FR-023**: Deployment MUST fail loudly with a clear message if the required Telegram credentials are not available, and MUST NOT leave a half-configured or broken service running.
- **FR-024**: Deployment MUST provide an option to force a rebuild of the service image on the host.
- **FR-025**: Deployment MUST verify the service answers its health endpoint before reporting success, and MUST fail if it does not come up within a reasonable bound.
- **FR-026**: The deployed service MUST run with hardened, least-privilege container settings (non-root user, dropped capabilities, read-only root filesystem) and MUST restart automatically if it exits.

#### Operator verification and observability commands

- **FR-027**: The remo CLI MUST provide a test command that sends a clearly test-labeled approval through the full path against a named host and reports the returned decision, surfacing an unreachable service rather than hanging.
- **FR-028**: The remo CLI MUST provide a status command that retrieves and displays the named host's notifier health summary.
- **FR-029**: The remo CLI MUST provide a logs command for a named host, with options to follow the stream and to limit the number of lines.
- **FR-030**: The remo CLI MUST provide a restart command that restarts the named host's notifier service.
- **FR-031**: Every notifier CLI subcommand that takes a host MUST offer an interactive fuzzy host picker when no host is named, consistent with existing remo commands.

#### Non-regression and packaging

- **FR-032**: Adding the notifier MUST NOT change the behavior of any existing remo command, and the laptop-side CLI install MUST NOT be forced to pull in the notifier's runtime dependencies (they live behind an optional install).
- **FR-033**: Deployment MUST be integrable into the existing per-host configuration flow via a toggle that can be turned off, consistent with how other optional host components are enabled/disabled.

### Key Entities *(include if feature involves data)*

- **Approval Request**: An inbound ask for a human decision. Carries an optional approval identifier, an opaque session identifier, an operation descriptor (kind, command, arguments, optional path/remote host/port, nesting context and depth), the triggering rule name, a human-readable policy message, the workspace path, the instance identifier, the project name, a requested timeout, and a submission timestamp.
- **Approval Response**: The outbound decision. Carries the approval identifier, the decision (allow or deny), the responder identity, an optional reason, the decision timestamp, and the measured latency.
- **Pending Approval**: The in-memory record of a request awaiting resolution, keyed by approval identifier, holding the means to deliver the eventual decision back to the waiting caller. Exists only between intake and resolution; never persisted.
- **Transport**: A notification channel responsible for delivering an approval request to a human, collecting their decision, and reflecting cancellation. Telegram is the only concrete transport in v1.
- **Notifier Configuration**: The validated settings governing listening parameters, timeout bounds, the selected transport and its parameters (authorized chat, secret location, message formatting), and the instance identity shown to humans.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a human taps a decision button, the waiting caller receives the matching decision within 5 seconds.
- **SC-002**: A request whose timeout elapses with no human response returns a fail-secure deny within roughly its timeout window (within about 1 second of the deadline).
- **SC-003**: An operator can take a fresh host to a running, health-passing notifier with a single deploy command and no manual post-steps, and the service reports healthy within 5 seconds of starting.
- **SC-004**: A first-time user can go from zero (no bot) to a confirmed working approval round-trip using only the documented setup steps plus the deploy and test commands.
- **SC-005**: No terminal outcome other than an explicit authorized human approval ever yields "allow" — verified across timeout, malformed-request, transport-down, and shutdown scenarios.
- **SC-006**: Secrets and sensitive request content (bot token, raw bodies, workspace paths) never appear in logs at normal verbosity.
- **SC-007**: Installing and using the notifier introduces no change to any existing remo command's behavior, and a default laptop install does not acquire the notifier's extra runtime dependencies.
- **SC-008**: The published wire protocol (request schema, response schema, status codes, timeout and cancellation semantics) is documented completely enough that an independent emitter could integrate against it without reading the notifier's code.

## Assumptions

- The target hosts already have a container runtime available (provisioned by the existing remo host configuration), so the notifier role can depend on it rather than installing it.
- Exactly one human (one Telegram chat) is authorized per instance in v1; multi-recipient routing, first-response-wins, and identity-aware authorization are deferred.
- Telegram is the only delivery channel in v1; the transport abstraction keeps other channels possible but none are built.
- The threat model excludes cross-host eavesdropping in v1: the endpoint is bound to the host container bridge and is not encrypted or caller-authenticated. This is revisited only if multi-host networking becomes a feature.
- The service image is built on the host during deployment in v1; publishing a prebuilt image is deferred until the container surface stabilizes.
- Callers (agentsh) own their own timeout-action policy; the notifier's responsibility ends at returning a deny on timeout.

## Out of Scope

- Any non-Telegram delivery channel (Slack, Discord, ntfy, email).
- Multi-user / multi-chat fanout, first-response-wins routing, and identity-aware authorization.
- Persistent storage of in-flight approvals across restarts.
- A web UI or dashboard for in-flight or historical approvals.
- Approval history / audit export beyond what the host's service logs already provide.
- Integration with downstream consumers (e.g. workflow status announcers or lifecycle-event emitters); this spec ships the foundation and the wire-protocol contract only.
- Prebuilt published container images.
- Transport encryption (TLS) for the listener.
- A dedicated CLI wrapper for bot-token rotation (rotation is supported by editing the secret file and restarting/signaling).
