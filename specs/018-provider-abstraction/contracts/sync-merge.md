# Contract: Sync-Query Merge Semantics (observed vs default) — closes #87

Amends the Spec-016 probe contract (`specs/016-sync-reconcile/contracts/provider-probe.md`); everything not stated here is unchanged (FR-020).

## Change

`DiscoveredHost` gains:

```python
observed: frozenset[str] | None = None
```

- `None` (default) — **legacy semantics**: every non-empty field counts as observed. Existing Incus/Proxmox/Hetzner probes need no change.
- A set — only the named fields were genuinely read from the provider; other fields carry defaults/fillers.

## Merge rule (`merge_entry`)

For each mergeable field `f` of an **existing** entry:

```
take discovered.f  iff  f is observed AND discovered.f is non-empty
else keep existing.f
```

**Additions** (host discovered, no existing entry): the discovered entry is used wholesale, defaults included — new adoption always yields a working entry.

## Provider obligations

- AWS probe: `access_mode` ∈ `observed` **iff** the `remo_access_mode` tag is present on the instance. The value remains `tags.get("remo_access_mode", "ssm")` so additions still default to `ssm`.
- Any future provider that fills a field it did not read MUST exclude it from `observed`.

## Acceptance (US5)

1. Existing entry `access_mode="ssh"`, instance untagged → sync: entry preserved, **no** `~ updated` line.
2. Instance tagged `remo_access_mode=ssm`, entry says `ssh` → sync updates to `ssm` (observed wins).
3. Untagged new instance adopted via `--all` → entry created with `access_mode="ssm"`.
4. Plan idempotence: two consecutive syncs with no provider-side changes produce an empty second plan.
