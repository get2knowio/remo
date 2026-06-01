# Phase 0 Research: Notifier Channels

All Technical Context items resolved; no NEEDS CLARIFICATION remain. Decisions below drive Phase 1 design. Anchored to the existing 007 implementation read during planning.

## R1 — Where the channel boundary sits (core vs channel)

**Decision**: Treat the existing channel-agnostic modules (`server.py`, `state.py`, `models.py`, `grants.py`, `logging_setup.py`) and the transport ABC (`transports/base.py`) as the **core, left in place**. Introduce a `channels/` subpackage holding the catalog and per-channel packages. Move only `transports/telegram.py` (→ `channels/telegram/transport.py`) and the Telegram Pydantic config (out of core `config.py` → `channels/telegram/config.py`).

**Rationale**: The 007 core already imports nothing Telegram-specific — `server.create_app` reaches Telegram features only through the ABC plus `hasattr`-guarded hooks (`bind_grants`, `send_digest`, `set_token`). So "adding a channel touches zero core lines" (SC-002) is already true structurally; we make it *reviewable* by confining channels to `channels/`, without a large rename that would churn imports and endanger the non-regression goal (US2).

**Alternatives considered**:
- Physically move all core modules into a `core/` package — rejected: large import churn across the package and tests for cosmetic gain; raises non-regression risk for no functional benefit.
- Keep Telegram in `transports/` and only add a catalog — rejected: leaves the channel's config model in core `config.py`, so core would still "know" Telegram, undermining the zero-core-edit invariant for the next channel.

## R2 — Channel catalog: shape and location

**Decision**: A pure-data catalog at `channels/catalog.py` exposing a `CHANNELS` list of `ChannelDescriptor` (defined in `channels/base.py`). Each descriptor declares: `id`, `label`, `image_name` (e.g. `remo-notifier-telegram`), `required_env` (list of `RequiredEnv(name, secret: bool, purpose)`), a lazy transport-factory reference (import path string, resolved only in-container), and a `render_transport_toml(values) -> str` hook. The module imports **nothing heavy** (no FastAPI, no telegram) so the laptop CLI can import it freely. Adding a channel = add the channel package + append one descriptor to `CHANNELS` (the spec's permitted "catalog registration").

**Rationale**: The laptop CLI must list channels and run preflight without pulling the `[notifier]` deps (FR-019). A declarative descriptor keeps catalog reads dependency-free; the heavy transport class is referenced by string and imported lazily only inside the service container (mirrors 007's existing lazy `from ...transports.telegram import TelegramTransport`).

**Alternatives considered**:
- Python entry-points auto-discovery (`importlib.metadata`) — rejected for v1: more machinery than needed when the catalog ships with the product (FR-006, not runtime-extensible); a static list is simpler and equally satisfies "register a channel." Entry-points remain a clean future migration if third-party channels are ever wanted.
- A TOML/JSON manifest file — rejected: descriptors need behavior (toml render, factory ref); a typed dataclass is clearer and testable.

## R3 — Generic transport configuration

**Decision**: Generalize core `TransportConfig` to `{ type: str, <type>: <raw mapping> }`, reading `data["transport"][type]` and handing that sub-mapping to the selected channel's own Pydantic model for strict validation. Telegram keeps its exact `[transport.telegram]` table (`bot_token_file`, `authorized_chat_id`, `message_parse_mode`), so the rendered `notifier.toml` is unchanged. Core `config.py` no longer imports any channel config model; the channel owns and validates its slice.

**Rationale**: Satisfies FR-005 (a new channel adds its config model without editing core) and FR-017/FR-018 (Telegram config + wire shape unchanged). Strict `extra="forbid"` is preserved per channel (FR-018) by the channel's own model.

**Alternatives considered**:
- Keep the explicit `telegram: TelegramConfig | None` union and add `slack: SlackConfig | None` per channel — rejected: every channel would edit core config (violates FR-005).
- A free-form `settings: dict[str, Any]` with no per-channel model — rejected: loses strict validation and clear errors (FR-018 / Constitution IV).

## R4 — Per-channel images and the shared core in the build

**Decision**: One **parameterized Dockerfile** with `ARG CHANNEL=telegram` that runs `uv pip install ".[notifier-${CHANNEL}]"`, producing per-channel images tagged `remo-notifier-<channel>:<version>`. Reorganize extras: `notifier-core` (channel-agnostic deps), `notifier-telegram` = `notifier-core` + `python-telegram-bot`, with `notifier` kept as an alias of `notifier-telegram` for back-compat. A channel may ship an optional dedicated Dockerfile only if it needs extra system libraries.

**Rationale**: Achieves per-image dependency isolation (SC-006 — only the selected channel's deps are installed) with zero Dockerfile duplication, and makes adding a channel a matter of an extra + catalog entry. Builds stay on-host (007 model carried forward), so no registry is required.

**Alternatives considered**:
- A shared `remo-notifier-core` base image that channel images `FROM` — rejected for v1 on-host builds: introduces base-image build ordering/caching complexity without a registry; the parameterized single Dockerfile gives the same isolation more simply.
- A separate hand-written Dockerfile per channel — rejected as the default: duplicates multi-stage boilerplate; kept available only as a per-channel override for exotic needs.

## R5 — Ansible role parameterization (single service preserved)

**Decision**: Add `remo_notifier_channel` (default `telegram` for direct ansible use; always passed explicitly by the CLI). The service template runs `remo-notifier-{{ remo_notifier_channel }}:{{ remo_notifier_version }}` and passes `--build-arg CHANNEL={{ remo_notifier_channel }}` at build. The `[transport]` block in `notifier.toml.j2` is rendered from a CLI-supplied `remo_notifier_transport_toml` fragment (produced by the descriptor's `render_transport_toml`), keeping the role channel-agnostic. Secrets continue to flow through the existing secret-file mechanism, with the secret's source env var named by the descriptor. The service name (`remo-notifier.service`), bridge bind, and port are unchanged; `ExecStartPre=docker rm -f remo-notifier` already makes a channel switch a clean image swap (FR-013/FR-014/FR-015).

**Rationale**: Keeps deployment infra generic (channel knowledge stays in the descriptor) and preserves 007's single-service/single-port/single-bind model so a switch is just a restart with a different image — including the expected in-flight/grant loss (FR-015).

**Alternatives considered**:
- Per-channel Jinja transport partials inside the role (`templates/transport/<channel>.toml.j2`) — workable but spreads channel knowledge into the role; rejected in favor of the descriptor owning its TOML so "add a channel" stays in the channel package.
- Per-channel systemd unit names/ports — rejected: violates the one-channel-per-host decision (FR-013/FR-014).

## R6 — Active-channel observability (no wire change)

**Decision**: Reuse the existing `/v1/health` `transport` field, which already returns `transport.name` (the channel id). Document it as "the active channel." No schema change.

**Rationale**: FR-016 (status reports active channel) is satisfied by the field 007 already emits; keeping the key name preserves the wire contract (FR-018). The CLI `status`/`channels` surfaces present it as the channel.

**Alternatives considered**: Rename the field to `channel` — rejected: a gratuitous wire change that would break FR-018 and any 007 consumer for no real gain.

## R7 — Channel-specific features (grants UI) stay in the channel

**Decision**: Standing-grant interactions (`bind_grants`, `send_digest`, `set_token`, the `/rules` `/revoke` `/pause` `/resume` commands, and the "Always" inline flow) remain inside the Telegram channel. The core keeps its existing `hasattr`-guarded hooks, so a channel that does not implement a grants UI simply skips them; grant *enforcement* (matching, expiry, capacity) stays in the core `GrantStore` and applies regardless of channel.

**Rationale**: Grant *policy/state* is channel-agnostic (core); grant *UI* is medium-specific (channel). 007 already drew this line with duck-typing, so no core change is needed and a new channel is free to omit grant UI.

**Alternatives considered**: Promote a formal grants-UI method onto the ABC — rejected: would force every channel to implement (or stub) it; the optional-hook pattern is already in place and looser-coupled.

## R8 — Test strategy for the extensibility guarantee

**Decision**: Relocate existing tests under `tests/notifier/core/` and `tests/notifier/channels/telegram/`, add `test_catalog.py` and generic-config tests, extend `test_cli_notifier.py` for the selection branches, and add `tests/notifier/channels/test_stub_channel.py` that registers a fake in-test channel descriptor and asserts it is selectable/deployable through the catalog and CLI **without importing or editing the core or Telegram** — the executable form of US3 / SC-002.

**Rationale**: Proves the extensibility contract continuously without shipping a second real channel (Slack stays out of scope), and exercises both `type`-dispatch branches (Constitution II).

**Alternatives considered**: Ship a minimal real second channel (e.g. ntfy) as the test vehicle — rejected: expands scope beyond this feature; a stub proves the seam with less surface.

## R9 — agentsh approval integration (verified against source, 2026-06-01)

**Decision**: The notifier consumes agentsh's real approval REST API rather than the 007-invented `/v1/approve` schema. The core acts as an **approver client**: poll `GET /api/v1/approvals` (authoritative pending list, carries the resolvable `id`), deliver each via the channel, resolve with `POST /api/v1/approvals/{id}` `{decision: approve|deny, reason}` using an approver-role `X-API-Key`. agentsh's notification webhook (if pointed at us) is an **untrusted "poll now" trigger only** — it is an unsigned, generic `[]types.Event` audit stream with no resolvable approval id. The `/v1/approve` push endpoint is removed; `/v1/health` is retained. Full contract + verified Go structs in contracts/agentsh-integration.md.

**Rationale**: Verified against `canyonroad/agentsh` source — `internal/approvals/manager.go` (`Request` struct), `internal/api/app.go` (routes + `requireRoles("approver","admin")`), `config.yml` (`approvals.mode=api`, `notification.webhook`), `internal/store/webhook/webhook.go` + `pkg/types/events.go` (the webhook posts unsigned `[]Event`). agentsh has **no watch/long-poll** endpoint, so polling is the only pull mechanism; the webhook merely lowers latency. Building our own protocol (007's mistake) guaranteed divergence from the actual emitter.

**Alternatives considered**:
- Keep the 007 blocking push-webhook server and have agentsh POST to it — rejected: agentsh's `api` mode is poll/resolve (approvers call agentsh), and its outbound webhook is unsigned generic events, not a resolvable approval; trusting it would be insecure and lossy.
- Pure polling with no webhook — viable and is the fallback; the webhook is an optional optimization layered on top.

**Open items to confirm against a live agentsh before GA** (do not block the design): exact webhook event `type` for "approval required"; whether `Event.fields` ever carries the approval `id` (would let us skip the correlating GET); future webhook HMAC signing. Tracked in the contract doc.
