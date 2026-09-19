# Research: Discovery Resilience (024)

**Date**: 2026-09-19 · **Verified against**: main@5780f3b (branch `024-discovery-resilience`)

Every anchor below was verified by a read-only sweep of the real code in this
run (not taken from the feature request). Line numbers are exact at 5780f3b.

## Verified failure chain

1. **Unconditional snapshot overwrite** — `src/remo_cli/web/discovery.py:476-480`,
   nested `_run_and_store` in `DiscoveryService.refresh`: stores
   `self._snapshots[snapshot.instance_id] = snapshot` under `self._lock` with no
   comparison to the prior snapshot. A `TIMEOUT`/`UNREACHABLE` snapshot always has
   `targets=[]`, `capability=None` (`_snapshot()` at `discovery.py:144-166`), so it
   wholesale replaces an `OK` snapshot with N targets.
2. **OK-only target index** — `discovery.py:510-520` `_rebuild_target_index`
   filters `if snapshot.status is InstanceStatus.OK:` (line 517) into
   `self._targets_by_id` (declared `:397`). Targets for the host then vanish from
   `GET /api/v1/sessions` (`web/api/hosts.py:481-486`).
3. **Terminal authorization collapses with the index** —
   `web/api/terminals.py:261` (`create_terminal`: `find_target(...) is None` →
   `_unknown_target_error()`) and `:381` (`terminal_ws` reattach: `find_target`
   `None` → `TerminalState.ERROR` + WS policy-violation close).
4. **Workspace drops unresolved panes** —
   `frontend/src/components/WorkspacePane.tsx:140-151`: pane ids are mapped
   through `targetsById` (line 144) and `undefined` results filtered (`:145`,
   `:148-149`); zero resolved targets short-circuits to the "Select a session"
   empty state (`:152-168`). `targetsById` is built in `AppShell.tsx:111-118`
   from `discovery.targets`.
5. **Rail error block** — `frontend/src/components/railModel.ts:167`
   `isError = instance.status !== "ok" && instance.error != null`; rows come from
   `GET /sessions` targets (`:141-146`), so they empty out as a side effect.
   Rendered by `SessionRail.tsx:339-347` (`.rail-inst-error`). There is no
   Dashboard component; the container is `AppShell`, the rail is `SessionRail`.
6. **Timeout starvation** — `discovery.py:139-140` gives each of the two
   sequential blocking calls (`get_capabilities`, `list_sessions`) the full
   `settings.discovery_timeout_s`, while the outer `asyncio.wait_for` at
   `:255-258` bounds both together at that same value. Worst case the sync
   worker needs 2× the budget but is cancelled at 1×.
7. **Existing partial mitigation (frontend only)** —
   `frontend/src/state/discovery.ts:93-109` keeps last-known instances when the
   HTTP poll itself fails — but a *successful* poll carrying a failed-discovery
   payload replaces state wholesale (`:95`, `:105`). That is the gap.

## Decisions

### D1 — Staleness is advisory fields, not a new status

**Decision**: extend `DiscoverySnapshot` (`models/discovery.py:42-57`) and
`InstanceOut` (`web/api/hosts.py:101-109`) with additive fields
`stale: bool = False`, `last_ok_at: str | None = None`,
`consecutive_failures: int = 0`. Status stays `ok` during the grace window.
**Rationale**: (a) keeping `status = ok` is what keeps targets in
`_targets_by_id`, which `terminals.py:261/:381` use to authorize open/reattach —
the whole point; (b) adding an `InstanceStatus` member is a wire-breaking enum
change gated by the exhaustive `STATUS_META: Record<InstanceStatus, …>`
(`frontend/src/components/providerMeta.ts:45-53`) and both drift gates, for zero
benefit; (c) optional additive fields follow the `region`/`refreshed_at`
precedent — old bundles keep working.
**Alternatives rejected**: new `stale`/`degraded` status member (breaks the
enum contract and the openable gates at `railModel.ts:142` /
`providerMeta.ts:67-69`); a parallel "last-known-good" store beside
`_snapshots` (targets live in exactly two places today — `_snapshots[*].targets`
and `_targets_by_id`; a third store breaks that invariant and the lock-free
read model for no gain, since the retained snapshot IS the prior snapshot).

### D2 — Retention merge lives in the store step, not the probe

**Decision**: apply retention inside `DiscoveryService` at the store site
(`_run_and_store`, refactored into a testable `_merge_snapshot(prior, fresh)`
+ per-instance retention bookkeeping), under the existing `self._lock`.
`_discover_one` stays a pure probe.
**Rationale**: the probe has no access to prior state and runs concurrently;
the store step is the single serialization point and already holds the lock.
Readers stay lock-free: the merged snapshot is a *new* `DiscoverySnapshot`
(never mutate the stored prior in place — `DiscoverySnapshot` is a mutable
dataclass despite its docstring, and stale snapshots are aliased by readers),
and `_rebuild_target_index` keeps its fresh-dict + single-swap pattern
(`:515-520`).
**Survives the prune**: the post-gather prune (`discovery.py:487-496`) only
deletes snapshots for hosts no longer in the registry, so a graced host keeps
its retained snapshot; eviction (`evict`, `:506`) and deregistration also drop
the internal retention bookkeeping.

### D3 — Grace budget is elapsed time on the monotonic clock

**Decision**: new setting `discovery_offline_grace_s`
(`REMO_WEB_DISCOVERY_OFFLINE_GRACE_S`, default `120.0`) via `_env_float`
(`web/config.py:44-51`), validated `>= 0` in `__post_init__` (precedent:
`host_stats_ttl_s` at `:220-224`). The service tracks
`_last_ok_monotonic: dict[str, float]` (set on success, dropped on
evict/prune); a retryable failure hardens only when
`monotonic() - last_ok > grace`. `last_ok_at` (ISO wall-clock) is
display-only. Budget `0` disables retention entirely.
**Rationale**: matches the spec clarification (elapsed time, not counts — a
count would rescale with the client's 15s poll interval, which is frontend-only:
`DEFAULT_AUTO_REFRESH_INTERVAL_MS = 15_000`, `frontend/src/state/discovery.ts:28`;
there is no backend interval setting). Monotonic clock is immune to wall-clock
jumps. `consecutive_failures` is advisory display data only.

### D4 — Retryable set is exactly {TIMEOUT, UNREACHABLE}

**Decision**: a module-level `_RETRYABLE_STATUSES = frozenset({InstanceStatus.TIMEOUT,
InstanceStatus.UNREACHABLE})` in `discovery.py`. `AUTH_FAILED`, `NO_REMO_HOST`,
`INCOMPATIBLE_PROTOCOL`, `MALFORMED` store immediately (advisory fields still
populated: `stale=False`, `last_ok_at` preserved, counter reset to 0).
**Rationale**: those four are deterministic misconfigurations
(`discovery.py:219-230`, `:299-313`, `:271-282`, `:283-297`/`:314-327`);
retaining through them would hide a real fix-me. Matches spec FR-005 and the
Assumptions ("new statuses default to non-retryable").

### D5 — Outer timeout budget = 2 × per-call + slack

**Decision**: the `asyncio.wait_for` at `discovery.py:255-258` gets
`timeout=2 * settings.discovery_timeout_s + 5.0` (a small constant slack for
SSH argv setup between the calls), extracted as a helper so the timeout error
message (`:266`, currently interpolating `{settings.discovery_timeout_s:.0f}s`)
names the actual total budget. Per-call timeouts (`:139-140`) are unchanged.
**Rationale**: the two calls are sequential and each legitimately owns a full
per-call budget; the outer bound exists only as a safety net and must not be
tighter than the sum of its parts. Known limitation (pre-existing, unchanged):
`wait_for` cancels the await, not the executor thread — a truly hung host
occupies a default-pool thread for up to the sum of the per-call timeouts.

### D6 — Rail: `isStale` beside `isError`, never replacing it

**Decision**: `RailGroup` (`railModel.ts:64-78`) gains `isStale: boolean`
(`instance.status === "ok" && instance.stale === true`); `isError` (`:167`) is
untouched. `SessionRail.tsx` renders a compact chip ("not responding ·
retrying") and a dimmed modifier class on the group when `isStale`; rows render
as normal (they exist, because the backend kept the targets). Grace-exhausted
and non-retryable instances flow down today's `isError` path byte-for-byte.
**Rationale**: staleness and error are different truths with different
presentations (spec US2); reusing the error block with softer copy would still
drop rows on the floor. `DiscoveryInstance` already flows from the generated
`InstanceOut` type (`frontend/src/api/client.ts:33`), so the new fields arrive
via regeneration, not hand-typing.

### D7 — Workspace: sticky last-known targets keyed to pane lifecycle

**Decision**: `WorkspacePane` keeps an in-memory sticky map
(`useRef<Map<string, SessionTarget>>`): every render, each attached pane id
that resolves via `targetsById` refreshes its sticky entry; resolution falls
back to the sticky entry when the live lookup misses; closing a pane deletes
its entry. Never persisted (spec clarification 3). Ids with neither a live nor
a sticky record (pre-first-resolve restore from localStorage) keep today's
filtered behavior.
**Rationale**: the unmount at `WorkspacePane.tsx:144-149` is the frontend half
of the bug and must not depend on discovery at all (US3) — the terminal's own
`connectionState` (`TerminalCard.tsx:291-292`, states from
`TerminalConnection.ts:31-37`, surfaced via `data-connection-state` at `:671`
and the header pill at `:710-715`) is the truth about liveness, and its
auto-reconnect exhausts in ~5.5s (3 attempts, backoff 500/1500/3500ms —
`TerminalConnection.ts:41-43`), well inside one 15s discovery tick, so a
backend-only fix cannot save an already-unmounted card.
**Alternatives rejected**: hoisting stickiness into `state/discovery.ts`
(would resurrect targets for the rail too, blurring D6's honest-error path);
persisting sticky targets with the layout (resurrects dead state across
sessions — rejected in clarification).

### D8 — TerminalCard presentation is already connection-driven; add only the degraded styling

**Decision**: no behavioral change to `TerminalCard`. Its presentation already
keys off `connectionState` (`data-connection-state` attribute + state pill +
manual-reconnect button `:731-742`); the feature adds only CSS so
`disconnected`/`reconnecting`/`error` states read as dimmed/greyed on the
mounted card, if not already covered.
**Rationale**: US3 scenario 2 is satisfiable by the existing two-axis model
(transport `connectionState` pill + typed-`error` block at `:922-944`); adding
a second liveness source would reintroduce the bug's root confusion.

### D9 — Contract regeneration (Principle IV)

**Decision**: after the `InstanceOut` change: `uv run python
scripts/export_openapi.py` → `cd frontend && npm run generate:types` → gates
`uv run pytest tests/unit/test_schema_drift.py` and
`npm run check:types-fresh`. No WS frame change, so `terminal-frames.*`
regenerate as a no-op via the same commands.
**Verified**: exporter at `scripts/export_openapi.py` (command pinned in
`tests/unit/test_schema_drift.py:52`); artifacts in
`frontend/src/api/generated/`; `client.ts` imports generated types only.

### D10 — Test placement

- Backend: `tests/unit/web/test_discovery.py` (retention, grace, merge,
  prune-survival, zero-budget; pytest-asyncio **strict mode** — every async
  test needs `@pytest.mark.asyncio`), `tests/unit/web/test_hosts_mapping.py`
  (`_instance_out` mapping of the new fields), timeout-budget unit next to the
  existing discovery tests. Settings injectable directly
  (`WebSettings(discovery_timeout_s=…)` — plain dataclass, no env patching).
- Frontend (vitest/jsdom, config `frontend/vite.config.ts:12-24`):
  `railModel.test.ts` (stale vs error vs ok grouping — the "non-ok with known
  targets" case has no existing coverage), `SessionRail.test.tsx` (chip +
  dimming render), `WorkspacePane.test.tsx` (sticky resolution: pane survives
  target disappearance; closing drops the sticky record — extends the existing
  "never remounts a terminal across layout changes" guard at `:144`).

## Non-changes (verified unnecessary)

- `web/health.py` — no discovery fields in `HealthResponse`/`ReadinessResponse`;
  untouched.
- `POST /discovery/refresh` (`hosts.py:488-508`) — returns 202 and runs in
  background; refreshed results flow through the same retention at the store
  site, so no route change. (This is also why staleness must ride on
  `InstanceOut` itself — the console never observes a refresh completing.)
- `frontend/src/state/discovery.ts` poll-failure tolerance (`:93-109`) — correct
  and untouched.
- No new `InstanceStatus` member; no registry schema change; no new runtime
  dependency (stdlib `time.monotonic` only).
