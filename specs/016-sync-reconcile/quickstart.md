# Quickstart: validating Unified Sync Reconcile

**Feature**: `016-sync-reconcile`

How to prove the feature works. Scenarios are ordered by user-story priority; each maps to acceptance criteria in [spec.md](./spec.md). Interfaces referenced here are defined in [contracts/cli-sync.md](./contracts/cli-sync.md) and [contracts/provider-probe.md](./contracts/provider-probe.md).

## Prerequisites

```bash
uv sync --all-extras
uv run pytest                       # baseline: everything green before you start
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

Known-failing-by-design once implementation lands (see research R11) — these must be *updated*, not deleted:

- `tests/unit/providers/test_provider_registry_entries.py` — pins the exact `KnownHost` shape each provider writes.
- `tests/unit/cli/providers/test_incus_sync_all.py`, `test_proxmox_sync_all.py` — assert `exit_code == 0` against a `None`-returning `sync` mock.
- `tests/unit/providers/test_aws_snapshot.py` — its `ec2` stub needs `get_paginator`.

## Automated validation

```bash
uv run pytest tests/unit/core/test_reconcile.py -v          # pure plan logic
uv run pytest tests/unit/providers -k sync -v               # per-provider probes
uv run pytest tests/unit/cli/providers -k sync -v           # flags and exit codes
uv run pytest tests/integration -k reconcile -v             # real temp registry
```

The pure-logic suite is the centre of gravity: `build_plan` performs no I/O, so the entire classification matrix in [data-model.md](./data-model.md) is testable without a provider, a registry, or a mock.

## Scenario 1 — Empty result no longer wipes the registry (P1, SC-001)

The headline bug. Uses a real temp registry via the `tmp_config_dir` fixture (`tests/conftest.py:10-21`).

**Setup**: seed three in-scope entries with `write_v2_registry`; stub the probe to return zero hosts, `complete=True`.

| Run | Expect |
|---|---|
| decline the prompt | registry byte-identical; output says nothing was changed; exit **3** |
| `--yes` | all three removed and each named in the output; exit **0** |
| probe raises `ProbeError` | registry unchanged; error on stderr; exit **1** |
| no TTY, no `--yes` | registry unchanged; message names `--yes`; exit **3** |
| `--dry-run` | plan printed; registry unchanged; no prompt; exit **0** |

The decline case is the one to check byte-for-byte — FR-013 requires that additions and updates are abandoned too, not just the removals.

## Scenario 2 — AWS region isolation (P1, SC-002)

**Setup**: registry holds `devbox` (`us-west-2`) and `eubox` (`eu-central-1`). Stub the paginator to return only `eubox`.

```bash
remo aws sync --region eu-central-1 --dry-run
```

- `devbox` appears in **no** category — not added, removed, or unchanged. It is out of scope, and `--dry-run` makes that checkable without touching anything.
- Apply for real: `devbox` retains its recorded region.
- Regression guard: an entry with an **empty** region must never be proposed for removal in any region, but must be matched and stamped if a same-named host is found (data-model, `in_update_scope` vs `in_removal_scope`).

## Scenario 3 — Stopped instances survive (P1, SC-003)

**Setup**: a registry entry for `parked` with a recorded public IP; the stub returns that instance with `State.Name == "stopped"` and **no** `PublicIpAddress` key — the shape AWS actually returns.

- Entry retained, classified `unchanged`, annotated `(stopped)` in the report.
- Recorded IP preserved — not blanked, not replaced by the instance id (FR-018 via `merge_entry`).
- Recorded region intact, so `remo aws start parked` resolves correctly afterwards.
- Nothing about the state reaches `registry.json` (FR-019). Assert on the file contents, not just the in-memory entry.
- Contrast: a `terminated` instance is excluded from the probe, so it appears under removals and needs consent.

## Scenario 4 — Uniform report and idempotency (P2, SC-004, SC-007)

For each of the four providers, with a mixed plan:

1. Scope line comes first.
2. Every non-empty category names its entries; counts equal what was written.
3. A changed address is reported `updated`, never `removed` + `added`, and does not prompt.
4. Run twice against an unchanged provider: the second run reports no changes, prompts for nothing, exits 0.

Then assert the write count: **one** registry write per run, zero on dry-run/decline/failure (SC-006). Spying on `mutate_registry` is the cheapest check, and it directly catches a regression to the old N+1-write shape.

## Scenario 5 — Enumeration completeness (SC-014)

The subtlest guarantee, and the easiest to regress.

- **Hetzner**: stub `_hetzner_api` to return a first page whose `meta.pagination.next_page` is `2`, then fail the second page. Expect: additions/updates applied, **zero** removals, explicit warning naming the incomplete listing. Without the fix this silently proposes deleting every server past the 25th.
- **AWS**: stub `get_paginator` to raise mid-iteration. Same expectation.
- **Happy path**: two full pages, `next_page: null` → `complete=True`, removals allowed.

## Scenario 6 — Adoption and marker semantics (P3, SC-010)

- Unmarked host present, no `--all` → not added; output names it and gives both remedies.
- With `--all` → added, warned as not remo-created, and the adoption criteria are printed.
- **Durability**: after adopting, run a plain sync with no flags. The entry is retained, reported unmarked, and **no prompt appears** (FR-022). This is the regression test for the semantics change — under the old marker-gates-removal behaviour it would be proposed for deletion on every run.
- Verify the stale warning is gone: `grep -rn "will drop those unmarked" src/` must return nothing (FR-026).

## Scenario 7 — Hetzner label gap (SC-009)

- `remo hetzner sync` against a stub containing a labelled server → discovered with no `--all`.
- `remo hetzner update` against an unlabelled server → label applied; re-running reports no change (FR-033).
- **Merge check**: an existing server carrying an unrelated label (`env: prod`) keeps it after backfill (FR-034). Hetzner's API replaces the label map wholesale, so a naive `PUT` fails this and it is the single most likely implementation slip.
- Ansible side: `grep -n "labels" ansible/roles/hetzner_server/tasks/main.yml` shows the new key; assert it sits on the server task only, not the shared SSH key (research R6).

## Manual smoke test

Only meaningful against real infrastructure. `--dry-run` makes the first three steps risk-free.

```bash
remo aws sync --dry-run                    # inspect the plan, nothing is written
remo aws sync --region eu-central-1 --dry-run
remo hetzner sync --dry-run
cp ~/.config/remo/registry.json /tmp/registry.backup.json
remo aws sync                              # answer 'n' at any prompt
diff ~/.config/remo/registry.json /tmp/registry.backup.json   # must be empty
```

Then confirm the exit-code contract:

```bash
remo aws sync --dry-run;  echo "expect 0: $?"
remo aws sync --nonsense; echo "expect 2: $?"      # Click usage error, untouched
echo -n | remo aws sync;  echo "expect 3 if removals pending: $?"
```

## Constitution checkpoints

- **II. Test All Conditional Paths** — the consent gate alone has five outcomes (no removals · `--yes` · confirmed · declined · non-interactive) and each must be covered. Same for `complete` True/False and `marked` True/False.
- **III. Idempotent by Default** — Scenario 4's double-run, plus the Hetzner label no-op in Scenario 7.
- **V. Documentation Reflects Reality** — before calling this done, update `README.md:291,300,313-314,324-325` (command reference) and `:405-424` (the troubleshooting prose that the marker-semantics change invalidates), `docs/proxmox.md:64-65` ("rebuild known_hosts entries" describes the behaviour being replaced), `docs/aws.md:218` (IAM table), and add sync sections to `docs/incus.md` and `docs/hetzner.md`, which have none.
