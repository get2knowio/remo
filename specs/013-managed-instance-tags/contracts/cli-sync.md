# CLI Contract: `sync --all` and filtered-sync output

Applies to `remo incus sync` and `remo proxmox sync`. No other command's flags
change. AWS/Hetzner `sync` is untouched (FR-011).

## New flag

```
--all    Register every container discovered on the host, including those
         without the remo managed marker (pre-feature behavior). Default off.
```

- Type: boolean `is_flag` (Click), threaded to `providers.<p>.sync(all=<bool>)`.
- Coexists with the existing `--host`, `--user`, `--use-ip` options.

## Behavior contract

| Host state | Command | Registered | stdout hint/summary |
|------------|---------|-----------|---------------------|
| mix of marked + unmarked | `sync` (default) | only marked | names skipped unmarked containers + count + both remedies |
| all marked | `sync` (default) | all | normal `Synced N container(s)…` (no skip hint) |
| all unmarked | `sync` (default) | none | `Synced 0…` + skip hint naming all skipped + remedies |
| any | `sync --all` | all | normal summary; if ≥1 was unmarked, ALSO a line distinguishing the unmarked/adopted count + round-trip warning |

## Output contract — default filtered sync that skipped containers (FR-008)

Must include, in this spirit (exact wording flexible):

```
Synced 1 container(s) from '<host>'.
Skipped 2 unmarked container(s): plex, homeassistant
  • Adopt all this run:      remo <provider> sync --host <host> --all
  • Mark one permanently:    remo <provider> update <name>
```

Requirements:
- The skipped container **names** are listed (clarification 1), not just a count.
- Both remedies are named: `--all` and `remo <provider> update <name>`.
- Emitted via `core.output` (`print_info`/`print_warning`), consistent with the
  existing `Synced N…` line.

## Output contract — `--all` adopting unmarked containers (FR-009)

```
Synced 3 container(s) from '<host>' (2 not remo-created; adopted via --all).
Note: a later default `sync` will drop the 2 unmarked one(s) again.
```

Requirements:
- The count of registered-but-unmarked containers is distinguished from the
  total (FR-009).
- The round-trip behavior is stated plainly (Edge Case: mixed-marker `--all`).

## Non-goals (unchanged behavior)

- Registry line format is unchanged (FR-012).
- `remo shell` / `remo cp` connection path is unchanged (FR-012).
- Lifecycle commands (`destroy`, `snapshot`, resize) gain **no** marker check;
  they operate uniformly on any registry entry (clarification 2).
