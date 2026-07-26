# Phase 1 Data Model: Unified Sync Reconcile

**Feature**: `016-sync-reconcile` | **Date**: 2026-07-25

No persisted schema changes. The registry format stays at v2 and no migration is introduced (SC-011). Everything below is in-memory, living in a new provider-agnostic module `src/remo_cli/core/reconcile.py`.

## Existing entity (unchanged)

### `KnownHost` — `src/remo_cli/models/host.py:8-29`

```python
type: str; name: str; host: str; user: str
instance_id: str = ""; access_mode: str = ""; region: str = ""
```

Identity is `(type, name)`, enforced by `validate_hosts` (`core/registry.py:344-359`). Per-type slot meanings are unchanged:

| type | `name` | `host` | `instance_id` | `access_mode` | `region` |
|---|---|---|---|---|---|
| incus | `<node>/<container>` | container name or IP | node SSH user | `direct` | — |
| proxmox | `<node>/<hostname>` | hostname or IP | vmid | `direct` | node SSH user |
| aws | resource name | public IP or instance id | instance id | `ssm`\|`direct` | AWS region |
| hetzner | server name | public IPv4 | — | — | — |

## New entities

### `SyncScope`

The bounded slice of the registry one sync run may change (FR-003, FR-004).

| Field | Type | Notes |
|---|---|---|
| `type` | `str` | `incus` \| `proxmox` \| `aws` \| `hetzner` |
| `host` | `str` | Incus/Proxmox node; `""` otherwise |
| `region` | `str` | AWS region; `""` otherwise |

**Two membership predicates, deliberately asymmetric:**

- `in_update_scope(entry) -> bool` — may be matched and refreshed.
- `in_removal_scope(entry) -> bool` — may be proposed for removal when absent.

| type | `in_update_scope` | `in_removal_scope` |
|---|---|---|
| incus, proxmox | `e.type == type and e.name.startswith(f"{host}/")` | same |
| hetzner | `e.type == "hetzner"` | same |
| aws | `e.type == "aws" and e.region in (region, "")` | `e.type == "aws" and e.region == region` |

**Why AWS differs**: a legacy AWS entry with no recorded region cannot be attributed to any region, so FR-023 ("cannot determine → retain") forbids ever removing it. But if the queried region turns out to contain a host of that name, matching it lets the reconcile *stamp* the region — the entry self-heals into a normal scoped entry on first sync. Removal remains impossible until it does.

**Validation**: `host` required non-empty for incus/proxmox; `region` required non-empty for aws; both empty for hetzner.

`describe() -> str` renders the scope line required by FR-003 and must name the enumeration boundary required by FR-045, e.g. `aws region us-west-2`, `incus host prox01 (default project)`, `proxmox node pve1 (this node only)`, `hetzner (all servers in project)`.

### `DiscoveredHost`

One host as the provider currently sees it.

| Field | Type | Notes |
|---|---|---|
| `entry` | `KnownHost` | desired registry shape for this host |
| `marked` | `bool` | carries the managed marker (FR-021) |
| `state` | `str` | observed provider state; `""` when N/A. Never persisted (FR-019) |
| `adopted` | `bool` | included only because the adoption flag was set (FR-030) |

`entry.name` is the matching key (FR-039). Provider identifiers inside `entry` are attributes, not identity.

### `ProbeResult`

A provider's complete answer for one scope, at one moment.

| Field | Type | Notes |
|---|---|---|
| `hosts` | `list[DiscoveredHost]` | every host found in scope, marked or not |
| `complete` | `bool` | enumeration exhaustive? (FR-040) |
| `incomplete_reason` | `str` | shown when `complete` is `False` |
| `adoption_criteria` | `str` | human-readable, printed when adoption is on (FR-030) |
| `warnings` | `list[str]` | non-fatal problems, e.g. one container's IP lookup failed |

**Invariant**: `hosts` contains every host the provider can see in scope — *including unmarked ones*. The marker only gates whether a host is eligible for addition; presence in this list is what protects an existing entry from removal (FR-022). Collapsing these two into one filtered list is precisely the bug being fixed.

**Corollary (FR-044)**: the marker must not appear in the query's filter either. AWS's `tag:remo=true` and Hetzner's `label_selector=remo` must be dropped and evaluated locally — a server-side marker filter makes an unmarked-but-live host indistinguishable from a deleted one, so the entry would be proposed for removal. Only terminal-state exclusion may be pushed server-side, because that genuinely establishes non-existence.

**Failure signalling** (FR-009): a probe that cannot ask the provider raises `ProbeError`; it never returns an empty `ProbeResult`. `complete=False` means "asked, but the answer may be partial."

### `ReconcilePlan`

The computed difference. Pure data — building it performs no I/O.

| Field | Type | Notes |
|---|---|---|
| `scope` | `SyncScope` | |
| `added` | `list[KnownHost]` | eligible at provider, not in registry |
| `updated` | `list[tuple[KnownHost, KnownHost]]` | `(before, after)` |
| `unchanged` | `list[KnownHost]` | |
| `removed` | `list[KnownHost]` | in registry, confirmed absent |
| `skipped_unmarked` | `list[str]` | present, unmarked, not adopted → not added (FR-029) |
| `retained_unmarked` | `list[str]` | in registry, present, unmarked → kept (FR-024) |
| `states` | `dict[str, str]` | name → observed state, non-running only (FR-019) |
| `removals_suppressed` | `bool` | enumeration incomplete (FR-040) |
| `baseline` | `tuple[KnownHost, ...]` | in-scope entries the plan was built from (R2 conflict check) |
| `warnings` | `list[str]` | carried from the probe |

Derived: `has_removals`, `is_noop` (`added`, `updated`, `removed` all empty).

**Classification rules** (FR-005), keyed by `entry.name` over the `in_update_scope` slice:

| Registry | Provider | Marker | → |
|---|---|---|---|
| absent | present | marked, or adoption on | **added** |
| absent | present | unmarked, adoption off | *skipped* (reported, not added) |
| present | present | any | **updated** if merge differs, else **unchanged**; also **retained_unmarked** if unmarked |
| present | absent | — | **removed**, but only if `in_removal_scope` **and** `complete` |
| present | absent | — | **unchanged** if `complete` is `False` (FR-040) or not `in_removal_scope` (FR-023) |

### `MergedEntry` rule (FR-041)

Not a type — the function `merge_entry(existing, discovered) -> KnownHost`:

- `type`, `name` — identity, never change.
- `host`, `access_mode`, `instance_id`, `region` — take the discovered value **when non-empty**, else keep the existing one.
- `user` — always preserved from `existing`. Every provider hardcodes `"remo"`, so the provider observes nothing here; preserving it respects hand-edits.

The "when non-empty" clause is what implements FR-018: a stopped AWS instance reports no `PublicIpAddress`, so `host` falls through to the last known address instead of being blanked or replaced by the instance id.

### `ConsentOutcome`

Result of the confirmation gate (FR-011 – FR-014).

`APPLY` · `DECLINED` · `NON_INTERACTIVE` · `NOT_REQUIRED`

`NOT_REQUIRED` covers both a no-removal plan and `--yes`. `DECLINED` and `NON_INTERACTIVE` both map to exit 3 with different messages.

## Errors

| Exception | Raised when | Exit |
|---|---|---|
| `ProbeError` | provider query failed (FR-009) | 1 |
| `ReconcileConflictError` | in-scope registry moved between plan and write (R2) | 1 |
| `AmbiguousPlanError` | two provider hosts resolve to one registry name (FR-037) | 1 |
| `ScopeError` | malformed scope (e.g. AWS scope with no region) | 1 |

None of these escape the provider layer: `run_sync` catches them, prints via `print_error`, and returns the code. `core/registry.py`'s no-`SystemExit` rule is preserved — the new module raises and returns, never exits; only the thin CLI wrapper calls `sys.exit(rc)`.

## Lifecycle

```
scope ──► probe(scope, include_all, use_ip) ──► ProbeResult      [network/SSH, no lock]
                                                    │
                       read_registry() ─────────────┤
                                                    ▼
                                            build_plan(...)      [pure, no I/O]
                                                    │
                                            render_plan(...)     [stdout]
                                                    │
                        ┌───────── dry-run? ────────┴─── yes ──► exit 0
                        │ no
                        ▼
                     consent gate                               [isatty + confirm()]
                        │ APPLY / NOT_REQUIRED
                        ▼
                mutate_registry(apply)                          [locked, ~microseconds]
                   └─ re-derive in-scope slice
                   └─ compare against plan.baseline ──► differ? ReconcileConflictError
                   └─ return out-of-scope + reconciled in-scope
```

Discovery and prompting sit entirely outside the lock; the locked critical section is a list comparison and a splice.
