# CLI Contract: Snapshot Subcommands (all four providers)

**Date**: 2026-05-24

All four providers expose the same surface (per-provider command tree is identical in shape). The provider-specific behavior lives in the business-logic layer; the CLI contract here is shared.

## Command tree

```text
remo <provider> snapshot create   <instance> [--name NAME] [--description TEXT] [--verbose]
remo <provider> snapshot list     [INSTANCE] [--verbose]
remo <provider> snapshot restore  <instance> <snapshot> [-y / --yes] [--verbose]
remo <provider> snapshot delete   <instance> <snapshot> [-y / --yes] [--verbose]
```

Where `<provider>` ∈ {`incus`, `proxmox`, `aws`, `hetzner`} and is implemented as a Click `@click.group()` named `snapshot` nested under each existing provider group.

## `snapshot create <instance>`

**Args**:
| Arg | Type | Required | Description |
|---|---|---|---|
| `instance` | str (positional) | yes | The remo instance name (or `<host>/<name>` for incus/proxmox where the host prefix already applies). |
| `--name` | str | no | Snapshot name. Defaults to `remo-YYYYMMDD-HHMMSS` (local time). |
| `--description` | str | no | Free-text description, empty string by default. |
| `--verbose` | flag | no | Passes through to subprocess / SDK for debug output. |

**Exit codes**:
- `0` — Snapshot accepted by provider (sync providers: created; async providers: kickoff complete).
- `1` — Provider failure (snapshot name conflict, storage backend unsupported, network error, etc.).
- `2` — Client-side validation error (invalid name format, missing instance).

**Output**:
- Sync providers: `Created snapshot '<name>' for <provider> instance '<instance>'.`
- Async providers: `Snapshot '<name>' creation started for <instance>. This will take several minutes. Run \`remo <provider> snapshot list <instance>\` to check status.`

**Edge cases** (mapped to spec FRs):
- Duplicate name → FR-006 → exit 1 with `Snapshot '<name>' already exists for instance '<instance>'.`
- Proxmox unsupported storage → FR-005 → exit 1 with `Storage backend '<type>' for instance '<instance>' does not support snapshots. Supported backends: ZFS, LVM-thin, Btrfs, Ceph.`
- Invalid name → FR-025 → Click `BadParameter`, exit 2.

## `snapshot list [instance]`

**Args**:
| Arg | Type | Required | Description |
|---|---|---|---|
| `instance` | str (positional) | no | Filter to one instance. If omitted, lists snapshots for all known instances of the provider. |
| `--verbose` | flag | no | Passes through. |

**Exit codes**:
- `0` — Query succeeded (output may show "no snapshots").
- `1` — Provider/account unreachable.
- `2` — Client-side error (unknown instance name).

**Output**: Table with these columns (Incus / Proxmox omit STATUS since they're always AVAILABLE):

```text
INSTANCE       SNAPSHOT                  CREATED               SIZE      STATUS      DESCRIPTION
dev1           remo-20260524-101530      2026-05-24 10:15:30   1.2 GiB   available   pre-upgrade
dev1           remo-20260524-143012      2026-05-24 14:30:12   ─         pending     before-config-change
```

When no snapshots exist for the requested scope: `No snapshots found for instance '<instance>' on <provider>.` (FR-010).

## `snapshot restore <instance> <snapshot>`

**Args**:
| Arg | Type | Required | Description |
|---|---|---|---|
| `instance` | str (positional) | yes | |
| `snapshot` | str (positional) | yes | The user-facing snapshot name. |
| `-y` / `--yes` | flag | no | Bypass the confirm prompt (FR-014). |
| `--verbose` | flag | no | |

**Exit codes**:
- `0` — Restore completed and the instance is back to its pre-restore reachable state.
- `1` — Provider failure, restore declined, pending snapshot, missing snapshot, or mid-flight failure with recovery instructions printed.
- `2` — Client-side error.

**Confirm prompt** (when `--yes` not passed):
- Incus / Proxmox: `Restore '<snapshot>' to <instance>? Container will be stopped during rollback. [y/N]`
- AWS: `Restore '<snapshot>' to <instance>? Instance will be stopped, root volume swapped, and restarted — typically 2-5 minutes of downtime. [y/N]`
- Hetzner: `Restore '<snapshot>' to <instance>? Server will be rebuilt from the snapshot image — typically 1-2 minutes of downtime. [y/N]`

Default = No (FR-014, matches existing destructive-action convention).

**Output on success**: `Restored '<snapshot>' to <instance>. You can reconnect with: remo shell <instance>` (FR-013, SC-002).

**Output on AWS mid-flight failure** (FR-016): `Restore failed at step <N>: <step description>. The pre-restore root volume '<vol-id>' is preserved in <AZ> and can be re-attached manually with: aws ec2 attach-volume --volume-id <old-vol-id> --instance-id <instance-id> --device <device-name>`

## `snapshot delete <instance> <snapshot>`

**Args**:
| Arg | Type | Required | Description |
|---|---|---|---|
| `instance` | str (positional) | yes | |
| `snapshot` | str (positional) | yes | |
| `-y` / `--yes` | flag | no | Bypass confirm prompt (FR-018). |
| `--verbose` | flag | no | |

**Exit codes**:
- `0` — Snapshot deleted from provider.
- `1` — Provider failure, delete declined, pending snapshot, or missing snapshot.
- `2` — Client-side error.

**Confirm prompt** (when `--yes` not passed):
`Delete snapshot '<snapshot>' of <instance>? [y/N]`

Default = No.

**Output on success**: `Deleted snapshot '<snapshot>' of <instance>.`

## Destroy integration

Each provider's existing `destroy` command gains a pre-destroy step (FR-020 — FR-023):

**When the instance has ≥1 snapshots**:
```text
Instance '<instance>' has 3 snapshot(s):
INSTANCE  SNAPSHOT                  CREATED               SIZE      STATUS
dev1      remo-20260524-101530      2026-05-24 10:15:30   1.2 GiB   available
dev1      remo-20260524-143012      2026-05-24 14:30:12   1.3 GiB   available
dev1      pre-experiment            2026-05-23 09:00:00   1.1 GiB   available

Delete these snapshots as part of destroy? [y/N]
```

- **Yes** → delete each snapshot, then proceed with the existing destroy logic (FR-021).
- **No** → print `Snapshots will remain on <provider>. After destroy they become invisible to remo and (on paid providers) continue to incur storage cost. Manage via the provider console.` (FR-022), then proceed.

**When the instance has 0 snapshots**: no change from current behavior (FR-023).

## Test contract

Each provider's test file under `tests/unit/cli/providers/test_<provider>_snapshot.py` MUST cover:

| Scenario | CLI invocation | Expected exit | Expected output assertion |
|---|---|---|---|
| Create — happy path | `snapshot create dev1` | 0 | output contains "Created snapshot 'remo-" and the instance name |
| Create — explicit name + description | `snapshot create dev1 --name pre-x --description "before x"` | 0 | output contains "pre-x" |
| Create — duplicate name | (mock list returns existing snap with that name) `snapshot create dev1 --name pre-x` | 1 | output contains "already exists" |
| Create — invalid name | `snapshot create dev1 --name "bad name!"` | 2 | Click error message |
| Create — async (AWS/Hetzner only) | `snapshot create dev1` | 0 | output contains "will take several minutes" |
| Create — Proxmox unsupported storage | (mock storage detect returns 'dir') `snapshot create dev1` | 1 | output contains "does not support snapshots" |
| List — happy path with rows | `snapshot list dev1` | 0 | table headers present, row count matches mock |
| List — no snapshots | `snapshot list dev1` | 0 | "No snapshots found" |
| List — provider unreachable | `snapshot list dev1` | 1 | underlying error surfaced |
| Restore — confirm Yes | `snapshot restore dev1 pre-x` (mock confirm True) | 0 | success message includes reconnect hint |
| Restore — confirm No | `snapshot restore dev1 pre-x` (mock confirm False) | 1 | no provider mutation calls made |
| Restore — bypass with --yes | `snapshot restore dev1 pre-x --yes` | 0 | confirm not called |
| Restore — pending snapshot | (mock list returns snap with PENDING) `snapshot restore dev1 pre-x` | 1 | "is still pending" |
| Restore — missing snapshot | `snapshot restore dev1 nonexistent` | 1 | "not found" |
| Restore — AWS mid-flight failure | (mock detach raises) `snapshot restore dev1 pre-x --yes` | 1 | recovery instructions include both volume IDs |
| Delete — confirm Yes | `snapshot delete dev1 pre-x` (mock True) | 0 | provider delete called |
| Delete — confirm No | `snapshot delete dev1 pre-x` (mock False) | 1 | provider delete NOT called |
| Delete — bypass | `snapshot delete dev1 pre-x --yes` | 0 | confirm not called |
| Delete — pending snapshot | `snapshot delete dev1 pre-x` | 1 | "is still pending" |
| Destroy with snapshots — accept cleanup | `destroy dev1` (mock confirm True, True) | 0 | each snapshot deleted; then instance destroyed |
| Destroy with snapshots — decline cleanup | `destroy dev1` (mock confirm True, False) | 0 | snapshots NOT deleted; orphan warning printed; instance destroyed |
| Destroy without snapshots — unchanged | `destroy dev1` (mock list returns empty) | 0 | no snapshot prompt; behaves as today |
