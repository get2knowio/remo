# remo-notifier-source (devcontainer Feature)

Opt-in devcontainer Feature that registers this container's [agentsh](https://github.com/canyonroad/agentsh)
with the **host's remo notifier** so approval prompts are delivered to your phone
(or whichever channel the host's notifier runs). A project that does not add this
Feature is never connected and is unaffected.

The Feature opens a **presence connection** (`POST /v1/sources`, held open) to the
notifier on container start and keeps it up, reconnecting with backoff across
notifier restarts. The open connection *is* the registration; when the container
stops the connection drops and the notifier removes the source. See
`specs/009-notifier-source-registration/contracts/devcontainer-feature.md`.

## Usage

```jsonc
{
  "features": {
    "./features/remo-notifier-source": {
      "notifierAddress": "172.17.0.1:18181",
      "agentshApiUrl": "http://proj-a:8080",
      "apiKeyFile": "/run/secrets/agentsh_approver_key",
      "labels": "project=proj-a,owner=paul"
    }
  }
}
```

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `notifierAddress` | `172.17.0.1:18181` | `host:port` of the notifier control plane on the bridge. |
| `agentshApiUrl` | (required) | **Notifier-reachable** agentsh approvals base URL. |
| `apiKey` | `""` | Approver `X-API-Key`, inline. Prefer `apiKeyFile`. |
| `apiKeyFile` | `""` | Path read at connect time so the secret stays out of `devcontainer.json`. |
| `sourceId` | container hostname | Stable id, 1:1 with this devcontainer. |
| `labels` | `""` | Comma-separated `key=value` labels for the status surface. |

The approver key is sent **inline** in the registration payload over the trusted
bridge (the clarified key-conveyance decision); it is held in the notifier's
memory only — never logged, never persisted.

## Deployment prerequisite — shared network path

The notifier must be able to reach this container's `agentshApiUrl`, **and** this
container must be able to reach `notifierAddress` on the bridge. That requires a
shared network path — a user-defined Docker network or published ports. **The
Feature does not create the network**; set it up in your compose/devcontainer
configuration.

## Behavior

- **Start** — opens the presence connection; the notifier begins polling this
  agentsh within one poll interval.
- **Notifier restart / drop** — the connector retries with full-jitter
  exponential backoff (base 1s, factor 2, cap 30s) and re-registers automatically.
- **At capacity** — a `503 at_capacity` is treated as a retryable condition; the
  connector keeps trying and claims a slot when one frees.
- **Container stop** — the connection drops and the notifier removes the source.

Set `REMO_SOURCE_DRY_RUN=1` and run the connector to print the registration JSON
and POST target without connecting (debugging only).
