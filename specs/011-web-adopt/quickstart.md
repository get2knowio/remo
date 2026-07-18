# Quickstart: CLI-to-Web Adoption — Validation Guide

**Feature**: 011-web-adopt. Runnable scenarios proving the feature end-to-end.
Contracts: [contracts/setup-api.md](contracts/setup-api.md),
[contracts/cli-web-adopt.md](contracts/cli-web-adopt.md).

## Prerequisites

- Workstation with this branch installed: `uv sync --all-extras`
- At least one bootstrapped, reachable remo instance registered locally
  (`uv run remo incus sync ...` or equivalent; `uv run remo shell <name>`
  works)
- Docker (for the container scenarios); `jq` for readable JSON

## A. Unconfigured boot (US2 / SC-006)

```bash
docker volume create remo-web-state
docker run -d --name remo-web-adopt-test \
  -e REMO_WEB_API_TOKEN=test-token-123 \
  -e "REMO_WEB_BIND_HOST=0.0.0.0" \
  -p 127.0.0.1:8080:8080 \
  -v remo-web-state:/home/remo/.config/remo \
  --read-only --tmpfs /run/remo-ssh \
  ghcr.io/get2knowio/remo-web:<this-release>

# Within 30s:
curl -s http://127.0.0.1:8080/api/v1/ready | jq .status   # "unconfigured"
docker exec remo-web-adopt-test ls /home/remo/.config/remo/web-identity/
# id_ed25519  id_ed25519.pub  known_hosts?  state.json

docker restart remo-web-adopt-test
# same key fingerprint after restart (FR-002):
docker exec remo-web-adopt-test ssh-keygen -lf /home/remo/.config/remo/web-identity/id_ed25519.pub
```

Browser at `http://127.0.0.1:8080` shows the "awaiting adoption" page (no
instances, no terminals).

## B. Token gating (US3 / SC-004)

```bash
# Wrong token -> 401; missing header -> 401
curl -s -o /dev/null -w '%{http_code}\n' -H 'Authorization: Bearer nope' \
  http://127.0.0.1:8080/api/v1/setup/status        # 401

# Redeploy WITHOUT REMO_WEB_API_TOKEN -> setup surface hidden
curl -s -o /dev/null -w '%{http_code}\n' \
  http://127.0.0.1:8080/api/v1/setup/status        # 404
# Dashboard/health unaffected in both cases.
```

## C. First-time adoption (US1 / SC-001, SC-002)

```bash
export REMO_API_URL=http://127.0.0.1:8080
export REMO_API_TOKEN=test-token-123
uv run remo web adopt
```

Expected: per-instance progress; interactive fingerprint prompt only for
instances never verified from this workstation; final summary with one line
per registry entry; verification report PASS for every reachable
direct-access instance; SSM entries listed as `skipped_by_design`; offer to
save credentials. Then:

- Browser now shows the dashboard with all instances; opening a terminal
  works (the session rides the service identity, not your personal key).
- On an instance: `grep remo-web@ ~/.ssh/authorized_keys` → exactly one
  line (SC-008 revocation target).
- Your personal private key never left the workstation (nothing was uploaded
  besides registry metadata + host keys — verify with the service logs or
  the payload contract).

## D. Idempotence (FR-015 / SC-003)

```bash
uv run remo web adopt   # immediately again
```

Expected: identical summary, exit 0, and on any instance still exactly one
`remo-web@` line; registry/host-keys byte-identical on the service.

## E. Ongoing push (US4 / SC-007)

```bash
uv run remo incus sync <incus-host>       # registers a new instance
uv run remo web push                      # zero-argument
```

Expected: only the new instance is keyscanned/authorized; it appears on the
dashboard with a working terminal in < 60 s. Rotate the service token and
re-run → exit 1 with a clear re-auth message (FR-027).

## F. Mirror semantics + guards (clarification Q1 / FR-016 / FR-017)

```bash
# Remove an instance locally, push: it disappears from the dashboard;
# its authorized_keys line REMAINS on the instance (manual revocation).
# Empty registry:
mv ~/.config/remo/known_hosts /tmp/kh.bak
uv run remo web push        # exit 1: refuses without --allow-empty
mv /tmp/kh.bak ~/.config/remo/known_hosts

# Mount-configured service rejects adoption:
# (run a second container using today's RO bind mounts, then)
uv run remo web adopt   # -> exit 1, "configured via mounts" message
```

## G. RO bind-mount regression (FR-005 / SC-005)

Run the existing compose example (read-only mounts, no state volume, no
token) — service starts directly in configured mode, dashboard and terminals
behave exactly as the previous release; existing `tests/image/` suite stays
green.

## H. Tunnel fallback (FR-018)

```bash
uv run remo web adopt --via <docker-host>   # no direct route / proxy in the way
```

Expected: adoption completes through the SSH forward; if the service's
`REMO_WEB_ALLOWED_HOSTS` excludes `127.0.0.1`, the CLI fails with a message
naming that setting.

## Automated equivalents

- `uv run pytest tests/unit/web tests/unit/core tests/unit/cli` — state
  matrix, token gating, payload validation, keyscan decision table,
  idempotent authorized_keys command
- `uv run pytest tests/integration/ -k adopt` — scenario C/D against a local
  `remo web serve`
- `REMO_RUN_IMAGE_TESTS=1 uv run pytest tests/image/ -v` — scenarios A/B/G in
  the real container
