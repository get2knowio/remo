# Quickstart Validation Record: CLI-to-Web Adoption

**Feature**: 011-web-adopt | **Date**: 2026-07-17 | **Build**: branch `011-web-adopt`
(T046). Scenarios from [quickstart.md](quickstart.md); no real remo hosts exist in
the CI/devcontainer environment, so per-instance SSH work is validated through the
automated equivalents quickstart.md itself defines (canned scan/authorize in the
live-service E2E, real `sh` execution of the authorize command locally, real
`ssh-keygen -H`/`-F` for trust verification).

| Scenario | Outcome | Evidence |
|----------|---------|----------|
| A. Unconfigured boot (US2/SC-006) | PASS | Real container, empty volume + token: `tests/image/test_docker_image.py::test_amd64_unconfigured_boot_reports_unconfigured_and_generates_identity` (ready `unconfigured` well under 30 s, keypair + `state.json` in volume) and `::test_amd64_service_identity_survives_container_restart` (same fingerprint after restart, FR-002) |
| B. Token gating (US3/SC-004) | PASS | Real container: `::test_amd64_setup_status_token_gated`, `::test_amd64_setup_routes_hidden_without_token_env` (404); exhaustive 78-test matrix in `tests/unit/web/test_setup_auth.py` (401 variants, 404 indistinguishability, constant-time comparison, credential-free logs) |
| C. First-time adoption (US1/SC-001, SC-002) | PASS | Live-service E2E `tests/integration/test_web_adopt_e2e.py::test_full_adopt_then_idempotent_rerun`: one `run_adopt()` over real HTTP → mixed outcomes (`adopted`/`skipped_by_design`/`skipped_unreachable`), byte-exact service registry + known_hosts, verify report, ready flips to configured; exactly-one `remo-web@` line + rotation proven by real `sh` execution in `test_web_adopt_authorize.py`; FR-007 no-private-key-material assertion in `test_web_adopt_payload.py` |
| D. Idempotence (FR-015/SC-003) | PASS | Same E2E test, second run: identical outcome summary, service-side files byte-identical |
| E. Ongoing push (US4/SC-007) | PASS | `::test_push_after_adopt_processes_only_the_new_instance` (only the new instance keyscanned/authorized; mirror + known_hosts grow correctly) and `::test_push_with_rotated_token_fails_with_reauth_guidance` (exit-1 class error, FR-027) |
| F. Mirror semantics + guards (Q1/FR-016/FR-017) | PASS | Removal → manual-revoke note + dropped from mirror/cache (`test_web_push.py`); empty registry refused without `--allow-empty` (unit + CLI tests); mount-configured target → 409 server-side (`test_setup_api.py`) and clear exit-1 abort CLI-side (`test_web_adopt_cmd.py`) |
| G. RO bind-mount regression (FR-005/SC-005) | PASS | Existing RO-mount image tests unmodified and green on this build (`ready` with full mounts, hard gate + 503 on missing mounts, hardening flags); `docker/compose.example.yml` diff is addition-only (adopted variant behind `--profile adopted`); mounted-mode SSH argv pinned byte-identical in `test_ssh_identity_opts.py` |
| H. Tunnel fallback (FR-018) | PASS (unit-level) | `open_via_tunnel` free-port probe, `ExitOnForwardFailure=yes`, readiness wait, teardown and the `REMO_WEB_ALLOWED_HOSTS`-naming error verified with mocked/local ssh in unit tests + orchestration smoke; no second docker host exists in this environment for a live `--via` hop — recommend a one-time manual check on the homelab |

**Suite totals on this build**: unit + integration `1155 passed, 3 skipped`
(pre-existing opt-in/env skips); image suite (`REMO_RUN_IMAGE_TESTS=1`)
`29 passed` including emulated arm64 build; `mypy src/remo_cli` clean;
`ruff check src/remo_cli` clean; frontend `npm run lint` + `npm run build` clean.

**SC-008 revocation target**: the authorized entry is a single
`remo-web@<deployment-id>`-tagged line; deletion-as-revocation and
rotation-replacement proven by real shell execution in
`tests/unit/core/test_web_adopt_authorize.py`.
