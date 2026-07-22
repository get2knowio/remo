# Host-Command Contract: marker apply & read

All commands run through the existing per-host SSH helpers
(`_ssh_run_on_incus_host` / `_ssh_run`), which also handle `host == "localhost"`
for Incus. `<name>`/`<vmid>` are `shlex.quote`d.

## Incus

### Apply marker (create + update) — idempotent (FR-001, FR-002, FR-004)

```
incus config set <name> user.remo=true
```
- Success: rc 0 (no-op when already set — SC-005).
- Failure: warn per FR-005; do not fail the enclosing command on this alone.

### Read markers for sync — single bulk query (FR-013)

```
incus list -f csv -c n,user.remo
```
- Output rows: `<name>,<marker-value>`; `marker-value == "true"` ⇒ marked.
- Default sync keeps marked rows; `--all` keeps all and counts `!= "true"`.
- Fallback (older Incus, if the column form is unreliable): two queries —
  `incus list -f csv -c n` (all) and `incus list user.remo=true -f csv -c n`
  (marked) — still bounded, still no per-container round-trip.

## Proxmox

### Apply marker (create + update) — union, preserve tags (FR-001..FR-004)

Read current tags, then write only if `remo` is absent:
```
pct config <vmid>                      # parse the `tags:` line → set
# if "remo" not in set:
pct set <vmid> --tags "<tag1;tag2;remo>"   # existing order preserved, remo appended
```
- Separator: split read on `[;, ]+`; join write with `;`.
- `remo` already present ⇒ skip the `pct set` entirely (strict no-op, no
  reorder — SC-005, FR-003).
- Failure: warn per FR-005; do not fail the enclosing command on this alone.

### Read markers for sync — inventory + one bulk tag read (FR-013)

```
pct list                                       # existing: vmid + name inventory
grep -H '^tags:' /etc/pve/lxc/*.conf           # one round-trip: vmid → tag line
```
- `<vmid>.conf` with `remo` in its tag line ⇒ that container is marked.
- A vmid absent from the grep output (no `tags:` line) ⇒ unmarked.
- Consistent with existing snapshot code that reads `/etc/pve/lxc/<vmid>.conf`.

## Invariants across both providers

- `sync` issues **no** apply/remove/modify command — read-only (FR-010).
- Marker literals come only from `core/config.py` constants (fixed, not
  configurable).
- Behavior is identical for localhost and remote Incus hosts.
