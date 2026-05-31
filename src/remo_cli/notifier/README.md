# remo notifier

A long-running HTTP daemon that receives agentsh approval requests, delivers
them to a human via Telegram (long-polling), and returns the human's decision
synchronously — failing secure (deny) on timeout, shutdown, send failure, or
capacity exhaustion. No persistent state.

This component currently ships as part of the remo repo for v1 distribution
simplicity. **Its wire protocol is the durable contract**; future consumers may
include juju's pending-decision-bead pusher, maverick workflow status
announcers, and deacon lifecycle events. See
[`docs/wire-protocol.md`](docs/wire-protocol.md).

## Layout

```
notifier/
├── cli.py            # `remo-notifier serve`
├── server.py         # FastAPI app + lifespan + fail-secure resolver
├── config.py         # Pydantic config + strict TOML loader
├── state.py          # in-memory PendingApprovals registry
├── models.py         # wire-protocol models
├── logging_setup.py  # structlog + secret redaction
├── transports/
│   ├── base.py       # NotificationTransport ABC
│   └── telegram.py   # Telegram (long-polling) implementation
└── docs/
    ├── wire-protocol.md
    └── config-schema.md
```

## Run locally

```bash
uv pip install -e ".[notifier]"
remo-notifier serve --config /path/to/notifier.toml
```

See the repo-root README "Notifier" section for first-time Telegram setup and
deployment via `remo notifier deploy`.
