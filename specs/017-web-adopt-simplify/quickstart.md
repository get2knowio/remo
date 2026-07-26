# Quickstart & Validation: Simplify Web Adoption & Close the Lifecycle

Runnable validation scenarios proving the feature end-to-end. Details live in [data-model.md](./data-model.md) and [contracts/](./contracts/); this guide is the run/verify checklist.

## Prerequisites

```bash
uv sync --all-extras          # CLI + web extra + dev tools
uv run pytest -q              # baseline green before starting
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

A local service for manual runs:

```bash
uv run remo web serve --host 127.0.0.1 --port 8080   # loopback: auto operator-auth "none"
# open http://127.0.0.1:8080 → copy pairing code for the push commands below
```

## Scenario A — Unified push: first push adopts, second re-syncs (US1, SC-001/SC-002)

```bash
# Fresh (never-adopted) deployment, populated local registry:
uv run remo web push http://127.0.0.1:8080 --token <code>
#   expect: identity authorized on reachable direct-access instances,
#           full mirror applied, service-side verification, exit 0.

# Run again with a fresh code against the now-adopted deployment:
uv run remo web push http://127.0.0.1:8080 --token <code2>
#   expect: unchanged instances reported "unchanged"; only new/changed re-processed; exit 0.
```

**Pass**: both invocations succeed through the *same* command; summaries share one format. `git grep -n "_adopt_flow" src/` returns nothing (one code path).

## Scenario B — Deprecated adopt alias still works (US1 / FR-008)

```bash
uv run remo web push --help    # documents the unified behavior
uv run remo web adopt --help   # marked deprecated
uv run remo web adopt http://127.0.0.1:8080 --token <code>
#   expect: a one-line deprecation warning, then identical behavior to `push`.
```

## Scenario C — Offline drift status (US2, SC-003)

```bash
# after a successful push, mutate the registry three ways:
uv run remo incus create newbox ...        # -> new
uv run remo incus destroy oldbox           # -> removed
uv run remo <provider> update changed ...  # -> changed (fingerprint differs)

# fully offline (disconnect network to prove it):
time uv run remo web status
#   expect: exactly 1 new, 1 removed, 1 changed; < 2s; no network/SSH.
```

**Pass**: correct classification, sub-2s, and the reported-against deployment id is shown. With multiple cached deployments and no `--deployment`, exits 1 listing known ids.

## Scenario D — Out-of-date nudge across all five mutating commands (SC-004)

```bash
# with a push cache present, each of these prints the one-line nudge on success:
uv run remo incus create ...      # provider create
uv run remo aws destroy ...       # provider destroy
uv run remo hetzner sync          # run_sync apply path (Spec 016 shared engine)
uv run remo add name host user    # registered SSH host add
uv run remo remove name           # registered SSH host remove

# with NO push cache (rm ~/.config/remo/web-service.json): none of the above print a nudge.
# aws stop/start/reboot and dry-run sync: never print a nudge.
```

## Scenario E — Best-effort revocation on removal (US3, SC-005)

```bash
# adopt covering two reachable instances, then remove one and push:
uv run remo incus destroy box2
uv run remo web push http://127.0.0.1:8080 --token <code>
#   expect: summary reports box2 "revoked"; ssh box2 'grep remo-web@ ~/.ssh/authorized_keys' -> empty,
#           other authorized keys intact.

# repeat with box2 powered off/unreachable:
#   expect: "could_not_revoke" with manual-removal remediation; overall push still exit 0.
```

## Scenario F — `--force` recovers an out-of-band rebuild (US4, SC-006)

```bash
# out-of-band: reset box1 host keys + wipe its authorized_keys, DON'T change its registry entry
uv run remo web push http://... --token <code>            # box1 reported "unchanged" (stale)
uv run remo web push http://... --token <code2> --force   # box1 re-scanned + re-authorized
#   expect: with --force every direct-access instance takes the full keyscan/authorize path.
```

## Scenario G — Multi-workstation flap warning (US5, SC-007)

```bash
# Workstation A:
uv run remo web push http://svc:8080 --token <codeA>      # generation -> N

# Workstation B (never pushed here before), narrower registry:
uv run remo web push http://svc:8080 --token <codeB>
#   interactive: warns "last pushed by hostA/... at <time>", prompts confirm/abort.
#   with --yes: prints the warning and proceeds.

# Same workstation twice in a row: no warning. First-ever push to a fresh svc: no warning.
```

## Scenario H — Bare-metal adopted mode + Docker RO-mount preserved (US6, SC-008)

```bash
# Bare-metal: workstation has ~/.ssh/id_ed25519 AND a writable REMO_HOME.
uv run remo web serve --host 127.0.0.1 --port 8080
curl -s localhost:8080/api/v1/health   # sanity
# GET /setup/status (with a live pairing code) -> state "unconfigured" then "adopted" after a push,
#   NOT "mount_configured".

# Explicit override:
REMO_WEB_MODE=adopted uv run remo web serve ...   # forces adopted deterministically

# Docker RO-mount deployment (REMO_HOME mounted :ro, operator-mounted registry+identity):
#   state MUST still be "mount_configured".
uv run pytest tests/unit/web/test_state.py -q
```

**Pass**: bare-metal-with-personal-key is adoptable; non-writable-`REMO_HOME` still classifies `mount_configured`; explicit `REMO_WEB_MODE` wins deterministically (subject to `broken` guards).

## Full gate

```bash
uv run pytest -q
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
cd frontend && npm run test        # unchanged; ensure no regressions (no frontend changes expected)
```

**Docs check (FR-031 / Constitution V)**: `docs/web-session-interface.md` has a single consolidated adoption section (no separate adopt vs. push sections) describing push, status, nudge, revocation, `--force`, flap detection, and `REMO_WEB_MODE`.
