# remo notifier

A long-running HTTP daemon that acts as an **approver client** to the host's
[agentsh](https://github.com/canyonroad/agentsh): it polls agentsh's approval
API for pending requests, delivers each to a human through a **channel**
(Telegram first), and resolves the human's decision back to agentsh — failing
secure (deny) on timeout, shutdown, send failure, or capacity exhaustion. No
persistent state.

The decision always flows **human → channel → notifier → agentsh**; the human
never calls agentsh directly. The agentsh integration contract (verified against
agentsh source) is in
[`../../../specs/008-notifier-channels/contracts/agentsh-integration.md`](../../../specs/008-notifier-channels/contracts/agentsh-integration.md).

## Core vs channels

The service splits into a channel-agnostic **core** and per-channel packages:

- **core** — `server.py` (agentsh poll loop + fail-secure resolver), `state.py`
  (in-memory `PendingApprovals`), `grants.py` (standing grants), `models.py`
  (agentsh `Request` + internal decision), `config.py`, `logging_setup.py`,
  `agentsh_client.py` (the httpx approver client), and `transports/base.py` (the
  `NotificationTransport` ABC). The core imports **no** channel.
- **channels** — `channels/base.py` (the import-light `ChannelDescriptor`),
  `channels/catalog.py` (the registry the laptop CLI reads), and one package per
  channel (`channels/telegram/` = `transport.py` + `config.py` + `descriptor.py`).

Adding a channel is a self-contained drop-in that touches no core file — see
[`../../../specs/008-notifier-channels/contracts/channel-extension.md`](../../../specs/008-notifier-channels/contracts/channel-extension.md).

## Layout

```
notifier/
├── cli.py              # `remo-notifier serve` (resolves the channel via the catalog)
├── server.py           # FastAPI app: agentsh poll→deliver→resolve loop + fail-secure
├── agentsh_client.py   # httpx approver client: poll GET, POST decision (approver X-API-Key)
├── config.py           # generic [transport] {type} + [agentsh]; strict TOML loader
├── state.py            # in-memory PendingApprovals registry
├── models.py           # agentsh Request (consumed) + internal decision/health models
├── grants.py           # standing "Always" grants (match on kind/target/session)
├── logging_setup.py    # structlog + secret redaction
├── transports/
│   └── base.py         # NotificationTransport ABC (delivers an agentsh Request)
└── channels/
    ├── base.py         # ChannelDescriptor + RequiredEnv (import-light)
    ├── catalog.py      # CHANNELS = [telegram, …] + list_channels()/get()
    └── telegram/       # transport.py (build() factory) · config.py · descriptor.py
```

## Run locally

```bash
uv pip install -e ".[notifier-telegram]"   # or .[notifier] (alias)
remo-notifier serve --config /path/to/notifier.toml
```

See the repo-root README "Notifier" section for channel selection, the
`REMO_NOTIFIER_<CHANNEL>_*` credential convention, the agentsh approver
connection, and deployment via `remo notifier deploy`.
