# Contract: CLI commands `remo web adopt` / `remo web push`

**Feature**: 011-web-adopt. Workstation side; no `web` extra required
(stdlib HTTP only). Wire calls: [setup-api.md](setup-api.md).

## remo web adopt

```text
remo web adopt [URL] [--token TEXT] [--via HOST] [--allow-empty] [--yes] [--save]
```

| Input | Resolution order |
|-------|------------------|
| Service URL | argument → `REMO_API_URL` env → interactive prompt |
| API token | `--token` → `REMO_API_TOKEN` env → interactive prompt (hidden input) |
| `--via HOST` | Optional: open `ssh -N -L <free-port>:127.0.0.1:<service-port> HOST` first and run the flow through `http://127.0.0.1:<free-port>` (FR-018) |
| `--allow-empty` | Required to push an empty registry (FR-016) |
| `--yes` | Non-interactive: accept defaults, skip fingerprint prompts (those instances → `skipped_no_trust`), decline credential saving unless `--save` |

### Flow (observable behavior)

1. `GET /setup/status` → abort with a clear message on `mount_configured`
   (FR-017) or auth failure.
2. `GET /setup/identity` → service public key + deployment id.
3. Build payload from the local registry (mirror; SSM entries included in
   `registry`, excluded from `host_keys` — FR-012).
4. Per direct-access instance (bounded per-instance timeout, failures never
   fatal — FR-013): `ssh-keyscan`, verify against workstation trust
   (`ssh-keygen -F`; interactive fingerprint confirmation when no record —
   clarification Q2; mismatch → `security_flagged`, nothing pushed for that
   instance — FR-010); install/replace the `remo-web@<deployment_id>`
   authorization entry over the user's existing SSH access (FR-011,
   idempotent).
5. `PUT /setup/registry`.
6. `POST /setup/verify` → render per-instance PASS/FAIL, annotating
   "reachable from workstation but not from the service" where the CLI
   succeeded and the service failed (FR-014).
7. Offer to save credentials (explicit consent, FR-025) →
   `~/.config/remo/web-service.json` (0600).

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Flow completed (per-instance skips/flags allowed; summary lists them) |
| 1 | Flow could not complete: auth failure, mount-configured target, empty registry without `--allow-empty`, tunnel failure, payload rejected |

### Output contract

Ends with a per-instance summary table (one line per registry entry:
`adopted` / `skipped_unreachable` / `skipped_by_design` /
`skipped_no_trust` / `security_flagged`) followed by the verification
report. `security_flagged` lines render prominently as potential
MITM warnings. Idempotence (FR-015): a second identical run reports the same
summary with zero changes made.

## remo web push

```text
remo web push [--allow-empty] [--yes]
```

Zero-argument re-sync (US4): loads saved credentials (absent → behaves like
first-time `adopt`, prompting for URL/token); compares saved
`deployment_id` with `GET /setup/identity` and aborts with re-adopt guidance
on mismatch (service identity changed — state volume was reset); then runs
the adopt flow, skipping authorization work for instances whose current
entry already matches (only new/changed instances get keyscan + authorize —
FR-026). Rejected token → exit 1 with re-adopt guidance (FR-027).

## Error-message requirements (Constitution IV)

- Tunnel mode Host-allowlist failure: explain that `REMO_WEB_ALLOWED_HOSTS`
  must include `127.0.0.1` for `--via` (research R9).
- `mount_configured` rejection: state that the deployment is configured via
  read-only mounts and adoption does not apply.
- Empty registry: name `--allow-empty` and the wrong-workstation risk.
- Every skip/flag reason in the summary carries a one-line remediation.
