# Contract: `remo <provider> sync` CLI surface

**Feature**: `016-sync-reconcile`

The user-facing contract. All four providers conform (FR-001).

## Command shapes

```
remo incus   sync [--host H] [--user U] [--use-ip] [--all] [--yes|-y] [--dry-run]
remo proxmox sync  --host H  [--user U] [--use-ip] [--all] [--yes|-y] [--dry-run]
remo aws     sync [--region R]                     [--all] [--yes|-y] [--dry-run]
remo hetzner sync                                  [--all] [--yes|-y] [--dry-run]
```

New on every provider: `--yes/-y`, `--dry-run`.
New on AWS and Hetzner: `--all`.
Hetzner gains its first flags at all.

### Flag semantics

| Flag | Meaning |
|---|---|
| `--all` | Widen additions to hosts lacking the managed marker (FR-028). Does **not** affect removals, and does **not** change what the provider query enumerates — the query always sees unmarked hosts (FR-044). |
| `--yes`, `-y` | Skip the removal confirmation (FR-012). Does not suppress the removal report. |
| `--dry-run` | Print the plan, change nothing, prompt for nothing, exit 0 (FR-042). Takes precedence over `--yes`. |
| `--use-ip` | Incus/Proxmox only, unchanged (FR-025). |
| `--host` / `--user` | Incus/Proxmox only, unchanged. `--host` required for Proxmox. |
| `--region` | AWS only. Absent → the existing default-region resolution (`_effective_region`). |

### Click wiring

Follow the AWS explicit-destination style so the provider kwarg needs no renaming:

```python
@click.option("--yes", "-y", "auto_confirm", is_flag=True, default=False,
              help="Skip the removal confirmation prompt.")
@click.option("--dry-run", "dry_run", is_flag=True, default=False,
              help="Show what would change without writing to the registry.")
@click.option("--all", "include_all", is_flag=True,
              help="Also register instances that lack the remo managed marker.")
```

Each wrapper must `sys.exit(rc)` — today none of the four does, which is why sync always exits 0 (R9).

## Exit codes (FR-043)

| Code | Meaning |
|---|---|
| `0` | Plan applied, or nothing to do, or `--dry-run` completed |
| `1` | Failure: provider query failed, write failed, or the plan was refused (ambiguous / conflicted) |
| `2` | **Reserved** — argument/usage errors, owned by Click and `remo shell`. Never emitted by sync. |
| `3` | Aborted with no change: confirmation declined, or removals needed consent with no TTY and no `--yes` |

## Output contract

Ordered sections. Empty sections are omitted; `--dry-run` prefixes the header.

```
Reconciling aws region us-west-2...

  + added      2   devbox, buildbox
  ~ updated    1   webhost
  = unchanged  3   alpha, beta, gamma (stopped)
  - removed    1   oldbox

The following 1 entry will be REMOVED from the registry:
  - oldbox (i-0abc123, us-west-2)

Remove it? [y/N]
```

Rules:

1. **Scope line first** (FR-003), before any change is described.
2. Every non-empty category names its entries (FR-007). Counts reflect what is actually written — not the pre-filter result count, which is what two providers report today.
3. **No-op** prints `Nothing to reconcile — registry already matches <scope>.` and exits 0 (FR-007, user story 4 scenario 4).
4. **Non-running state** is annotated inline on the entry, e.g. `beta (stopped)` (FR-019). Never persisted.
5. **Removals** are listed individually before the prompt (FR-010), with enough identifying detail to recognise them.
6. **Unmarked retained entries** get a note plus the permanent-marking hint (FR-024):
   `2 retained entries are not remo-marked: legacy1, legacy2` / `Mark permanently: remo incus update --name <n> --host <h>`
7. **Skipped unmarked hosts** name themselves and both remedies (FR-029):
   `Skipped 3 unmarked instance(s): a, b, c` / `Adopt this run: … --all` / `Mark permanently: … update …`
8. **Adoption criteria** are stated whenever `--all` is set (FR-030), e.g.
   `--all: also matching instances named remo-* without the remo tag` (AWS) or
   `--all: every server in this Hetzner project` (Hetzner).
9. **Suppressed removals** warn explicitly (FR-040):
   `Removals skipped: the provider listing was incomplete (<reason>). Additions and updates were applied.`
10. Errors go through `print_error` (stderr); everything else through `print_info`/`print_success`/`print_warning` (stdout), matching `core/output.py`.

## Behavioural guarantees

| # | Guarantee | FR |
|---|---|---|
| C1 | Entries outside the scope are never read as removal candidates, modified, or counted | FR-004 |
| C2 | Exactly one registry write per run; none at all on dry-run, decline, or failure | FR-006, SC-006 |
| C3 | A failed provider query leaves the registry untouched and exits 1 | FR-009 |
| C4 | Removals always require consent; additions and updates never prompt | FR-010, FR-011 |
| C5 | Declining changes nothing — not the additions either | FR-013 |
| C6 | No provider-side state is mutated | FR-008 |
| C7 | Re-running against an unchanged provider is a no-op and does not prompt | FR-036, SC-007 |
| C8 | An unmarked host that still exists is never removed | FR-022 |
| C9 | Removals require a provably complete enumeration | FR-040 |
| C10 | The query is never narrowed by the managed marker | FR-044 |
| C11 | The scope line names the boundary actually enumerated | FR-045 |
| C12 | A same-scope registry change between plan and write aborts the write | FR-046 |

## Compatibility notes

- Additive for incus/proxmox: existing invocations keep working. The `--all` warning text changes — the claim that "a later default `sync` will drop those unmarked one(s) again" is now false and must be deleted (FR-026).
- Behaviour change for AWS: bare `remo aws sync` no longer touches other regions, and stopped instances are retained.
- Behaviour change for Hetzner: `sync()` gains parameters; it previously took none.
- `sync` return type changes from `None` to `int` in all four providers, breaking `tests/unit/cli/providers/test_incus_sync_all.py` and `test_proxmox_sync_all.py` (they assert exit 0 against a `None`-returning mock).
