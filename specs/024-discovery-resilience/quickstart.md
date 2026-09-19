# Quickstart: Validating Discovery Resilience (024)

## Prerequisites

```bash
uv sync --all-extras          # backend + web extra + dev tools
cd frontend && npm ci && cd .. # frontend deps
```

## Targeted test runs (inner loop)

```bash
# Backend: retention/grace/merge/timeout-budget + mapping of the new fields
uv run pytest tests/unit/web/test_discovery.py tests/unit/web/test_hosts_mapping.py

# Frontend: rail stale presentation + sticky pane resolution
cd frontend && npx vitest run src/components/railModel.test.ts \
  src/components/SessionRail.test.tsx src/components/WorkspacePane.test.tsx
```

## Contract gates (Principle IV — must pass after the InstanceOut change)

```bash
uv run python scripts/export_openapi.py
cd frontend && npm run generate:types && npm run check:types-fresh && cd ..
uv run pytest tests/unit/test_schema_drift.py
```

## Full gates (before commit)

```bash
uv run pytest
uv run pytest tests/unit/test_architecture.py tests/unit/test_docs_structure.py
uv run ruff check src/remo_cli && uv run mypy src/remo_cli
cd frontend && npm run lint && npm run test && npm run build
```

## Manual end-to-end scenario (optional, needs a registered host)

1. `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S=120 uv run remo web serve`, open the
   console, open a terminal to a target on host H.
2. Simulate a blip: make H's discovery time out (e.g. drop SSH traffic to H
   briefly, or load H heavily). Expected within one poll (~15s): rail group
   for H dims with a "not responding · retrying" chip; rows stay; the open
   terminal never unmounts or disconnects; `GET /api/v1/hosts` shows
   `status:"ok", stale:true, consecutive_failures>=1`.
3. Restore H. Expected: chip clears on the next successful cycle;
   `stale:false, consecutive_failures:0`.
4. Keep H unreachable past the budget (>120s). Expected: today's red error
   block returns, targets leave `GET /sessions`, but the already-open pane
   stays mounted with its card's own connection pill reporting its state.
5. Zero-budget check: restart with `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S=0`;
   the first timeout surfaces immediately (pre-024 behavior).

Expected outcomes map to spec.md SC-001..SC-005.
