# Contract: Adding a Channel (the extensibility guarantee)

The complete, exhaustive set of changes to add a new channel (e.g. Slack). Anything beyond this list is a design violation. This is the executable subject of US3 / SC-002.

## The four permitted changes

1. **New channel package** `src/remo_cli/notifier/channels/<id>/`:
   - `transport.py` — a `NotificationTransport` subclass (implements `start`, `stop`, `send_approval_request`, `cancel`, optional `healthy`) plus a `build(config) -> NotificationTransport` factory.
   - `config.py` — a strict Pydantic model (`extra="forbid"`) for that channel's `[transport.<id>]` settings.
   - `descriptor.py` — the `ChannelDescriptor` (see channel-descriptor.md).
2. **Catalog registration** — append the descriptor to `CHANNELS` in `channels/catalog.py` (one line).
3. **Dependency extra** — add `notifier-<id> = ["remo-cli[notifier-core]", "<channel sdk>"]` in `pyproject.toml`.
4. **Image** — produced by the existing parameterized Dockerfile via `--build-arg CHANNEL=<id>` (no new Dockerfile needed unless the channel requires extra system packages, in which case an optional `notifier/<id>.Dockerfile` override may be added).

## What MUST NOT change (the invariant — SC-002)

Adding a channel MUST NOT edit any of:
- Core service modules: `server.py`, `state.py`, `models.py`, `grants.py`, `logging_setup.py`, `config.py`.
- The transport ABC: `transports/base.py`.
- Any **other** channel package under `channels/`.
- The HTTP wire protocol or its schemas.
- The Ansible role logic (the role is channel-agnostic; channel TOML comes from the descriptor).

A test (`tests/notifier/channels/test_stub_channel.py`) registers a fake channel and asserts it is selectable and deployable through the catalog + CLI while importing none of the core/Telegram modules.

## Automatic consequences of registration

Once the descriptor is in `CHANNELS`, with no further work:
- `remo notifier channels` lists it with its credential requirements (FR-006a).
- `remo notifier deploy` offers it in the picker and accepts it by name (FR-009).
- The deploy preflight checks its `required_env` (FR-012).
- The role builds/runs `remo-notifier-<id>:<version>` on the single service/bind/port (FR-013/FR-014).
- The core's fail-secure guarantees apply unchanged (FR-007/FR-008): the channel can only fail to deliver.
