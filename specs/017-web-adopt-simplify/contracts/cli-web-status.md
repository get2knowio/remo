# Contract: `remo web status` (offline drift) + post-mutation nudge

Supports FR-009..FR-014. Both the command and the nudge use `core/web_drift.py` (stdlib only; importable without the `web` extra).

## `remo web status [OPTIONS]`

Compares the current local registry against the recorded push cache and reports per-instance drift. **Makes zero network or SSH connections** (FR-010).

| Option | Meaning |
|--------|---------|
| `--deployment TEXT` | Select which cached deployment to report against (deployment id or service URL). Required only when the cache records more than one deployment (FR-012 / Clarifications Q4). |

### Behavior

1. Load the push cache. If it does not exist or is empty → print a clear "no prior push recorded from this workstation" message and exit `0` (FR-011). Not an error.
2. Select the deployment: implicit when exactly one is cached; when more than one and `--deployment` is absent → exit `1` listing the known deployment ids so the operator can re-run with a selector. The reported-against deployment id is always shown in the output (FR-012).
3. Diff each registry entry against the deployment's cached instances:
   - present in registry, not in cache → `new`
   - present in both, fingerprint differs → `changed`
   - present in both, fingerprint equal → `in sync`
   - in cache, not in registry → `removed`
4. Render a table (name, type, state) grouped/colored by state.
5. If every instance is `in sync` → print "in sync — nothing to push" (FR-011).

### Exit codes

- `0` — status reported (including the "no prior push" and "in sync" cases).
- `1` — ambiguous multi-deployment selection with no `--deployment`.

Drift being present is **not** a non-zero exit — status is informational.

## Post-mutation out-of-date nudge (FR-013/FR-014)

A single-line notice printed after a **successful** registry-mutating command **iff** a push cache exists (`web_drift.out_of_date_notice()` returns non-`None`). Suppressed entirely when no cache exists (FR-014).

**Notice text (illustrative)**: `Your web deployment may now be out of date — run 'remo web status' to see what changed, or 'remo web push' to re-sync.`

### Trigger sites (exhaustive)

| Command | Site | Notes |
|---------|------|-------|
| `remo <provider> create` | `providers/{incus,proxmox,hetzner,aws}` create success | registry gains an entry |
| `remo <provider> destroy` | same providers, destroy success | registry loses an entry |
| `remo <provider> sync` | `core/reconcile.run_sync`, after successful `apply_plan` | one site covers all four providers (Spec 016 shared engine); fires only when the plan applied (not on dry-run/abort) |
| `remo add` | `cli/added.py` add success | registered SSH host added |
| `remo remove` | `cli/added.py` remove success | registered SSH host removed |

**Not triggered**: `remo aws stop|start|reboot` (no registry mutation), `remo <provider> list|info`, `remo web *`, dry-run/aborted syncs, and any command that failed.

**Gating rule**: cache-existence only, not an exact inline diff (Assumptions / Clarifications) — a rare false positive after a no-op mutation is acceptable because `remo web status` is the cheap authoritative follow-up.
