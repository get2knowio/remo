# Contract: registry-admin API (023)

`/api/v1/registry/*`, gated by `REMO_WEB_REGISTRY_ADMIN=enabled` — a NEW flag
(not HOST_ADMIN reuse; bigger blast radius). Dormancy is the `/setup`
pattern: flag off, or operator-auth refused → 404 byte-identical to an
unknown route. All mutators additionally answer `409 read_only_deployment`
on a mount-configured deployment. Errors use the shared `{"error": {...}}`
envelope. All work runs through the **embedded CLI** (`_run_cli` seam) or the
detached job runner (`web/jobs.py`) — never a reimplementation.

| Route | Semantics |
|---|---|
| `POST /registry/hosts` | `{name, target, user?, port?}` (no identity field — the service always authenticates with its own key) → `remo add … --yes` (no `--verify`; rc 1 stays unambiguous). 201 `{instance_id, name, host, user, port, public_key, authorize_command}`. rc 2 → 400 `invalid_target`; rc 1 → 409 `name_conflict`; else → 502 `cli_failure`. Bumps the marker (`origin: web`), background targeted discovery refresh. First add from `unconfigured` mints the service identity (flips to adopted). |
| `DELETE /registry/hosts/{id}` | ssh-type only (`409 provider_managed` names `remo <type> destroy`). `remo remove … --yes`; rc 1 (lost race) is idempotent success. Trust-file cleanup via `ssh-keygen -R`, marker bump, background FULL refresh (pruning is full-refresh-only). 200 `{name, removed: true}`. |
| `POST …/scan-key` | ssh-keyscan + classify against the service trust file. 200 `{status: trusted\|mismatch\|no_trust\|unreachable, detail, fingerprints[], lines[]}` (host keys are public — showing them is the point). |
| `POST …/trust-key` | `{lines}` — the client echoes exactly what the operator confirmed (no blind re-scan window). Server re-validates structure AND that each hosts field equals this instance's lookup key (the route can never trust an arbitrary host), then replaces that instance's slice of the trust file. |
| `POST …/verify` | `ssh -o BatchMode=yes … true` with the service key. `{status: ok\|auth_failed\|host_key_untrusted\|unreachable, detail}` (rc 255 stderr mapping). Works before remo-host exists; `ok` triggers a targeted refresh. |
| `GET …/authorize-command` | The deploy-key one-liner again (come-back-later screens). |
| `POST …/configure` | Pre-flights: ssh-type, non-root user, no unresolvable workstation identity (`409 workstation_identity`), tool names `[a-z0-9_-]`. Detached job `remo configure NAME --yes -v` (verbose: the filtered renderer's `\r` control chars garbage a log tail). 202 wire-compatible with host_admin's `JobAcceptedResponse` (`project: ""`); duplicate running job → `409 job_already_running` carrying the existing id. |
| `GET /registry/jobs/{job_id}` | Wire-identical to host_admin's `JobStatusResponse`; unknown → 404 `unknown_job`. Registry jobs live on the SERVICE (no instance in the path). |
| `GET …/jobs` | This instance's jobs, newest-first (re-attach after reload/restart). |

Instance resolution is a FRESH registry read matched by `derive_instance_id`
— deliberately not the discovery cache (a just-added host isn't discovered
yet).
