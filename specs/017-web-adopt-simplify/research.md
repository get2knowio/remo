# Phase 0 Research: Simplify Web Adoption & Close the Lifecycle

All Technical Context unknowns were resolvable from the existing codebase (specs 011/012/015/016 already landed). No external research was required; the decisions below record how each requirement maps onto existing structure.

## R1 — Collapse `_adopt_flow` / `_push_flow` into one path (FR-001..FR-003, US1)

**Decision**: Delete `_adopt_flow` and make `_push_flow` the single orchestrator, renaming the public entry to `run_push`. The flow already reads `GET /setup/status` first; "adopt vs. re-sync" is a natural consequence of the delta cache being empty for the deployment on first contact (every instance takes the full keyscan/authorize path) versus populated on later runs. No `state`-based branching is needed — the cache-miss *is* first-push behavior.

**Rationale**: The two functions differ only in that `_push_flow` consults the delta cache and `_adopt_flow` seeds it at the end. `_push_flow` already seeds the cache on success too (`_update_push_cache`), so `_adopt_flow` is pure duplication. One path guarantees identical URL/code resolution, identical hard-failure guards, and identical summary rendering (FR-005/FR-006/FR-007).

**Alternatives considered**: Keep both and share helpers — rejected: the request is explicitly "one code path", and divergence risk (e.g. the payload-version gate) is exactly what caused the two flows to drift.

**`remo web adopt` fate (Clarifications Q1)**: `cli/web.py`'s `adopt` command body becomes a thin wrapper that prints a one-line deprecation notice (via `print_warning`) and calls the same `run_push`. Marked for removal one release later. Documented as deprecated in `--help`.

## R2 — Best-effort revocation on removed instances (FR-015..FR-018, US3)

**Decision**: Replace the current "print a manual-revocation warning" block (`web_adopt.py` lines ~1332-1338) with an actual attempt. Add `build_revoke_command(marker)` — a POSIX-sh command symmetric to `build_authorize_command`: `grep -vF ' remo-web@'` the file through a temp file + `mv`, tolerating a missing file, 0600. Add `revoke_service_key(host) -> (ok, detail)` mirroring `authorize_service_key` (ambient SSH access, `BatchMode=yes`, never raises). The push computes `removed = cached_names - current_names`, then for each removed **direct-access** entry it can construct a `KnownHost` for, attempts revocation and records a `RevocationOutcome`.

**Rationale**: Reuses the exact marker (`AUTHORIZED_KEYS_MARKER`), transport (`build_ssh_base_cmd`, ambient identity), and atomic-edit idioms already proven for authorization — so revocation is marker-scoped, idempotent, and leaves other keys intact (FR-016) for free.

**Key subtlety**: a *removed* instance is, by definition, no longer in the registry — so its host/user fields are gone from `get_known_hosts()`. The push cache stores only `{fingerprint, host_keys}` per name, not connection fields. **Resolution**: extend the cached instance to also retain the minimal connection tuple (host, user, access, port/type) needed to reconstruct an SSH target for revocation. This is non-secret (host keys are already cached) and gated behind the `cache_version` bump (R6). When the connection tuple is absent (older cache) or the instance is SSM/unreachable, revocation is reported as could-not-be-performed with remediation (FR-017), never fatal.

**Alternatives considered**: Ask the service to revoke — rejected: the service authenticates *to* instances with its own identity and has no operator-level write to `authorized_keys`; revocation must use the operator's ambient access, workstation-side, exactly like authorization.

## R3 — `--force` full re-authorization (FR-019..FR-021, US4)

**Decision**: Add `force: bool = False` to `run_push`/`_push_flow` and a `--force` Click flag. In the per-instance loop, the "fingerprint matches cache → `unchanged`" branch is guarded by `not force`. With `force`, every direct-access instance takes the `_process_instance` full path (keyscan + authorize) regardless of fingerprint. Per-instance failure classification is unchanged (FR-021).

**Rationale**: Minimal, surgical — one boolean threaded to one branch. Covers the out-of-band-rebuild case the registry-field fingerprint structurally cannot see (same entry, new host keys, wiped `authorized_keys`).

## R4 — Multi-workstation flap detection (FR-022..FR-027, US5)

**Decision**: The service maintains a **mirror-meta** file in the writable `web-identity/` state dir: `{ "generation": <int>, "last_push": { "at": <iso8601>, "workstation": <label> } }`. `PUT /setup/registry` increments `generation` and records `last_push` inside `_apply_payload` (atomic temp-file + `os.replace`, same as the known_hosts write). `GET /setup/status` returns `mirror_generation` and `last_push` (additive fields). `PUT` response returns the new `mirror_generation`.

The workstation records, per deployment, the `mirror_generation` it last wrote, in the push cache (R6). On push: after reading status, if `server.mirror_generation` is present and **greater than** the cache's recorded generation for this deployment (someone else advanced it), a flap is detected → warn naming `last_push.at`/`.workstation`. Interactive: prompt confirm/abort (FR-026). Non-interactive (`--yes`): warn and proceed (Clarifications Q2). First-ever push (no server generation / no cache entry) → no warning (FR-025). After a successful PUT, store the returned generation.

**Workstation label**: `socket.gethostname()` plus the local username — best-effort, non-authoritative, informational only. The `last_push` block exposes no secret and no instance content (FR-027) and is served only over the pairing-gated surface.

**Rationale**: A monotonic counter is the minimal reliable signal that "the mirror moved under me"; the descriptor is human context for the warning. Storing the last-written generation workstation-side (not a per-instance value) keeps the comparison O(1) and offline-free of any extra call.

**Alternatives considered**: ETag/If-Match on PUT — rejected: heavier, and the requirement is a *warning*, not optimistic-concurrency rejection; the counter-in-status approach gives the same detection with additive, backward-compatible fields (a pre-017 service simply omits them → no warning, which is the safe default).

## R5 — Mode-detection fix (FR-028..FR-030, US6)

**Decision**: Two-part, in `web/state.py::detect_state`:

1. **Explicit override** — new `REMO_WEB_MODE` env var read via `WebSettings` (values: `adopted` / `mount_configured`; unset = heuristic). When set to a valid mode it wins deterministically (subject only to the `broken` guards, which always take precedence over any healthy mode). Documented.
2. **Narrowed heuristic** — remove `_user_identity_present()` from the `mount_configured` trigger. The authoritative `mount_configured` signal becomes **non-writable `REMO_HOME`** (the `:ro` bind mount, `_home_writable()` == False). When `REMO_HOME` is writable and a service keypair exists → `adopted`, even if a personal `~/.ssh/id_*` is readable.

**Rationale**: The bug is that "a readable personal key" was used as a proxy for "operator mounted an identity", but every workstation has personal keys, so bare-metal serve was misclassified. The non-writable-mount signal is the real, unambiguous marker of the Docker read-only-mount deployment (FR-029), and it is untouched. The explicit override covers any residual ambiguity deterministically (FR-030).

**Docker story preserved**: The read-only-mount deployment mounts `REMO_HOME` `:ro`, so `_home_writable()` stays False and it still classifies `mount_configured` — proven by the existing `test_state.py` non-writable cases, which continue to pass unchanged.

**Alternatives considered**: Detect a *service* keypair vs. a *user* keypair by path — already done; the fix is simply to stop treating a user key as a mount signal. Keeping `_user_identity_present()` but only when `REMO_HOME` is also non-writable — redundant, since non-writable alone already decides it.

## R6 — Offline drift (`remo web status`) + shared nudge (FR-009..FR-014, US2)

**Decision**: New stdlib-only module `core/web_drift.py`:

- `diff_registry_against_cache(hosts, cached_instances) -> DriftReport` — pure function classifying each name as `new` / `changed` (fingerprint differs) / `removed` / `in_sync`. Reuses `instance_fingerprint()` from `web_adopt.py`.
- `select_deployment(cache, selector) -> deployment_id` — implicit when the cache has exactly one deployment; requires an explicit `deployment_id`/URL selector otherwise, erroring with the list of known deployments (Clarifications Q4).
- `render_drift(report, deployment_id)` — table output; the reported-against deployment is shown (FR-012).
- `out_of_date_notice() -> str | None` — returns a one-line notice **iff** the push cache file exists and is non-empty; `None` otherwise (FR-014). Callers print it.

`remo web status` (new `cli/web.py` command, `core`-only imports so it works without the `web` extra) loads the cache, selects the deployment, diffs, renders. Zero network/SSH (FR-010). "No prior push" and "in sync / nothing to push" are explicit outcomes (FR-011).

**Nudge call sites (FR-013)**: an explicit helper call at the end of each successful registry-mutating command rather than centralizing in `main.py`'s `result_callback` (which has no reliable before/after view and would misfire on nested provider groups). Sites: `providers/{incus,proxmox,hetzner,aws}` create/destroy command bodies; `added.py` add/remove; and `core/reconcile.py::run_sync` after a successful `apply_plan` (covers all four providers' sync via the shared engine — Spec 016). AWS `stop/start/reboot` do not mutate the registry → no nudge.

**Rationale**: `web_drift.py` gives one implementation for both the command and the nudge's cache-existence check; explicit call sites are unambiguous and cheap. Placing the nudge inside `run_sync` (core) is consistent with `run_sync` already printing success/warn lines via `core.output`.

**Nudge precision (Clarifications, Assumptions)**: the nudge is gated only on cache existence, not on an exact inline diff — a rare false positive after a no-op mutation is acceptable because `remo web status` is the cheap authoritative follow-up.

## R7 — Push-cache format bump (cross-cutting)

**Decision**: Bump `PUSH_CACHE_VERSION` 2 → 3. Per-deployment shape gains `mirror_generation: int` (R4) and each instance entry gains an optional non-secret connection tuple for revocation (R2): `{fingerprint, host_keys, host, user, access, port, type}`. A cache without `cache_version: 3` is treated as empty (existing `load_push_cache` behavior), forcing one full re-verification push after upgrade — same graceful-degradation pattern spec 015 used for the 1→2 bump. `save_push_cache` writes the new fields; the file remains non-secret (no URL, no code) and 0600/atomic.

**Rationale**: Consistent with the established cache-version discipline; additive; a downgrade or missing version simply retries in full.

## Resolved unknowns summary

| Technical Context item | Resolution |
|------------------------|------------|
| Which flow survives the collapse | `run_push` (R1); `_adopt_flow` deleted; `adopt` → deprecated alias |
| Where mirror generation/descriptor is stored (service) | `web-identity/mirror-meta.json`, atomic (R4) |
| How a removed instance is reached for revocation | connection tuple retained in push cache v3 (R2/R7) |
| How bare-metal adopted mode becomes reachable | drop user-key mount signal; non-writable-`REMO_HOME` authoritative; `REMO_WEB_MODE` override (R5) |
| Where the offline diff/nudge lives | new stdlib-only `core/web_drift.py` (R6) |
| Nudge trigger points | provider create/destroy, add/remove, `run_sync` success (R6) |
| Cache compatibility | `cache_version` 2 → 3, older treated as empty (R7) |

No `NEEDS CLARIFICATION` remain.
