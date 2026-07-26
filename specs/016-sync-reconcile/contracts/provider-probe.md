# Contract: provider probe interface

**Feature**: `016-sync-reconcile`

The internal seam between `core/reconcile.py` and the four providers. A provider's *only* contribution to sync is one function conforming to this contract (FR-002, SC-005).

## Signature

```python
ProbeFn = Callable[[], ProbeResult]
```

The driver receives a zero-argument thunk; each provider closes over its own scope and flags:

```python
def sync(host="localhost", user="", use_ip=False, include_all=False,
         auto_confirm=False, dry_run=False) -> int:
    scope = SyncScope(type="incus", host=host)
    return run_sync(
        scope,
        lambda: _probe(scope, user=user, use_ip=use_ip, include_all=include_all),
        auto_confirm=auto_confirm,
        dry_run=dry_run,
    )
```

That is the entire body of each provider's `sync`. All reporting, diffing, consent, and writing lives in `run_sync`.

## Obligations

A conforming probe **MUST**:

1. **Return every host it can see in scope** — marked and unmarked alike. Filtering unmarked hosts out of the result is the bug this feature removes: presence is what protects an existing entry from removal (FR-022), independently of eligibility for addition (FR-021).
   **The marker must not narrow the query itself** (FR-044). Only conditions establishing genuine non-existence — a terminated instance — may be pushed server-side. A marker-filtered query cannot distinguish "deleted" from "lost its tag", so it would propose deleting a host that is sitting right there.
2. **Set `marked` per host** from the provider's managed marker: `user.remo=true` (Incus), the `remo` tag (Proxmox), `tag:remo=true` (AWS), the `remo` label (Hetzner).
3. **Report `complete` honestly** (FR-040). `True` only if the enumeration is provably exhaustive. Anything less sets `False` with an `incomplete_reason`, and the reconcile layer then produces no removals at all.
4. **Raise `ProbeError` when it could not ask** (FR-009). Never return an empty `ProbeResult` to mean "the query failed." A probe that cannot distinguish these two must report `complete=False`.
5. **Mutate nothing at the provider** (FR-008) — no create, destroy, start, stop, tag, label, or reconfigure. Markers are written only by `create` and `update`.
6. **Build `entry` in the same shape the provider's `create` writes**, so create and sync agree and a freshly created host reconciles as `unchanged`.
7. **Degrade softly on per-host problems.** One container's IP lookup failing must append to `warnings` and leave `entry.host` empty, so `merge_entry` preserves the previously recorded address. It must not abort the run and must not drop the host from `hosts` — dropping it would turn a transient failure into a proposed deletion.
8. **Set `state`** to the observed provider state when the provider has one, `""` otherwise. Reported, never persisted (FR-019).
9. **Set `adoption_criteria`** to a human-readable description of what `include_all` widened to (FR-030).

A conforming probe **MUST NOT** call `save_known_host`, `remove_known_host`, `clear_known_hosts_*`, `mutate_registry`, or `replace_registry`. Registry writes belong exclusively to the reconcile driver.

## Per-provider implementation notes

### Incus — `providers/incus.py`

- Source: `_list_containers_with_marker(host, user)` (`incus.py:156-180`), one `incus list -f csv -c n,user.remo`. This already enumerates every container and evaluates the marker locally, so it satisfies FR-044 as written.
- `complete = True` — `incus list` does not paginate. Default project only; `--all-projects` is deliberately not added (FR-045), so the scope description must name the boundary: `incus host <h> (default project)`.
- Existing `RuntimeError` on non-zero return code maps to `ProbeError`.
- `--use-ip`: `_resolve_container_ip` must be changed to return `""` on SSH failure instead of `sys.exit(1)` (`incus.py:113,117`), appending to `warnings`. Also catch `FileNotFoundError` from the SSH helper.
- Entry: `name=f"{host}/{c}"`, `host=ip or c`, `user="remo"`, `instance_id=<node ssh user>`, `access_mode="direct"`.

### Proxmox — `providers/proxmox.py`

- Sources: `pct list` (`proxmox.py:756-780`) and `_read_tags_by_vmid` (`proxmox.py:145-180`).
- **Must add a return-code check to `_read_tags_by_vmid`.** It currently ignores `returncode`, so an SSH failure silently yields an empty tag map, every container reads unmarked, and a default sync registers zero. Under reconcile that would propose deleting the node's entire fleet. Non-zero → `ProbeError`.
- `complete = True` — neither source paginates. Addressed node only; the scope description must say so (FR-045): `proxmox node <h> (this node only)`.
- Entry: `name=f"{host}/{hostname}"`, `instance_id=vmid`, `access_mode="direct"`, `region=user or "root"`.

### AWS — `providers/aws.py`

- Replace the single-shot `describe_instances` (`aws.py:721-731`) with `ec2.get_paginator("describe_instances").paginate(...)`.
- **Filter on state only** — never on `tag:remo` (FR-044). Enumerate every non-terminal instance in the region and set `marked` from `tags.get("remo") == "true"` locally. This is one broader paginated call rather than several narrow ones, and it is the only way an untagged-but-live instance can be retained instead of deleted.
- `include_all` widens **eligibility for addition** to instances whose `tag:Name` matches `remo-*`. It does not change the query.
- **States**: `pending`, `running`, `stopping`, `stopped` (FR-017) — matching the `states=` list every other AWS command already passes to `_find_remo_instance` (`aws.py:783,837,928,977`). Exclude `shutting-down` and `terminated`.
- `complete = True` only if the paginator ran to exhaustion. A mid-iteration exception returns the pages gathered so far with `complete=False` — additions still apply, removals are suppressed.
- Name: prefer the `remo_resource_name` tag; fall back to `Name` minus `remo-` (R8). Skip an instance that yields an empty name.
- Entry: `host=<public ip>` or `""` when absent (**not** the instance id — the merge preserves the prior address, FR-018), `instance_id=<id>`, `access_mode=tags.get("remo_access_mode", "ssm")`, `region=<scope region>`.
- `state = instance["State"]["Name"]`.

### Hetzner — `providers/hetzner.py`

- Route through `_hetzner_api` (`hetzner.py:446-485`), not the inline `urllib` block at `:396-407`.
- **Must paginate.** `GET /v1/servers` defaults to `per_page=25`; today's sync silently truncates there. Add `_hetzner_api_paged(path, key)` looping on `meta.pagination.next_page` with `per_page=50`. `complete=False` if the loop terminates early.
- **Never use `?label_selector=remo`** (FR-044). Always `GET /v1/servers` unfiltered and set `marked` from the presence of the `remo` label locally. The existing selector is precisely what makes an unlabelled-but-live server look absent.
- `include_all` widens **eligibility for addition** to every server in the project — Hetzner has no naming convention to match on (research R7). It does not change the query.
- `complete = True` only when pagination ran to exhaustion.
- Entry: `name=<server name>`, `host=<public IPv4>`, `user="remo"`, others empty — matching what `create` writes (`hetzner.py:141-148`).
- A server with no IPv4 still belongs in `hosts` with `entry.host=""`; the merge preserves the prior address rather than dropping the entry.
- `state = server["status"]`.

## Test seam

Each probe is independently testable by patching one symbol:

| Provider | Patch target |
|---|---|
| Incus | `remo_cli.providers.incus._ssh_run_on_incus_host` |
| Proxmox | `remo_cli.providers.proxmox._run_on_node` |
| AWS | the `ec2` client fixture (must now stub `get_paginator`) |
| Hetzner | `remo_cli.providers.hetzner._hetzner_api` |

`build_plan` is pure, so the whole classification matrix is testable with no provider at all.
