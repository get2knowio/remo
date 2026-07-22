# Quickstart & Validation: Managed-Instance Tagging & Filtered Sync

Runnable validation for the feature. Assumes a working `remo` dev env
(`uv sync --all-extras`) and access to either an Incus host (localhost is fine)
or a Proxmox node over SSH with at least one hand-created (non-remo) container.

Details live in the design docs — see [contracts/cli-sync.md](./contracts/cli-sync.md),
[contracts/marker-commands.md](./contracts/marker-commands.md), and
[data-model.md](./data-model.md).

## Automated checks (no hypervisor needed)

```bash
uv run pytest tests/unit/providers/test_incus_marker.py \
              tests/unit/providers/test_proxmox_marker.py \
              tests/unit/cli/providers/test_incus_sync_all.py \
              tests/unit/cli/providers/test_proxmox_sync_all.py
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

These mock the SSH helpers (as the existing snapshot suites do) and assert:
marker apply is idempotent, Proxmox tag union preserves existing tags, default
sync filters, `--all` registers everything, and the hint/summary text matches
the CLI contract.

## Scenario 1 — filtered sync only pulls remo containers (US1, SC-001)

```bash
# Incus host with one remo container + one hand-made container:
remo incus create --name dev1 --host <host>      # applies user.remo=true
incus launch images:ubuntu/24.04 plex            # unrelated, unmarked

remo incus sync --host <host>
# Expect: "Synced 1 container(s)…" and a hint naming 'plex' as skipped,
#         with the --all and `remo incus update` remedies.
remo incus list        # dev1 present; plex absent
```

## Scenario 2 — create is picked up by the next sync (SC-002)

```bash
remo proxmox create --name dev1 --host <node>
remo proxmox sync --host <node>
remo proxmox list      # dev1 present with no extra action
```

## Scenario 3 — `--all` adopts everything, with a clear summary (US2, SC-003)

```bash
remo proxmox sync --host <node> --all
# Expect: every container registered; if any were unmarked, a summary line
#         distinguishing the unmarked/adopted count + round-trip warning.
```

## Scenario 4 — backfill via update (US3, SC-004)

```bash
# A container remo made before this feature (no marker):
remo incus sync --host <host>          # it is skipped (unmarked) — see hint
remo incus update --name dev1 --host <host>   # applies the marker
remo incus sync --host <host>          # now dev1 is registered
```

## Scenario 5 — idempotent re-apply preserves Proxmox tags (SC-005)

```bash
# Give a container a user tag first:
ssh <node> 'pct set <vmid> --tags mytag'
remo proxmox update --name dev1 --host <node>   # adds 'remo', keeps 'mytag'
ssh <node> 'pct config <vmid> | grep ^tags:'    # tags: mytag;remo
remo proxmox update --name dev1 --host <node>   # re-run: no change
ssh <node> 'pct config <vmid> | grep ^tags:'    # still: mytag;remo (no reorder)
```

## Scenario 6 — upgrade hint is unmissable (SC-006)

```bash
# On a host of pre-existing unmarked remo containers, a first default sync:
remo incus sync --host <host>
# Expect: registers nothing, and the hint names BOTH remedies
#         (--all and `remo incus update <name>`). No silent registry wipe,
#         no silent re-marking.
```

## Pass criteria

- Default `sync` registers exactly the marked containers; the skip hint names
  the skipped containers and both remedies.
- `--all` reproduces pre-feature behavior and flags adopted-unmarked counts.
- `update` marks pre-existing containers; re-apply is a no-op that preserves
  existing Proxmox tags.
- No change to AWS/Hetzner sync, the registry format, or the connect path.
