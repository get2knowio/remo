# Quickstart: Validating Registry v2

Runnable scenarios proving the feature end-to-end. Schema details: [contracts/registry-file-v2.md](contracts/registry-file-v2.md); API surface: [contracts/registry-accessor-api.md](contracts/registry-accessor-api.md); push wire contract: [contracts/mirror-payload-v2.md](contracts/mirror-payload-v2.md).

## Prerequisites

```bash
uv sync --all-extras           # includes web extra for service-side scenarios
export REMO_HOME=$(mktemp -d)  # isolate every scenario from your real config
```

## 1. Fresh install (S0 → S2)

```bash
uv run remo incus list
```

**Expected**: empty listing, no error, no migration notice; `$REMO_HOME` contains no backup file; `registry.json` is created only once a host is actually saved (no spurious writes on read).

## 2. Migration from a populated legacy file (S1 → S2) — the P1 scenario

```bash
cat > "$REMO_HOME/known_hosts" <<'EOF'
incus:nuc/dev1:dev1.incus:remo:paul:direct
proxmox:pve1/dev2:10.0.0.42:remo:104:direct:root
aws:buildbox:203.0.113.7:remo:i-0abc123def456:ssm:us-east-1
hetzner:dev1:198.51.100.9:remo
ssh:nas:nas.lan:admin:2222:direct:/home/paul/.ssh/id_nas
incus:old/box:box.incus:remo:paul:ssm
proxmox:old/pct:10.0.0.9:remo:101::root
this line is garbage
EOF
uv run remo incus list
```

The first five lines match what current providers actually write (every save path sets `access_mode` explicitly — `direct` or `ssm`). The two `old/…` lines are legacy variants that only exist in files written by older versions or by hand: a non-AWS line carrying a literal `ssm` (the `to_line` back-fill quirk when `instance_id` was set with an empty access mode) and a 7-field line with an empty access-mode slot. Migration's type-first rule must classify both as `access: "direct"`.

**Expected**:
- One-time migration notice: 7 entries migrated, backup name (`known_hosts.v1.bak`), the garbage line quoted as skipped, and the "next `remo web push` will re-verify all instances" note.
- `registry.json` exists and validates against the contract schema; the proxmox entry has `proxmox.vmid: "104"` and `proxmox.node_user: "root"`; the aws entry has `access: "ssm"`; the ssh entry has `ssh.port: 2222` (integer) and `ssh.identity_file`; both `old/…` legacy-variant entries have `access: "direct"` despite their odd access-mode slots.
- `known_hosts` is gone; `known_hosts.v1.bak` is byte-identical to the original (including the garbage line).
- Re-running any command produces **no** second migration notice and no new backup (idempotency).

## 3. Automated migration matrix

```bash
uv run pytest tests/unit/core/test_registry_migration.py -v
```

**Expected**: green across the matrix — all 5 types × optional-field combinations (4/6/7-field lines), garbage lines, unknown types (preserved, not dropped), empty vs missing file, pre-existing backup (numeric-suffix, never clobbered), both-present equivalent (silent rename completion) and divergent (v2 wins + warning, no merge), newer-version file (clear error, file untouched). Provider save-path fixtures pin the exact bytes each provider writes today (research R5 risk).

## 4. Values that corrupted the legacy format (US2)

```bash
uv run remo add v6box 'admin@[2001:db8::7]'        # OpenSSH-style brackets, optional [:port]
uv run remo add v6bare 2001:db8::8 --user admin    # bare IPv6 TARGET (no port suffix) also accepted
uv run remo incus list   # or `remo add`'s own listing
```

**Expected**: both IPv6 addresses round-trip intact in `registry.json` and in listings; `remo remove v6box` cleans up. Note: today `remo add`'s TARGET parser splits `[user@]host[:port]` at the first colon (providers/added.py), so IPv6 fails before the registry is even involved — T020 fixes the parser (bracket form plus bare-IPv6 heuristic) as part of this story. Unit round-trip/property tests:

```bash
uv run pytest tests/unit/core/test_registry_format.py -v
```

## 5. Concurrency: no lost updates (US4)

```bash
uv run pytest tests/integration/test_registry_concurrency.py -v
```

**Expected**: multiprocess writers upserting disjoint entry sets in a loop; final registry always contains the union; file parses cleanly on every iteration; a deliberately held lock makes a second writer fail with "registry busy" after ~5 s (not proceed on stale data); SIGKILL mid-write leaves the previous complete state.

## 6. Web service: readonly consumption of both formats (US3)

```bash
# Legacy file on a read-only mount — service must read in place, never write
chmod 555 "$REMO_HOME"
REMO_WEB_ALLOWED_HOSTS=127.0.0.1 uv run remo web check
chmod 755 "$REMO_HOME"
```

**Expected**: `remo web check` reports the registry as readable (naming which format it found), performs zero writes/mkdirs against `$REMO_HOME` (verify mtimes unchanged), and one seeded malformed entry degrades to a warning, not a failure. Repeat with a `registry.json` in place of the legacy file — identical host set reported. Parser-collapse check (SC-003):

```bash
grep -rn "from_line\|partition(\":\")" src/remo_cli/web/   # expect no private registry parsing left
```

## 7. Push payload versions & skew (US5)

```bash
uv run pytest tests/integration/test_setup_payload_versions.py -v
```

**Expected** (per the [compatibility matrix](contracts/mirror-payload-v2.md#4-compatibility-matrix-fr-021fr-022-sc-006)):
- v1 payload → accepted, stored as `registry.json` v2, legacy mirror file removed.
- v2 payload → accepted; wire entries match the file schema exactly.
- version 3 payload → 400 `unsupported_payload_version`, prior mirror intact and served.
- Status includes `payload_versions: [1, 2]`; the push flow aborts before any keyscan/PUT when the field is missing (old-service simulation), with remediation naming the service as the side to upgrade.
- After a real (or test-harness) migration, `web-service.json` without `cache_version: 2` is treated as empty → first push re-verifies every instance and is idempotent on immediate re-run.

## 8. Performance guard (SC-008)

```bash
uv run pytest tests/perf/test_registry_perf.py -v
```

**Expected**: generated 200-entry registry read+validate+write round-trip under 100 ms.

## 9. Full gates

```bash
uv run pytest
uv run mypy src/remo_cli
uv run ruff check src/remo_cli
```

**Expected**: all green; docs updated in the same change set (README registry section, `docs/web-session-interface.md` payload examples, CLAUDE.md storage line) per Constitution Principle V.
