# Data Model: Discovery Resilience (024)

## Extended: `DiscoverySnapshot` (`src/remo_cli/models/discovery.py`)

Plain dataclass (unchanged nature). Additive fields, defaults preserve current
behavior for any constructor call site that does not pass them:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `stale` | `bool` | `False` | True only on a retained-ok snapshot served during the grace window. Never true on a fresh `ok` or on any non-ok snapshot (post-grace hard error serves `stale=False`). |
| `last_ok_at` | `str \| None` | `None` | ISO-8601 wall-clock timestamp of the last *successful* discovery for this instance. Populated on every success; carried forward verbatim on retained and failure snapshots. Display-only — never used for grace math. |
| `consecutive_failures` | `int` | `0` | Count of consecutive retryable failures since the last success. `0` on fresh success and on non-retryable snapshots. Advisory only. |

Existing fields unchanged: `instance_id`, `instance_type`, `instance_name`,
`status`, `capability`, `targets`, `error`, `refreshed_at`, `region`.

### Retained-snapshot construction (the merge)

Given `prior` (stored snapshot, `status == OK`, possibly already stale) and
`fresh` (probe result with retryable status), the retained snapshot is a NEW
`DiscoverySnapshot` (never a mutation of `prior` — readers alias it):

- from `prior`: `capability`, `targets`, `region`, `last_ok_at`
- from `fresh`: `error` (the latest failure's `TypedError`), `refreshed_at`
- computed: `status = OK`, `stale = True`,
  `consecutive_failures = prior.consecutive_failures + 1`
- identity fields (`instance_id`, `instance_type`, `instance_name`) from
  `fresh` (they equal `prior`'s; `fresh` reflects the current registry entry).

### State transitions (per instance)

```
                     success                       retryable failure,
  (no snapshot) ────────────────► OK (fresh) ────► within grace ──► OK (stale)
        │                          ▲    │                             │  ▲ │
        │ retryable/non-retryable  │    │ non-retryable               │  └─┘ (counter++)
        ▼                          │    ▼                             │
   failure status (as today,       │  failure status (immediate,      │ grace exhausted OR
   advisory fields defaulted/kept) │  stale=False, counter=0,         │ non-retryable
                                   │  last_ok_at kept)                ▼
                                   └──────── success ──────── failure status
                                                              (stale=False, counter kept,
                                                               last_ok_at kept, targets=[])
```

- Retryable set: exactly `{TIMEOUT, UNREACHABLE}` (module constant).
- Grace check (elapsed monotonic time, not counts): retained only while
  `monotonic() - last_ok_monotonic <= discovery_offline_grace_s` and budget > 0.
- First-ever failure (no prior OK snapshot): stored as-is (nothing to retain).

## New (service-internal): retention bookkeeping (`web/discovery.py`)

`DiscoveryService` gains `_last_ok_monotonic: dict[str, float]` guarded by the
existing `self._lock` write discipline (written where snapshots are written).
Set on success; entry removed wherever the snapshot is removed (`evict`, the
deregistered-host prune). Reads occur only inside the store step (already
locked), so the lock-free public read model is untouched.

## Extended: `InstanceOut` (`src/remo_cli/web/api/hosts.py`)

Pydantic response model; additive fields mirroring the snapshot:

| Field | Type | Default |
|---|---|---|
| `stale` | `bool` | `False` |
| `last_ok_at` | `str \| None` | `None` |
| `consecutive_failures` | `int` | `0` |

Mapped in `_instance_out` (`hosts.py:193-223`). No `targets` on `InstanceOut`
(unchanged — sessions come from `GET /sessions`). Contract details:
`contracts/instance-out-stale.md`. Requires regenerating the four artifacts
(Principle IV).

## New (console-internal): sticky pane-target records (`WorkspacePane.tsx`)

`Map<string, SessionTarget>` held in a `useRef` — pane id → last successfully
resolved `SessionTarget`.

- **Refresh**: on every resolution pass, an attached id found in `targetsById`
  overwrites its sticky entry.
- **Fallback**: an attached id missing from `targetsById` resolves via its
  sticky entry instead of being filtered out.
- **Drop**: closing a pane deletes its entry; ids never resolved (e.g. layout
  restored from localStorage before first discovery) have no entry and keep
  today's filtered behavior.
- **Lifetime**: in-memory only; never persisted (spec clarification 3).

## Extended: rail view model (`frontend/src/components/railModel.ts`)

`RailGroup` gains `isStale: boolean` =
`instance.status === "ok" && instance.stale === true`. Orthogonal to `isError`
(a stale group is by construction not an error group, since `isError` requires
`status !== "ok"`). `SessionRail` renders `isStale` as a dimmed group with rows
intact plus a compact "not responding · retrying" chip.

## New setting (`src/remo_cli/web/config.py`)

| Field | Env var | Default | Validation |
|---|---|---|---|
| `discovery_offline_grace_s` | `REMO_WEB_DISCOVERY_OFFLINE_GRACE_S` | `120.0` | `>= 0` (`__post_init__`, `WebConfigError`); `0` disables retention |
