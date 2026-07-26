# Contract: `remo web push` (unified) + deprecated `remo web adopt` alias

Supersedes the separate adopt/push CLI contracts from spec 011/012. One command, one code path (`core/web_adopt.run_push`). Supports FR-001..FR-008, FR-015..FR-021.

## `remo web push [URL] [OPTIONS]`

Adopts a not-yet-adopted deployment on first use and re-syncs an already-adopted one on subsequent use — auto-detected; the operator never chooses.

| Arg / option | Meaning |
|--------------|---------|
| `URL` (arg, optional) | Service URL. Resolution: arg → `$REMO_API_URL` → interactive prompt. |
| `--token TEXT` | Pairing code. Resolution: option → `$REMO_API_TOKEN` → hidden prompt. Never persisted. |
| `--via HOST` | SSH local-forward tunnel (unchanged; requires `127.0.0.1` in `REMO_WEB_ALLOWED_HOSTS`). |
| `--allow-empty` | Permit pushing an empty registry (wipes the deployment's list). Unchanged guard. |
| `--yes` | Non-interactive: skip fingerprint prompts; **on flap detection, warn and proceed** (Clarifications Q2). |
| `--force` | **New.** Bypass the fingerprint "unchanged" fast-path: re-scan host keys and re-authorize the service key on every direct-access instance (FR-019/FR-020). |

### Behavior

1. `GET /setup/status` → mount-configured refusal (409/state) and payload-version skew abort **before any mutation** (unchanged, FR-006).
2. **Flap check** (FR-024): compare `status.mirror_generation` to the cached generation for this deployment; warn/confirm/proceed per [setup-status-marker.md](./setup-status-marker.md).
3. `GET /setup/identity` → deployment id + public key.
4. Per-instance loop over the local registry:
   - direct-access + fingerprint matches cache + `not --force` → `unchanged` (keyscan/authorize skipped; cached host-key lines reused).
   - otherwise → full `_process_instance` (keyscan + trust verify + authorize). Per-instance failures never fatal (FR-007/FR-021).
5. Compute `removed = cached_names − current_names`; **best-effort revocation** on each removed direct-access instance ([revocation.md](./revocation.md), FR-015).
6. `PUT /setup/registry` (full mirror; includes optional `workstation` label) → applies mirror, bumps generation, returns new `mirror_generation`.
7. Update push cache v3: per-instance entries for `adopted`/`unchanged` instances (with connection tuple), and the returned `mirror_generation`. Removed instances drop out.
8. `POST /setup/verify` → service-side verification (unchanged).
9. Render summary: per-instance adoption outcomes **plus** per-removed-instance revocation outcomes (FR-018), then verification.

### Exit codes

- `0` — flow completed (per-instance skips/flags/revocation-failures are reported, not fatal).
- `1` — hard failure: dormant surface, mount-configured, empty registry without `--allow-empty`, payload-version skew, tunnel failure, payload rejected, or **flap abort declined interactively**.

## `remo web adopt` (deprecated alias)

`remo web adopt [URL] [OPTIONS]` accepts the same options and **delegates to `run_push`** after printing a one-line deprecation notice (`print_warning`, e.g. "`remo web adopt` is deprecated; use `remo web push` — first push adopts automatically"). Scheduled for removal one release later (FR-008). `--help` marks it deprecated. No separate code path.

## Preserved trust-model invariants (FR-004)

Unchanged: service-scoped identity only; host keys included only when workstation-verified; SSM instances never receive host keys or key authorization; the single `remo-web@<deployment>` marker is the only service line touched; the setup surface stays pairing-gated; personal keys are never copied anywhere.
