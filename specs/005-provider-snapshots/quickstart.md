# Quickstart: Provider Snapshots

**Audience**: A reviewer or implementer who wants to manually exercise the feature end-to-end on a real provider.

## Prereqs

```bash
uv sync --all-extras                    # boto3 + hcloud + dev tools
uv run remo --version                   # should be 2.0.0rc2 or later (this branch)
```

You need at least one of these already provisioned by remo:
- An **Incus** container (any Incus host that `remo incus list` knows about)
- A **Proxmox** LXC container (with rootfs on ZFS / LVM-thin / Btrfs — NOT `dir`)
- A **Hetzner** server (any region)
- An **AWS** EC2 instance (with the `aws` CLI configured)

Pick one to verify the round-trip; repeat for the others.

## Round-trip per provider

Replace `<P>` with `incus`, `proxmox`, `aws`, or `hetzner` and `<INST>` with your instance name.

### 1. Create a baseline file inside the instance

```bash
uv run remo shell <INST>
# inside:
echo "baseline" > /tmp/snapshot-test.txt
exit
```

### 2. Take a snapshot

```bash
uv run remo <P> snapshot create <INST> --description "quickstart baseline"
```

**Expected**:
- Incus / Proxmox: completes in seconds; final line `Created snapshot 'remo-YYYYMMDD-HHMMSS' for <P> instance '<INST>'.`
- AWS / Hetzner: returns within ~5 seconds with `creation started ... will take several minutes`. **Snapshot is NOT usable yet.**

### 3. List snapshots

```bash
uv run remo <P> snapshot list <INST>
```

**Expected**: Table with one row matching the snapshot you just created. On AWS/Hetzner the `STATUS` column shows `pending` initially, transitioning to `available` after a few minutes — re-run to observe the transition.

### 4. Mutate state inside the instance

```bash
uv run remo shell <INST>
# inside:
echo "mutated" > /tmp/snapshot-test.txt
echo "new file" > /tmp/should-be-gone-after-restore.txt
exit
```

### 5. Restore (wait for `available` first on AWS/Hetzner)

```bash
# Optional first: check status
uv run remo <P> snapshot list <INST>

uv run remo <P> snapshot restore <INST> remo-YYYYMMDD-HHMMSS
```

**Expected**:
- Prompt explicitly mentions the downtime expected for this provider.
- After completion, output includes `Restored ... You can reconnect with: remo shell <INST>`.

### 6. Verify the restore reverted state (SC-001)

```bash
uv run remo shell <INST>
# inside:
cat /tmp/snapshot-test.txt              # → "baseline"
ls /tmp/should-be-gone-after-restore.txt 2>&1   # → No such file or directory
exit
```

### 7. Verify reconnection works (SC-002)

The `remo shell <INST>` invocation in step 6 should have just worked — no manual SSH known_hosts editing required, no remo registry edits.

### 8. Delete the snapshot

```bash
uv run remo <P> snapshot delete <INST> remo-YYYYMMDD-HHMMSS
```

**Expected**: prompt; on accept, snapshot removed; `list` no longer shows it.

### 9. Destroy-time cleanup (one-shot)

Take two snapshots, then destroy with cleanup accepted, then destroy declined:

```bash
uv run remo <P> snapshot create <INST> --name cleanup-test-1
uv run remo <P> snapshot create <INST> --name cleanup-test-2
uv run remo <P> destroy <INST>
# at the snapshot prompt: y → both snapshots deleted, then instance destroyed
```

Repeat with a fresh instance to verify the decline path leaves snapshots intact (on AWS/Hetzner, verify in the provider console).

## Pre-flight failure paths to verify

### Proxmox: unsupported storage

If you have a Proxmox container whose rootfs is on `dir` storage:

```bash
uv run remo proxmox snapshot create <INST>
# Expected: exit 1, message naming 'dir' storage and listing supported alternatives.
```

### AWS: pending-snapshot operations (FR-028)

Immediately after `snapshot create` on AWS, while status is still `pending`:

```bash
uv run remo aws snapshot create <INST>
uv run remo aws snapshot restore <INST> <name-just-created>
# Expected: exit 1, message "snapshot ... is still pending; check `list` for status".
```

### Name validation

```bash
uv run remo <P> snapshot create <INST> --name "spaces are bad"
# Expected: exit 2, Click validation error.

uv run remo <P> snapshot create <INST> --name "-leadingdash"
# Expected: exit 2.

uv run remo <P> snapshot create <INST> --name pre-x
uv run remo <P> snapshot create <INST> --name pre-x
# Expected: exit 1 on second call, "already exists".
```

## Unit-test fast loop

```bash
uv run --extra dev pytest tests/unit/cli/providers/test_<provider>_snapshot.py \
                          tests/unit/providers/test_<provider>_snapshot.py \
                          tests/unit/core/test_snapshot.py -v
```

All 25+ scenarios from the CLI contract test matrix should pass before opening a PR.

## Cleanup

Don't forget to clean up snapshots taken during this quickstart, especially on AWS/Hetzner where they keep costing money:

```bash
uv run remo <P> snapshot list <INST>
# delete any leftover quickstart snapshots
```
