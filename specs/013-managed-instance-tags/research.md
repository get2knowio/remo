# Phase 0 Research: Managed-Instance Tagging & Filtered Sync

All Technical Context items are resolved — no `NEEDS CLARIFICATION` remains.
The three clarifications from `/speckit-clarify` (hint names skipped containers;
lifecycle commands stay uniform / no marker guard; marker is a fixed constant)
are treated as settled inputs.

## Decision 1 — Where the marker is applied (host-side, Python layer)

**Decision**: Apply and read the marker in the Python provider layer
(`providers/incus.py`, `providers/proxmox.py`), reusing the existing per-host
SSH helpers `_ssh_run_on_incus_host(host, user, cmd)` and `_ssh_run(host, user,
cmd)`. Not in Ansible.

**Rationale**:
- `update` MUST apply the marker (FR-004, the backfill path), but `update`'s
  `*_configure.yml` playbook connects to the **container's IP**, not the
  hypervisor host — it has no host connection to run `incus config set` / `pct
  set`. The Python provider layer is the only place that holds a host/node SSH
  context at *both* `create` and `update` time (it already uses it for
  `_resolve_vmid`, `_resolve_container_ip`, and all snapshot operations).
- Keeping the marker literal in Python (`core/config.py`) gives the single,
  fixed, cross-provider source of truth the clarification requires, instead of
  threading a value through Ansible extra-vars.
- Localhost Incus is handled transparently: `_ssh_run_on_incus_host` already
  runs `bash -c <cmd>` when `host == "localhost"` (Edge Case: localhost parity).

**Alternatives considered**:
- *Apply in the Ansible create role* (`incus config set ... user.remo=true`,
  `pct set ... --tags`): works for `create` but not for `update` (no host
  connection there), so it would need a second mechanism anyway. Rejected —
  duplicates the marker literal across Ansible + Python and splits the logic.
- *Record marker state in the local registry*: explicitly out of scope; the
  registry stays connection-only and the provider side stays authoritative.

## Decision 2 — Incus marker: `user.remo=true` config key, single-query listing

**Decision**: Marker is the config key `user.remo` with value `true`.
- **Apply** (`create` + `update`): `incus config set <name> user.remo=true`.
- **List for sync**: one query `incus list -f csv -c n,user.remo` yields
  `<name>,<marker-value>` rows. A row whose second column is `true` is marked;
  empty is unmarked. This single bulk query serves BOTH the default filtered
  path (keep rows where marker == `true`) and the `--all` path (register all
  rows, and count rows where marker != `true` for the FR-009 summary).

**Rationale**:
- `user.*` is the Incus namespace reserved for user metadata — cannot collide
  with Incus's own keys (Edge Case: config key collision).
- Setting an already-present identical key is a true no-op — idempotent by
  construction (FR-002), satisfying SC-005 without a read-before-write.
- One column-augmented list call satisfies FR-013 (bounded, single query, no
  per-container round-trip) and gives the `--all` summary its unmarked count for
  free. Incus/LXD `list -c` accepts arbitrary config keys as columns by name.

**Alternatives considered**:
- *Server-side filter* `incus list user.remo=true -f csv -c n`: clean for the
  default path, but `--all` still needs the full list AND the marked set to
  compute the unmarked count → two queries. The column form gets both from one.
  Kept as a documented fallback if the column form misbehaves on an older Incus.
- *A `user.remo.*` sub-namespace or JSON blob*: over-engineered; a single
  boolean-valued key matches the AWS `remo=true` analog exactly.

## Decision 3 — Proxmox marker: `remo` guest tag, bulk conf read, tag-set union

**Decision**: Marker is the guest tag `remo` (a bare tag, matching Proxmox's
set-of-strings tag model).
- **Apply** (`create` + `update`): read the current tag set from `pct config
  <vmid>` (the `tags:` line), and if `remo` is absent, write the **union** back
  with `pct set <vmid> --tags "<existing;...;remo>"`. If `remo` is already
  present, skip the write entirely (guaranteed no reorder / no-op).
- **List for sync**: keep the existing `pct list` for the vmid/name inventory,
  and add ONE bulk tag read: `grep -H '^tags:' /etc/pve/lxc/*.conf`, which maps
  each `<vmid>.conf` to its tag line in a single SSH round-trip. Containers with
  no `tags:` line are simply absent from the map → treated as unmarked. Marked =
  `remo` ∈ tag set for that vmid.

**Rationale**:
- Proxmox tags are a set; applying the marker as a union preserves all
  pre-existing user tags and never removes/reorders them (FR-003, Edge Case:
  container already carries user tags). Skipping the write when `remo` is present
  keeps re-application a strict no-op (FR-002 / SC-005).
- Reading tags from `/etc/pve/lxc/*.conf` in one `grep` is bulk and bounded
  (FR-013) and is consistent with the existing snapshot code, which already
  reads `/etc/pve/lxc/<vmid>.conf` over SSH. No per-container `pct config` loop.
- Tag separator: Proxmox stores tags separated by `;` (and accepts `;`, `,`, or
  space on input). We split on `[;, ]+` when reading and join with `;` on write.

**Alternatives considered**:
- *`pvesh get /nodes/<node>/lxc --output-format json`* (includes `tags` per CT
  in one call): also bulk, but requires the cluster node name and reworks the
  established `pct list` + conf-file parsing the provider already relies on.
  Rejected as a larger, higher-risk change for no functional gain.
- *Per-container `pct config <vmid> | grep tags`*: violates the spirit of
  FR-013 (round-trips scale with unrelated containers). Rejected.

## Decision 4 — Fixed marker constants live in `core/config.py`

**Decision**: Add three module-level constants to `src/remo_cli/core/config.py`:
`INCUS_MANAGED_CONFIG_KEY = "user.remo"`, `INCUS_MANAGED_CONFIG_VALUE = "true"`,
`PROXMOX_MANAGED_TAG = "remo"`. Both providers import from here.

**Rationale**: `core/config.py` is the existing shared, provider-agnostic config
module. A single definition site enforces the "stable, namespaced, consistent
across both providers" requirement and the clarified "fixed, not user-
configurable" decision. No env var or option reads override them.

**Alternatives considered**: a new `core/managed_marker.py` module — unnecessary
for three constants and two tiny helpers that already live with their provider.

## Decision 5 — `--all` flag surface and the skip hint

**Decision**:
- Add `--all` as a Click `is_flag` option on `remo incus sync` and `remo proxmox
  sync`, threaded to `providers.*.sync(all=<bool>)`.
- Default (filtered) `sync` that skips ≥1 unmarked container prints, via
  `print_info`/`print_warning`, a hint that **names the skipped containers**,
  states the count, and lists both remedies: `--all` (one-time adoption) and
  `remo <provider> update <name>` (permanent mark). (FR-008, clarified to
  include names.)
- `--all` that registers ≥1 unmarked container prints a summary distinguishing
  the unmarked count and warning about the round-trip drop on the next default
  sync (FR-009 + Edge Case: mixed-marker host with `--all`).

**Rationale**: `--all` mirrors the existing boolean flag ergonomics used across
the CLI (`--use-ip`, `--yes`). Naming skipped containers makes the `update
<name>` remedy directly actionable (clarification 1). Lifecycle commands are
**not** touched — per clarification 2 they operate uniformly on any registry
entry with no marker guard.

**Alternatives considered**: a positive `--managed-only` default-on flag pair —
rejected; `--all` as an explicit opt-out reads better and matches the spec's
language exactly.
