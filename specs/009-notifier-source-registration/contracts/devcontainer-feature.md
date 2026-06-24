# Contract: `remo-notifier-source` devcontainer Feature

The opt-in, reusable devcontainer Feature that opens and maintains a source's
**presence connection** to the host notifier from inside a participating
devcontainer (FR-016/FR-017). A project that does not add the Feature is never
connected and is unaffected (US3#4). agentsh is **not** modified.

Ships in-repo at `features/remo-notifier-source/` (publication to a public Feature
registry is Out of Scope / later).

---

## `devcontainer-feature.json`

```jsonc
{
  "id": "remo-notifier-source",
  "version": "1.0.0",
  "name": "Remo Notifier Source",
  "description": "Register this devcontainer's agentsh with the host's remo notifier (opt-in approval delivery).",
  "options": {
    "notifierAddress": {
      "type": "string",
      "default": "172.17.0.1:18181",
      "description": "host:port of the notifier control plane on the container bridge."
    },
    "agentshApiUrl": {
      "type": "string",
      "default": "",
      "description": "Notifier-reachable agentsh approvals base URL (e.g. http://<this-container>:8080)."
    },
    "apiKey": {
      "type": "string", "default": "",
      "description": "Approver X-API-Key (inline). Prefer apiKeyFile to keep secrets out of devcontainer.json."
    },
    "apiKeyFile": {
      "type": "string", "default": "",
      "description": "Path inside the container to read the approver key from at connect time."
    },
    "sourceId": {
      "type": "string", "default": "",
      "description": "Stable source id (defaults to the container hostname)."
    },
    "labels": {
      "type": "string", "default": "",
      "description": "Optional comma-separated key=value labels for the status surface."
    }
  },
  "entrypoint": "/usr/local/share/remo-notifier-source/remo-source-connect.sh",
  "installsAfter": ["ghcr.io/devcontainers/features/common-utils"]
}
```

The `entrypoint` launches the connector in the background for the container's
lifetime (the standard Feature mechanism for a long-running side process).

---

## `install.sh`

Idempotent (Constitution III). Installs `curl` if absent, copies
`remo-source-connect.sh` to `/usr/local/share/remo-notifier-source/`, makes it
executable, and renders the resolved options into an env file the entrypoint
sources. Re-running produces identical state.

---

## `remo-source-connect.sh` (the connector)

POSIX `sh` + `curl`. Behavior:

1. **Preflight** (fail-fast, Constitution IV): require `notifierAddress`,
   `agentshApiUrl`, and a key source (`apiKey` or readable `apiKeyFile`). On a
   missing option, print exactly what is missing and exit non-zero.
2. **Resolve** `source_id` (option or `hostname`); read the inline `api_key` from
   `apiKeyFile` at connect time if provided; parse `labels`.
3. **Connect loop** — forever:
   - Build the `SourceRegistration` JSON and `POST /v1/sources` with
     `curl --no-buffer -sS -X POST -H 'content-type: application/json' -d @-`,
     holding the streamed keepalive response open. While the stream is open the
     source is registered and polled.
   - On any exit of the `curl` (connection dropped, notifier restart, `503
     at_capacity`, network error): **reconnect with full-jitter exponential
     backoff** — base `1 s`, factor `2`, cap `30 s` — so a notifier restart
     self-heals and a saturated notifier is not hammered (FR-012, reconnect-storm
     edge case). The loop never terminates on its own; it ends only when the
     container stops (which drops the connection and de-registers the source —
     US3#3).

The connector holds **no** application heartbeat and sends **no** periodic
re-register; it only re-opens a dropped connection (the notifier's keepalive ticks
and idle timeout are the liveness mechanism, per `source-registration.md`).

---

## Behavioral contract (maps to acceptance scenarios)

| Scenario | Behavior |
|----------|----------|
| Container with the Feature starts (US3#1) | Connector opens the presence connection; notifier begins polling this agentsh within one poll interval. |
| Connection drops, e.g. notifier restart (US3#2) | Connector keeps retrying with backoff and re-establishes registration with no manual action. |
| Container stops (US3#3) | Connection ends (instant on graceful stop; ≤ idle timeout otherwise); notifier removes the source. |
| Project omits the Feature (US3#4) | No connection is opened; the notifier is unaffected. |
| Notifier at capacity | `503 at_capacity` ⇒ connector backs off and keeps retrying; claims a slot if one frees. |

## Deployment prerequisite (surfaced in the Feature README)

The notifier must be able to reach this container's `agentshApiUrl`, and this
container must be able to reach `notifierAddress` on the bridge. This requires a
shared network path — a user-defined Docker network or published ports
(spec Assumptions). The Feature documents this; it does not create the network.
