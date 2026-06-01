# Feature Specification: Notifier Channels — interchangeable delivery channels for the notifier sidecar

**Feature Branch**: `008-notifier-channels`  
**Created**: 2026-06-01  
**Status**: Draft  
**Input**: User description: "Generalize the notifier sidecar (spec 007) so the notifier responsibility can be fulfilled by one of several interchangeable notification channels. Telegram (shipped in 007) is the first. Each channel is a separate container image built over a shared, channel-agnostic notifier core; adding a channel (e.g. Slack) means adding a channel package + its own image and registering it in a catalog, with zero changes to the core or to existing channels. A host runs exactly one channel at a time (installing replaces the prior one). The CLI install/deploy flow asks the operator which channel to install (fuzzy picker over the catalog when none is named) and runs the credential preflight specific to the chosen channel."

## Overview

Spec 007 delivered a notifier sidecar with exactly one delivery channel — Telegram — baked into a single service image, and (mistakenly) invented its own approval wire protocol. The approval-handling mechanism it established (tracking pending approvals, standing grants, fail-secure decision logic) is entirely channel-agnostic; only the last hop to a human is Telegram-specific. This feature makes two corrections at once: the delivery medium becomes pluggable, and the approval source becomes agentsh's real REST API rather than an invented schema.

This feature separates those concerns so the notifier responsibility can be fulfilled by any one of several **interchangeable channels**. The channel-agnostic behavior becomes a single shared **notifier core** that integrates with agentsh as an **approver client** (polling agentsh's `/api/v1/approvals` and resolving decisions back to it); each delivery channel (Telegram today, Slack/Discord/ntfy/email tomorrow) becomes a thin, separately built unit that the core can be paired with. Operators choose which channel to install on a given host. Crucially, the fail-secure guarantee lives in the core, so a channel can only ever fail to *deliver* a notification — it can never cause a wrongful *allow*.

The lasting deliverables are: a **channel catalog** that lists the available channels and their requirements, a **channel contract** that a new channel implements without touching the core or any existing channel, an **agentsh integration contract** the core consumes instead of an invented protocol, and an install flow that selects a channel and validates its specific prerequisites. Telegram is migrated to be the first catalog entry with no change to its delivery behavior.

## Clarifications

### Session 2026-06-01

- Q: How should each channel be packaged, given the channel-agnostic core must live in exactly one place? → A: Separate container image per channel, each built over the single shared notifier core; adding a channel adds a package + image + catalog entry and edits neither the core nor existing channels.
- Q: Can a single host run more than one channel at once, or exactly one? → A: Exactly one channel per host. Installing a channel replaces any previously installed one and reuses the existing single service identity, single bridge bind address, and single port from 007.
- Q: Must an operator supply credentials for every catalog channel, or only the one being installed? → A: Only the chosen channel. The install preflight validates exactly the credentials that the selected channel declares; other channels' requirements are irrelevant to that install.
- Q: Is the catalog extensible at runtime by operators, or fixed by what ships with the product? → A: Fixed by what ships. The catalog is the set of channels built into the released product; adding a channel is a development change (new package + image + catalog registration), not operator-side runtime configuration.
- Q: When an operator switches a host from one channel to another, what happens to in-flight approvals and standing grants? → A: They are lost, exactly as on any notifier restart (007 FR-009): the channel switch is a redeploy/restart, in-flight callers see their connection drop (fail-secure deny), and in-memory standing grants are cleared.
- Q: How is the channel chosen when the notifier is deployed via the provisioning/configure flow rather than the interactive deploy command? → A: The notifier is never deployed by the provisioning/configure flow. Installation requires the explicit `remo notifier deploy` command, and the channel is chosen there (named on the command line, or via the catalog picker). 007's configure-flow deploy behavior is dropped.
- Q: Should there be a dedicated CLI surface to list available channels and their credential requirements? → A: Yes — a `remo notifier channels` subcommand lists each available channel and the credentials it requires.
- Q: What convention governs how a channel declares its required credentials and how the operator provides them? → A: Each channel declares named environment variables following the `REMO_NOTIFIER_<CHANNEL>_<NAME>` convention; the deploy preflight checks those env vars are present and non-empty. Telegram keeps its existing `REMO_NOTIFIER_TELEGRAM_BOT_TOKEN` and `REMO_NOTIFIER_TELEGRAM_CHAT_ID` unchanged.
- Q: Should the notifier define its own approval wire protocol, or use agentsh's? → A: Use agentsh's. The notifier does NOT invent a schema; it integrates against agentsh's real approval REST API (`canyonroad/agentsh`, verified 2026-06-01). The 007 invented `/v1/approve` request/response schema is replaced by agentsh's `Request` object and decision contract (see contracts/agentsh-integration.md).
- Q: agentsh hosts the approval API (poll/resolve), and its outbound notification webhook is an unsigned generic audit-event stream — how does the notifier integrate? → A: The notifier core acts as an agentsh **approver client**: it polls `GET /api/v1/approvals` (the authoritative, resolvable source), delivers each pending approval to a human via the channel, and resolves it with `POST /api/v1/approvals/{id}` using an approver `X-API-Key`. agentsh's notification webhook, if configured at us, is treated only as an optional "poll now" trigger (it is unsigned and carries no resolvable id); the core MUST function on polling alone.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operator installs a notifier by choosing a channel (Priority: P1)

An operator wants approvals delivered on a specific host. They run the deploy command for that host. Because more than one channel can satisfy the notifier role, the command asks **which channel** to install — presenting an interactive fuzzy picker over the catalog when no channel is named on the command line, consistent with how remo already prompts for a host. The operator picks a channel; the command validates that channel's specific credentials are present and deploys it. The host ends up running that one channel as the notifier.

**Why this priority**: This is the entire reason the feature exists — turning "the notifier is Telegram" into "the notifier is whichever channel you choose." Without channel selection at install time there is no multi-channel capability. It is the MVP.

**Independent Test**: With a catalog containing at least one channel and that channel's credentials present, run deploy for a host without naming a channel; confirm a picker offers the catalog, pick one, and confirm the host ends up running exactly that channel and reports healthy.

**Acceptance Scenarios**:

1. **Given** a catalog with more than one channel and no channel named on the command line, **When** the operator runs deploy, **Then** they are offered an interactive fuzzy picker of the available channels and the chosen channel is the one deployed.
2. **Given** a channel named explicitly on the command line, **When** the operator runs deploy, **Then** that channel is deployed with no picker prompt.
3. **Given** a chosen channel whose required credentials are present, **When** deploy runs, **Then** the host ends up running that channel and it reports healthy, identifying itself as that channel.
4. **Given** a channel name that is not in the catalog, **When** the operator names it on the command line, **Then** deploy fails with a clear error that lists the available channels and deploys nothing.

---

### User Story 2 - Existing Telegram operator: delivery unchanged, approvals via agentsh (Priority: P1)

An operator who deployed the Telegram notifier under spec 007 upgrades to this release. Telegram is now "just the first channel in the catalog," built over the shared core rather than as a bespoke image. Its **delivery** experience — the deploy/status/logs/restart commands, the Telegram message format and buttons, outcome edits, standing grants — behaves identically. The one intended difference: the approval it shows is sourced from agentsh's real approval API (FR-020..FR-021), and the human's tap resolves that agentsh approval, rather than the 007 invented `/v1/approve` exchange.

**Why this priority**: The refactor must be safe for the one channel in production. If generalizing the design changes Telegram's *delivery* behavior, the feature has regressed what already works — while correctly re-pointing the approval source to agentsh is the whole reason for the change. Equal priority to US1 because shipping channel choice is worthless if it breaks the one channel that exists.

**Independent Test**: Run the Telegram workflow (deploy, status/logs, a real approval, a standing grant) against this release and confirm identical delivery behavior, with the approval rendered from an agentsh `Request` and the tap resolving the matching agentsh approval.

**Acceptance Scenarios**:

1. **Given** valid Telegram credentials and an approver key, **When** the operator deploys the Telegram channel, **Then** the message format, buttons, outcome edits, and standing-grant behavior match spec 007 exactly.
2. **Given** a pending approval in agentsh, **When** the notifier polls and delivers it, **Then** the Telegram message renders agentsh's `Request` fields (kind, target, rule, message) and a human tap resolves that approval via `POST /api/v1/approvals/{id}` with the mapped `approve`/`deny`.
3. **Given** the human taps a decision, **When** the notifier processes it, **Then** the decision reaches agentsh **only through the notifier** (the human never calls agentsh directly), and a denied/timed-out/dropped case never resolves as `approve`.

---

### User Story 3 - A developer adds a new channel without touching the core (Priority: P2)

A developer wants to add a new delivery channel (e.g. Slack). They add a new channel package implementing the channel contract, supply its own container image, declare its required credentials, and register it in the catalog. They do not edit the notifier core, and they do not edit any existing channel. The new channel immediately appears in the operator's install picker and is deployable.

**Why this priority**: The extensibility promise is the durable value beyond Telegram. It is P2 because the product is fully usable with one channel; this story proves the design holds the line that makes future channels cheap and safe.

**Independent Test**: Add a minimal second channel (even a stub that delivers to a no-op or test sink), register it, and confirm it appears in the picker and deploys — with the diff touching only the new channel's files and the catalog registration, and zero changes to the core or to the Telegram channel.

**Acceptance Scenarios**:

1. **Given** a new channel package that implements the channel contract and is registered in the catalog, **When** the catalog is listed, **Then** the new channel appears alongside existing ones.
2. **Given** the change set that adds a new channel, **When** its diff is inspected, **Then** it contains no edits to the notifier core or to any other channel.
3. **Given** a registered new channel with its credentials present, **When** an operator selects it at deploy, **Then** it deploys and serves the same approval wire protocol as every other channel.

---

### User Story 4 - Operator switches a host to a different channel (Priority: P3)

An operator running the Telegram channel on a host decides to move that host to a different channel. They deploy the new channel to the same host; the install replaces the previously installed channel rather than running both. After the switch, the host serves the same notifier role through the new channel, on the same address and port as before.

**Why this priority**: Day-2 flexibility. Useful once more than one channel exists, but not required for the core capability and lower-frequency than first install.

**Independent Test**: On a host already running one channel, deploy a different channel; confirm only the new channel is running afterward, on the unchanged address/port, and the old channel is gone.

**Acceptance Scenarios**:

1. **Given** a host already running one channel, **When** the operator deploys a different channel to it, **Then** the previous channel is replaced and only the new channel runs afterward.
2. **Given** a channel switch, **When** it completes, **Then** the notifier is reachable at the same bind address and port as before and reports the new channel as active.
3. **Given** approvals in flight or standing grants present at the moment of a switch, **When** the switch occurs, **Then** those are lost exactly as on any restart (in-flight callers receive a fail-secure deny; grants are cleared), with no fabricated allow.

---

### Edge Cases

- **No channel named, non-interactive session**: Deploy is invoked without a channel and without an interactive terminal for the picker. It MUST fail with a clear message telling the operator to name a channel, rather than hang waiting for a selection it cannot collect (consistent with remo's existing host-picker behavior).
- **Single-channel catalog**: Only one channel exists (the state at release: Telegram only). The install MAY proceed with that sole channel without forcing a meaningless choice, while still honoring an explicitly named channel.
- **Chosen channel missing credentials**: The selected channel's required credentials are absent or empty. Deploy fails loudly naming exactly what that channel needs, and deploys nothing — even though a *different* channel's credentials may be present.
- **Unknown channel named**: An operator names a channel not in the catalog. Deploy fails listing the available channels; nothing is deployed.
- **Catalog entry malformed**: A channel is registered without declaring the metadata the contract requires (e.g. no credential requirements, no image identity). This is a build-time/registration error surfaced clearly, not a silent partial deploy.
- **Switching channels mid-approval**: Covered in US4 — treated as a restart; fail-secure deny for in-flight callers; no persisted state.
- **Status/observability after switch**: Status and health for a host always reflect the *currently installed* channel, never a stale prior channel.

## Requirements *(mandatory)*

### Functional Requirements

#### Channel abstraction and catalog

- **FR-001**: The system MUST define a single channel-agnostic notifier core that owns all behavior independent of the delivery medium: the agentsh integration (polling agentsh's approval API and resolving decisions back to it — FR-020..FR-023), the in-memory tracking of approvals awaiting a human, standing grants, capacity limits, fail-secure decision logic, structured logging, and the local `/v1/health` surface. This core MUST exist in exactly one place and be shared by every channel.
- **FR-002**: The system MUST define a channel contract that a delivery channel implements to: deliver an approval request to a human, collect and report the human's decision, reflect an outcome/cancellation back to the human, and declare its readiness. This is the only surface a channel implements.
- **FR-003**: The system MUST maintain a catalog of available channels. Each catalog entry MUST declare at least: a stable channel identifier, a human-facing label, the identity of the channel's deployable image, and the credentials the channel requires.
- **FR-004**: Each delivery channel MUST be packaged as its own container image built over the shared core, such that a channel's delivery-specific dependencies are isolated to that channel's image and absent from other channels' images.
- **FR-005**: Adding a new channel MUST require only adding the channel's package, its image, and its catalog registration; it MUST NOT require editing the notifier core or any existing channel.
- **FR-006**: The catalog MUST be the set of channels built into the released product (not operator-extensible at runtime). Listing the catalog MUST report every available channel and its declared credential requirements.
- **FR-006a**: The remo CLI MUST provide a `remo notifier channels` subcommand that lists every available channel and the credentials each requires, so an operator can discover channels and their prerequisites before deploying.

#### Fail-secure invariant (channel-independent)

- **FR-007**: The fail-secure guarantee MUST live in the core: no channel implementation can cause an "allow" outcome except by relaying an authorized human's explicit approval. Every non-approval terminal outcome (timeout, delivery failure, shutdown, lost connection, capacity exhaustion) MUST resolve to deny or to no decision, regardless of channel.
- **FR-008**: A channel's only failure mode that affects an approval MUST be failure to *deliver* (or to collect a decision), which the core MUST treat as it treats any delivery failure today — refusing the request fail-secure — never as an allow.

#### Install-time channel selection

- **FR-009**: The deploy/install flow MUST allow the operator to select which channel to install on a host, accepting an explicitly named channel and, when none is named, offering an interactive fuzzy picker over the catalog consistent with remo's existing host-selection UX.
- **FR-009a**: The notifier MUST be installable only via the explicit `remo notifier deploy` command, where the channel is chosen. It MUST NOT be deployed as part of the default host provisioning/configure flow; that flow does not select or install any channel. (This supersedes spec 007's configure-flow toggle, which is dropped.)
- **FR-010**: When a named channel is not in the catalog, deploy MUST fail with a clear error listing the available channels and MUST deploy nothing.
- **FR-011**: In a non-interactive context with no channel named, deploy MUST fail with a clear, actionable message rather than block on a picker it cannot present.
- **FR-012**: Deploy MUST run the credential preflight for the *selected* channel only, using that channel's declared credential requirements, and MUST fail loudly — deploying nothing — if any required credential for that channel is missing or empty.
- **FR-012a**: A channel MUST declare its required credentials as named environment variables following the `REMO_NOTIFIER_<CHANNEL>_<NAME>` convention; the preflight (FR-012) checks those variables are present and non-empty. The Telegram channel MUST retain its existing `REMO_NOTIFIER_TELEGRAM_BOT_TOKEN` and `REMO_NOTIFIER_TELEGRAM_CHAT_ID` variables unchanged (FR-017).

#### Single channel per host

- **FR-013**: A host MUST run exactly one channel at a time. Installing a channel MUST replace any previously installed channel on that host rather than run additional ones.
- **FR-014**: A channel switch MUST preserve the notifier's existing single network identity: the same single managed service, the same single bridge bind address, and the same single port established in spec 007.
- **FR-015**: Switching channels MUST behave as a restart with respect to state: in-flight approvals and in-memory standing grants are lost (no persistence), in-flight callers experience a fail-secure deny, and no allow is fabricated.

#### Observability of the active channel

- **FR-016**: The notifier's health/status surface MUST report which channel is currently active on a host (and the reachability of its agentsh connection), so operators and status commands always reflect the installed channel rather than a stale one. The local `/v1/health` shape from 007 is retained for this purpose.

#### Non-regression

- **FR-017**: Telegram MUST be migrated to be the first catalog entry, built over the shared core, with no change to its **delivery** behavior: message format, inline controls, outcome edits, and standing grants all match spec 007. The approval *content* it renders is now sourced from agentsh's `Request` (FR-021) rather than the 007 invented request body — the only intended observable change.
- **FR-018**: The integration contract the notifier depends on MUST be agentsh's approval REST API (contracts/agentsh-integration.md), not a notifier-defined wire protocol. The 007 `/v1/approve` push-intake endpoint and its invented request/response schema are removed.
- **FR-019**: This feature MUST NOT change the behavior of any existing remo command beyond (a) adding channel selection to deploy and (b) removing the notifier deployment from the provisioning/configure flow (per FR-009a), and MUST NOT force the laptop-side install to acquire any channel's delivery-specific runtime dependencies.

#### agentsh integration (channel-independent)

- **FR-020**: The core MUST integrate with agentsh by acting as an **approver client** of agentsh's approval REST API: poll `GET /api/v1/approvals` for pending approvals, and resolve each with `POST /api/v1/approvals/{id}` carrying `{"decision":"approve"|"deny","reason"}`. It MUST authenticate with an **approver**-role `X-API-Key` (never an agent key), and MUST surface a clear error if agentsh has auth disabled (its approvals API is then unavailable).
- **FR-021**: The notifier MUST NOT define its own approval schema. The approval it delivers to a human is agentsh's `Request` object (`id`, `created_at`, `expires_at`, `session_id`, `command_id`, `kind`, `target`, `rule`, `message`, `fields`), and the decision it returns uses agentsh's vocabulary. The internal allow/deny logic maps to agentsh's `approve`/`deny` per contracts/agentsh-integration.md. The integration MUST be pinned to a specific agentsh version.
- **FR-022**: If agentsh is configured to POST its notification webhook at the notifier, the core MAY use it as a low-latency "poll now" trigger, but MUST treat it as untrusted (it is an unsigned, generic audit-event stream carrying no resolvable approval id) and MUST remain fully functional on polling alone.
- **FR-023**: agentsh owns the approval lifetime (`expires_at` / its configured timeout). The notifier MUST deliver and resolve within that window and MUST NOT fabricate an `approve`; if no human responds, the notifier defaults to `deny` (or leaves agentsh's own expiry to fail-secure). Timeout/cancellation semantics are agentsh's, not an invented contract.

### Key Entities *(include if feature involves data)*

- **Notifier Core**: The single channel-agnostic implementation of the notifier responsibility — the agentsh approver-client integration (poll/resolve), tracking of approvals awaiting a human, capacity handling, standing grants, fail-secure decision logic, logging, and the local health surface. Shared by all channels; contains no delivery-medium specifics.
- **agentsh Approval (`Request`)**: The unit of work, owned by agentsh and fetched via its approval API — `id`, `created_at`, `expires_at`, `session_id`, `command_id`, `kind`, `target`, `rule`, `message`, `fields`. The notifier renders this for the human and resolves it by `id`; it does not define this shape.
- **agentsh Approvals API**: The external contract the notifier integrates against — `GET /api/v1/approvals` (list pending) and `POST /api/v1/approvals/{id}` (resolve), `X-API-Key` approver-role auth. The source of truth for pending approvals and their ids.
- **Notification Channel**: A delivery medium that fulfills the notifier role over the channel contract (e.g. Telegram, later Slack). Packaged as its own image over the core. Carries delivery-specific logic and dependencies only.
- **Channel Catalog**: The set of channels available in the released product. Each entry declares the channel identifier, human-facing label, deployable image identity, and required credentials.
- **Channel Credential Requirement**: The credentials a given channel needs to operate (e.g. Telegram's bot token and chat identifier), declared by the channel as named `REMO_NOTIFIER_<CHANNEL>_<NAME>` environment variables and checked by the install preflight for the selected channel only.
- **Installed Channel (per host)**: Which single channel is currently deployed on a host. Replaced — never accumulated — on a subsequent install; surfaced by health/status.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can install a notifier on a host by selecting a channel — choosing from a picker when none is named, or naming one directly — in a single deploy command, and the host ends up running exactly that one channel reporting healthy.
- **SC-002**: Adding a new channel is achievable by adding only the new channel's package, image, and catalog registration: the change set touches zero lines of the notifier core and zero lines of any existing channel, yet the new channel appears in the install picker and deploys.
- **SC-003**: Telegram's delivery behavior (message format, buttons, outcome edits, standing grants) is unchanged from spec 007 — verifiable by the existing Telegram operator workflow — while the approval content it renders is sourced from agentsh's `Request`, and decisions are resolved against agentsh's approval API.
- **SC-004**: No terminal outcome other than an explicit authorized human approval yields "allow," verified across every channel and across timeout, delivery-failure, capacity, and shutdown scenarios — the guarantee holds in the core regardless of channel.
- **SC-005**: Installing a second channel on a host that already runs one leaves exactly one channel running afterward, on the unchanged bind address and port, with status reporting the new channel.
- **SC-006**: A channel's delivery-specific dependencies are present only in that channel's image and absent from other channels' images and from the laptop-side CLI install.
- **SC-007**: An operator selecting a channel whose credentials are absent is stopped at preflight with a message naming exactly what that channel requires, and nothing is deployed — even when another channel's credentials are present.

## Assumptions

- The notifier core is the channel-independent behavior from spec 007 (tracking pending approvals, standing grants, capacity, logging, fail-secure logic), **with its approval-source edge re-pointed** from the invented `/v1/approve` intake to agentsh's approval REST API (FR-020..FR-023). This is the one deliberate redesign beyond relocation.
- agentsh ([`canyonroad/agentsh`](https://github.com/canyonroad/agentsh)) is the external system of record for approvals; it is configured with `approvals.mode=api`, auth enabled (`api_key`), and an approver key issued to the notifier. The integration is pinned to a verified agentsh version (schema captured 2026-06-01 in contracts/agentsh-integration.md).
- Spec 007 is merged and its Telegram service is the starting point; Telegram becomes the first catalog entry rather than being rewritten (its delivery code is preserved; its approval-data source changes).
- Some agentsh details remain to confirm against a live instance before GA (exact webhook event `type`, whether `fields` ever carries the approval id, future webhook signing) — tracked in contracts/agentsh-integration.md; the polling design does not depend on them.
- The single-human / single-authorized-recipient model from 007 is retained per channel; multi-recipient routing remains out of scope.
- The host-side build/deploy mechanism and network exposure model from 007 (build on host, bind to the container bridge, hardened least-privilege container, managed auto-restarting service) carry forward; this feature parameterizes them by channel rather than changing them.
- Only one channel ships at release (Telegram); the catalog and contract are built to make the second channel a drop-in, but no second channel is required by this feature.
- Operators pre-stage only the selected channel's credentials; there is no requirement to configure credentials for channels they do not install.

## Out of Scope

- Building any specific second channel (Slack, Discord, ntfy, email). This feature delivers the catalog, contract, and selection — not a new delivery medium.
- Running multiple channels concurrently on one host, channel fan-out, or first-response-wins across channels.
- Operator-extensible/runtime-pluggable catalog (e.g. dropping in a channel without a product release).
- Changing agentsh itself, or defining a new approval protocol: the notifier consumes agentsh's existing API as-is. Also out of scope: the single-recipient model and persistence of approval state.
- Consuming agentsh's notification webhook as an authoritative/trusted source (it is unsigned today); it is used only as an optional poll trigger.
- A migration tool for in-flight state across a channel switch (a switch is a restart; state loss is expected and fail-secure).
- Publishing prebuilt channel images to a registry (host-side build carries forward from 007; registry publication remains deferred).
