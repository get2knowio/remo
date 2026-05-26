# Phase 0 Research: Provider Snapshots

**Date**: 2026-05-24
**Status**: Complete — no `NEEDS CLARIFICATION` items remain from the technical context.

## Scope

The spec already locked down the user-visible behavior through `/speckit.clarify` (five clarifications recorded in spec). This research focuses on the per-provider primitives we'll actually call and the cross-cutting helpers we need.

## Provider primitives

### Incus

| Operation | Command | Notes |
|---|---|---|
| Create | `incus snapshot create <container> <snap-name>` | Synchronous (seconds). COW. Non-stateful by default. |
| List | `incus query /1.0/instances/<container>/snapshots?recursion=1` | JSON output; includes `created_at`, `size` (bytes), and the description field if set. `incus snapshot list <container> --format json` is the friendlier equivalent. |
| Restore | `incus restore <container> <snap-name>` | In-place; container must be stopped if it was running at snapshot time and you don't want a stateful restore. We're non-stateful so a temporary stop is harmless. |
| Delete | `incus snapshot delete <container>/<snap-name>` | Synchronous. |
| Description | Set via `--description <text>` on create; surfaced in JSON list output. | |
| Run location | All operations execute on the Incus host. We SSH to `host` (extracted from `KnownHost.name` which is `host/container`). | Same pattern as `_resolve_container_ip` in `providers/incus.py`. |

**Decision**: Invoke via SSH-to-host (no new Ansible playbook). Existing `_ssh_run` helper pattern in `providers/proxmox.py` is the model; `providers/incus.py` doesn't have a named `_ssh_run` helper but uses the same `subprocess.run(["ssh", ...])` shape inline. We'll lift the pattern into a small helper or replicate inline (the latter matches existing style — defer to taste during implementation).

### Proxmox

| Operation | Command | Notes |
|---|---|---|
| Create | `pct snapshot <vmid> <snap-name> --description <text>` | Synchronous. Non-stateful (no `--vmstate`). Confirmed out-of-scope by spec. |
| List | `pct listsnapshot <vmid>` | Text output with columns: name, parent, date, description. Parse with regex; or read `/etc/pve/lxc/<vmid>.conf` and look for `[<snap>]` sections (more reliable). |
| Restore | `pct rollback <vmid> <snap-name>` | In-place. Container is stopped during rollback, restarted by us afterwards if it was running. |
| Delete | `pct delsnapshot <vmid> <snap-name>` | Synchronous. |
| Storage detection | `pvesm status` lists storages with their type column. `pct config <vmid>` shows which storage backs the rootfs (`rootfs: <storage>:<volume>,size=...`). | Snapshot-capable: `zfspool`, `lvmthin`, `btrfs`, `cephfs`, `rbd`, `nfs` (with qcow2). NOT capable: `dir` (without qcow2), `lvm` (thick). |
| Run location | All operations execute on the Proxmox host. We SSH using the user stored in `KnownHost.region` (existing convention). VMID is `KnownHost.instance_id`. | |

**Decision**: Use the conf-file parsing approach for `list`. `pct listsnapshot` output is human-formatted and could change between PVE versions; parsing `/etc/pve/lxc/<vmid>.conf` is the canonical source and uses a stable INI-like format. Detect snapshot-incapable storage by running `pct config <vmid>` to find the rootfs storage, then `pvesm status` to look up its type, and bail before invoking `pct snapshot` if the type isn't in the supported set.

### AWS

| Operation | Boto3 call | Notes |
|---|---|---|
| Create | `ec2.create_snapshot(VolumeId=<root-vol>, Description=<text>, TagSpecifications=[{ResourceType: "snapshot", Tags: [...]}])` | Returns immediately with `SnapshotId` and `State=pending`. Async — completion in minutes. |
| List | `ec2.describe_snapshots(Filters=[{Name: "volume-id", Values: [<root-vol>]}, {Name: "tag:remo", Values: ["true"]}])` | Filtering by `volume-id` gives us the provider-side identity scope (spec FR-027). Tag filter ensures we only return remo-managed snapshots. |
| Restore | Multi-step (see "AWS restore flow" below). | In-place volume swap. Synchronous from user perspective; the underlying steps each take seconds-to-minutes. |
| Delete | `ec2.delete_snapshot(SnapshotId=<snap-id>)` | Synchronous from API perspective; storage release is eventual. |
| Tagging | `Tags: [{Key: "remo", Value: "true"}, {Key: "remo-snapshot-name", Value: <user-facing-name>}, {Key: "remo-instance", Value: <instance-name>}]` | `remo` tag = "managed by remo"; `remo-snapshot-name` = user-facing name; `remo-instance` = redundant but useful for human inspection in console. The authoritative scoping is volume-id (FR-027). |
| Root volume lookup | `ec2.describe_instances(InstanceIds=[<id>])` → `BlockDeviceMappings` → find the device matching `RootDeviceName` → `Ebs.VolumeId`. | Cache nothing; query on each call (matches existing pattern). |

**AWS restore flow** (FR-013, FR-016, FR-029):

1. Look up current root volume ID + size from `describe-instances`.
2. Look up snapshot by `remo-snapshot-name` tag (filtered to our volume-id, per FR-027); if status is not `completed`, fail per FR-028.
3. Confirm prompt with explicit downtime warning (FR-015). Default = No.
4. `stop_instances(InstanceIds=[id])`, wait for `stopped` state (poll `describe-instances` every 5s).
5. `detach_volume(VolumeId=<current-root>)`, wait for `available` (poll every 3s).
6. `create_volume(SnapshotId=<snap-id>, VolumeType=<current-type>, Size=<MAX(current-size, snapshot-size)>, AvailabilityZone=<instance-az>)`, wait for `available`.
7. `attach_volume(VolumeId=<new>, InstanceId=<id>, Device=<original-root-device-name>)`, wait for `in-use`.
8. `start_instances(InstanceIds=[id])`, wait for `running`.
9. `delete_volume(VolumeId=<old-root>)` (optional — could keep as safety net; **decision: keep** the old volume but tag it `remo-restore-orphan=<timestamp>` so the user can manually delete via console once happy. Document this in the success message.).
10. If steps 4–8 fail, fall back: best-effort `attach_volume` of the original volume, then re-`start_instances`. If the fallback also fails, print explicit recovery instructions naming the original volume ID, the new volume ID (if created), and the AZ.

**Decision**: Step 9 keeps the pre-restore root volume as a labelled orphan. Trade-off: small ongoing EBS cost, but bulletproof recovery if the new volume turns out to be corrupted. The success message will tell the user to delete it manually when they're satisfied. (This isn't called out in the spec but is consistent with FR-016's "either rollback or print explicit recovery instructions" intent.)

### Hetzner

| Operation | hcloud-python call | Notes |
|---|---|---|
| Create | `client.servers.create_image(server=<server>, type=ImageType.SNAPSHOT, description=<text>, labels={"remo": "true", "remo-snapshot-name": <name>, "remo-source-server-id": str(server.id)})` | Returns an `Action` and an `Image` resource. Async — completion in minutes. |
| List | `client.images.get_all(type=[ImageType.SNAPSHOT], label_selector=f"remo=true,remo-source-server-id={server.id}")` | Label selector enforces provider-side identity scope (FR-027). |
| Restore | `client.servers.rebuild(server=<server>, image=<snapshot-image>)` | In-place. Hetzner preserves server ID, IP, name. Synchronous-from-user-perspective; returns an Action we can poll for completion. |
| Delete | `client.images.delete(image=<image>)` | Synchronous. |
| Source server lookup | `client.servers.get_by_name(server-name)` → `.id` | We use the server name from `KnownHost.host` or `KnownHost.name`. |

**Decision**: For restore, poll the rebuild action until `status == "success"` (typically 1–2 minutes). Unlike AWS's multi-step swap, Hetzner's rebuild is atomic from the user's perspective — there's no intermediate broken state to recover from. The confirm prompt still warns about downtime.

## Cross-cutting helpers

### Snapshot name generation & validation (`core/snapshot.py` — NEW)

- `generate_default_name() -> str` → returns `remo-YYYYMMDD-HHMMSS` using `datetime.now()` in local time. Format keeps names sortable lexicographically.
- `validate_name(name: str) -> None` → raises `ClickException` on invalid input. Rules:
  - Length 1–40 (intersection of provider limits).
  - Characters: `[A-Za-z0-9_-]+` (intersection of provider rules; Hetzner labels disallow some chars Incus/Proxmox accept, so we take the strict union — keeps cross-provider name reuse possible).
  - Reject leading `-` (mimics provider-side restrictions).

### Snapshot model (`models/snapshot.py` — NEW)

See `data-model.md` for the full dataclass.

### Confirm-bypass flag

Existing `confirm()` in `core/output.py` takes only a prompt and a default. To support FR-014 / FR-018 / FR-022 "bypass flag", the new commands accept `--yes` / `-y` and pass `auto_confirm` through to the provider business-logic functions (matches existing pattern in `aws.stop()` and `aws.reboot()`, both of which already take `auto_confirm: bool`).

**Decision**: No change to `core/output.py`. The bypass logic is `if not auto_confirm: confirm(...)`, replicating the existing AWS pattern.

### Destroy integration

Each provider's existing `destroy()` business-logic function gets a new pre-step:

```python
existing_snapshots = snapshot_list_internal(...)  # same code as the public list
if existing_snapshots:
    print_warning(f"Instance '{name}' has {len(existing_snapshots)} snapshot(s):")
    _print_snapshot_table(existing_snapshots)
    if confirm("Delete these snapshots as part of destroy?", default=False):
        for snap in existing_snapshots:
            snapshot_delete_internal(...)
    else:
        print_warning("Snapshots will remain. On paid providers they continue to incur storage cost; they will be invisible to `remo` after the instance is destroyed.")
# ...then proceed with the existing destroy logic
```

This is additive — no behavior change for instances without snapshots (FR-023).

## Open implementation questions deferred to /speckit.tasks

- Exact wire format for parsing `pct listsnapshot` vs. `/etc/pve/lxc/<vmid>.conf` (both work; pick during impl).
- Whether to share a `_ssh_run` helper between `providers/incus.py` and `providers/proxmox.py` or duplicate inline (existing code does both).
- Polling intervals and timeouts (AWS instance state transitions, Hetzner rebuild action) — pick conservative defaults (5s poll, 10min timeout) and surface via `--verbose`.
- Whether `--yes` is the canonical flag name or `--auto-confirm` (AWS `destroy` uses both — `-y`/`--yes` short form and `--auto-confirm` long form). Decision: match existing AWS pattern verbatim.

## Constitution alignment

| Principle | Phase 0 stance |
|---|---|
| I. Defensive Variable Access (Ansible) | No new Ansible code planned. N/A. |
| II. Test All Conditional Paths | Each provider's snapshot tests will cover: success path, async-pending path (AWS/Hetzner), confirm-accept, confirm-decline, --yes bypass, name conflict, missing snapshot, network failure. |
| III. Idempotent by Default | Create with duplicate name → conflict error (FR-006). Destroy with no snapshots → unchanged behavior (FR-023). Delete of missing snapshot → loud error (decided here, not silent success). |
| IV. Fail Fast | Proxmox storage detection runs before any provider mutation; AWS restore validates snapshot state before stopping the instance; client-side name validation runs before any API call. |
| V. Documentation Reflects Reality | README will gain a "Snapshots" section in the tasks phase. |

No violations. Re-check after Phase 1 design.
