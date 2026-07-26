# Phase 0 Research: Unified Sync Reconcile

**Feature**: `016-sync-reconcile` | **Date**: 2026-07-25

All findings below were verified against the working tree. File:line references are current as of branch `016-sync-reconcile`.

## R1: Can the existing registry API express a single atomic reconcile?

**Decision**: Yes — reuse `mutate_registry()` unchanged. No new core write path.

**Rationale**: `core/registry.py:794-807` already provides exactly the primitive the spec's FR-006 needs:

```python
def mutate_registry(mutator: Callable[[list[KnownHost]], list[KnownHost]]) -> RegistryView:
    with registry_lock():
        _migrate_locked()
        current = _current_document_locked()
        new_hosts = mutator(list(current.hosts))
        validate_hosts(new_hosts)
        _write_v2_file(get_registry_path(), new_hosts, current.unknown_raw)
```

The mutator runs *inside* the `fcntl` exclusive lock (`registry.py:629-666`, sidecar `registry.lock`, 5s timeout → `RegistryBusyError`), receives the state read under that same lock, and its return value is validated before `_atomic_write_text` does `os.replace` (`registry.py:495-505`). Unknown-type entries are preserved verbatim. That satisfies "one atomic registry rewrite" with zero core changes.

**Constraints this imposes on the design** (all load-bearing):

1. **The lock is not reentrant.** A mutator that calls `read_registry`, `save_known_host`, `remove_known_host`, or `replace_registry` will block on itself for 5s and then raise `RegistryBusyError`. The reconcile mutator must be pure list-in/list-out.
2. **Nothing slow may happen inside the mutator.** Concurrent `remo` commands time out after 5s, and the web service's setup-apply path takes the same lock (`web/api/setup.py:344`). All discovery and all prompting must happen outside.
3. **The mutator cannot return side-channel data** — `mutate_registry` returns only a `RegistryView`. The applied plan is therefore computed before the call and closed over.

**Alternatives considered**:
- *`replace_registry(hosts, allow_empty=...)`* — rejected. It writes a whole-registry snapshot, so an out-of-scope entry added by a concurrent process between our read and our write would be silently destroyed. That is the very class of bug this feature exists to remove.
- *A new `reconcile_registry()` in `core/registry.py`* — rejected as unnecessary. The scope/diff logic belongs in a provider-agnostic layer above the registry, not inside the format accessor. `core/registry.py`'s docstring (lines 8-9) also forbids it raising `SystemExit`, and keeping reconcile out of it preserves that boundary.

## R2: How to prompt outside the lock without applying a stale plan

**Decision**: Snapshot → plan → render → prompt → `mutate_registry` with a mutator that **re-derives the in-scope slice from its own fresh snapshot and aborts if it moved**.

**Rationale**: The spec's concurrency edge case requires that "the plan a user confirmed must be the plan that gets written, or the write must fail loudly." Since the prompt cannot be held inside the lock (R1 constraint 2), the only correct construction is optimistic concurrency: record the exact in-scope entry set the plan was built from, and inside the mutator compare it against what is actually there. If they differ, raise `ReconcileConflictError` — the write does not happen, and the user is told to re-run.

Out-of-scope entries are deliberately *excluded* from the conflict check: a concurrent `remo aws sync --region eu-central-1` must not invalidate an in-flight `--region us-west-2` reconcile. This is what makes scoping more than cosmetic.

**Alternatives considered**:
- *Prompt inside the lock* — rejected; a user who walks away from the prompt bricks every other `remo` invocation for the duration.
- *Last-write-wins* — rejected; silently applies a plan the user never saw.

## R3: Enumeration completeness (FR-040)

**Decision**: The provider probe returns an explicit `complete: bool`. Removals are computed only when it is `True`.

**Rationale**: This is the sharpest latent bug in the reconcile design, and it is already live in one provider:

- **Hetzner truncates at 25 servers today.** `providers/hetzner.py:396` builds `https://api.hetzner.cloud/v1/servers?label_selector=remo` by hand with no `page`/`per_page`. Hetzner's `GET /v1/servers` defaults to `per_page=25` (max 50) and reports `meta.pagination.next_page`. Nothing in the file reads `meta`. Under clear-then-repopulate this silently lost entries; under reconcile it would silently propose deleting them.
- **AWS paginates nowhere.** `providers/aws.py:721-731` (sync), `:185-204` (`_get_running_instance`), `:664-680` (`_find_remo_instance`) each issue a single `describe_instances`. The default page size is 1000, so this is latent rather than live — but it is the same defect.
- **Incus and Proxmox are complete by construction.** `incus list -f csv` and `pct list` have no pagination.

**Design consequence**: completeness is *reported by the provider*, never inferred by the reconcile layer. A provider that cannot prove completeness reports `False` and therefore can never produce a removal. Incus/Proxmox hardcode `True`; AWS and Hetzner set it from whether the paginator ran to exhaustion.

**Chosen idioms**:
- AWS: `ec2.get_paginator("describe_instances").paginate(Filters=...)`. A mid-iteration exception yields the pages collected so far with `complete=False` rather than aborting — additions still apply, removals are suppressed.
- Hetzner: a new `_hetzner_api_paged(path, key)` looping on `meta.pagination.next_page` with `per_page=50`.

## R4: Migrating Hetzner onto the existing API helper

**Decision**: Route the new probe through `_hetzner_api()` (`providers/hetzner.py:446-485`), extending it with the paged wrapper.

**Rationale**: The Hetzner provider has two parallel HTTP implementations. `_hetzner_api()` — added with the snapshot feature — does proper `HTTPError`/`URLError` handling and raises `RuntimeError` with a useful message. The older inline `urllib.request` blocks in `sync()` (`:396-407`), `info()` (`:327-365`) and `_query_hetzner_server_ip()` (`:44-75`) duplicate it badly; `_query_hetzner_server_ip` swallows every error and returns `""`.

Consolidating also unlocks testing: `tests/unit/providers/test_hetzner_snapshot.py` already has an `api` fixture that patches `_hetzner_api`, which becomes the seam for the first-ever Hetzner sync tests.

**Scope boundary**: migrate `sync`'s probe now (required). `info()` and `_query_hetzner_server_ip()` are opportunistic and out of scope unless free.

## R5: Silent failure modes that must become loud (FR-009)

**Decision**: Every probe path must distinguish "provider says there is nothing" from "we failed to ask."

Three concrete offenders found:

1. **Proxmox tag reads never check the return code.** `providers/proxmox.py:145-180` (`_read_tags_by_vmid`) runs a remote `cat` loop and parses `result.stdout` without inspecting `result.returncode`. An SSH failure yields an empty tag map → every container reads unmarked → a default sync registers **zero** containers, prints "Skipped N unmarked container(s)", and (today) has already wiped the node's entries. This is a false negative masquerading as a successful sync.
2. **Incus IP resolution hard-exits mid-loop.** `providers/incus.py:113,117` — `_resolve_container_ip` calls `sys.exit(1)` on SSH failure. During a `--use-ip` sync this fires *after* `clear_known_hosts_by_prefix` at `:627`, leaving a partially-populated registry and no error recovery. Under the new design it must return `""` softly so FR-041's merge preserves the previously recorded address.
3. **Neither incus SSH helper catches `FileNotFoundError`** for a missing `ssh`/`bash` binary in `_ssh_run_on_incus_host` (`incus.py:718-738`) — it escapes as an uncaught traceback.

**Rationale**: Under clear-then-repopulate these produced wrong-but-quiet output. Under reconcile they produce *proposed deletions*, so they graduate from cosmetic to dangerous. The probe contract makes the distinction explicit: raise `ProbeError` (→ exit 1, registry untouched) or return `complete=False` (→ removals suppressed).

## R6: Hetzner label application — Ansible vs. Python

**Decision**: Apply the label in **Ansible at create time**, and backfill in **Python at update time**. Label the server only.

**Rationale**: `grep -rn "labels" ansible/` returns zero hits, confirming the spec's core finding — `sync()`'s `label_selector=remo` matches nothing remo creates.

For **create**, the label belongs in the existing task at `ansible/roles/hetzner_server/tasks/main.yml:55-69`, as a sibling key of `state: present`. `hetzner.hcloud.server` (pinned `>=6.7.0` in `ansible/requirements.yml`) diffs labels and issues a `PUT` when they differ, so it is idempotent — satisfying FR-033.

For **backfill**, `hetzner.hcloud.server` and the raw `PUT /v1/servers/{id}` both treat the supplied label map as **authoritative and replace it wholesale**. That directly violates FR-034 ("without disturbing any other labels"). So backfill must read-merge: `_get_server_by_name` → merge `{"remo": "true"}` into the existing map → `PUT`. This also matches the established `_apply_managed_marker` precedent (`incus.py:137-153`, `proxmox.py:115-142`): return `(ok, err)`, never raise, never exit, and the caller warns-and-continues (`incus.py:421-428`).

A further reason to keep backfill in Python: `ansible/hetzner_configure.yml:17-20` is a `hosts: all` play over SSH with no `localhost` tasks, so an Ansible backfill would require a new prepended play.

**Scope decision — label the server only.** Not the volume, firewall, or SSH key:
- `hetzner.hcloud.ssh_key` (`main.yml:28-36`) is **shared across servers** (default `remote-coding-key`, `defaults/main.yml:7`), so a per-server label is meaningless.
- `ansible/hetzner_resize.yml:60-66` re-asserts the volume with `state: present` and no `labels:` key. If volumes were labeled, a resize could strip the label. Avoiding that trap is worth more than the marginal benefit.
- `sync`'s selector only ever needs the server label.

**Label chosen**: `remo: "true"`, matching the existing `label_selector=remo` query and the snapshot code's `remo` key (`hetzner.py:580-585`). Note the codebase is inconsistent about separators — snapshot labels use hyphens (`remo-snapshot-name`), AWS tags use underscores (`remo_resource_name`) — so a single-key `remo` label sidesteps the question entirely.

## R7: Adoption criteria per provider (FR-030)

**Decision**:

| Provider | Default membership | `--all` widens to |
|---|---|---|
| Incus | `user.remo=true` config key | every container on the host |
| Proxmox | `remo` tag | every container on the node |
| AWS | `tag:remo=true` | additionally `tag:Name` matching `remo-*` |
| Hetzner | `remo` label | every server in the project |

**Rationale**: AWS has a real naming convention to lean on — `aws_instance_name: "remo-{{ aws_resource_name }}"` (`ansible/roles/aws_server/defaults/main.yml:5`), already queried as an exact filter at `aws.py:671`. `Values` supports `*` wildcards, so `remo-*` is a legitimate server-side filter.

Hetzner has **no** prefix convention — `hetzner_server_name` defaults to the literal `"remo"` (`defaults/main.yml:3`) and `--name alice` produces a server named `alice`. There is nothing to pattern-match, so Hetzner's `--all` must mean "every server in the project." FR-030 requires stating this in the output, which makes the bluntness visible rather than surprising.

**Alternatives considered**: inferring Hetzner membership from a paired `<name>-home` volume or `<name>-firewall` — rejected; costs two extra API calls and infers management from a naming coincidence.

## R8: AWS name derivation

**Decision**: Prefer the `remo_resource_name` tag; fall back to `Name` minus the `remo-` prefix.

**Rationale**: `aws.py:740-751` currently reverse-engineers the registry name by string-stripping the `Name` tag, ignoring `remo_resource_name` — which the Ansible role *does* set authoritatively (`ansible/roles/aws_server/tasks/ec2.yml:80,122`). The current approach misbehaves on an instance tagged `remo=true` whose `Name` lacks the prefix: `removeprefix` is a no-op, so it is registered under its full `Name`. Reading the authoritative tag first is strictly better and costs nothing — the tag is already in the response.

This matters more under reconcile than before, because the derived name is now the **matching key** (FR-039). An unstable name derivation would manifest as spurious remove-plus-add pairs.

## R9: Exit codes

**Decision**: `0` applied/no-op · `1` failure · `3` aborted without change. `2` is reserved.

**Rationale**: Click exits `2` for usage errors, and `cli/shell.py:78,81,88` already follows that convention. Reusing `2` for "user declined" would make a mistyped flag indistinguishable from a deliberate refusal, defeating SC-013. Confirmed with the user; FR-043 was amended accordingly.

**Plumbing consequence**: all four `sync()` functions currently return `None` (`incus.py:600`, `proxmox.py:731`, `hetzner.py:383`, `aws.py:707`) and none of the four CLI wrappers calls `sys.exit` (`cli/providers/incus.py:203`, `proxmox.py:230`, `hetzner.py:129-134`, `aws.py:125-131`) — so sync always exits 0 today unless a provider hard-exits. They must return `int` and the wrappers must `sys.exit(rc)`, matching the established pattern already used by create/destroy/update (`cli/providers/incus.py:157-168`).

## R10: Non-interactive detection

**Decision**: `sys.stdin.isatty()`, checked by the reconcile driver.

**Rationale**: `core/output.py:38-56`'s `confirm()` has no TTY awareness — under closed stdin it hits `EOFError` and returns `default` (i.e. `False`), which is safe but indistinguishable from a deliberate "no". FR-014 requires a *specific* message and exit code for the non-interactive case, so the check must happen before prompting. The established convention is `sys.stdin.isatty() and not assume_yes`, used at `core/web_adopt.py:1117,1232`.

## R11: Test seams

**Decision**: Build on `tmp_config_dir` and the existing per-module patch points.

- `tests/conftest.py:10-21` — `tmp_config_dir` sets `REMO_HOME` and yields the dir; `registry.json` and `registry.lock` land inside it. This is what makes SC-008's "real temporary registry" testable, replacing today's practice of mocking the destructive step out entirely.
- Companion helpers in the same file: `build_v2_host_entry` (`:86`), `write_v2_registry` (`:105`).
- Patch points, by convention on the *provider module's* symbol: `remo_cli.providers.incus._ssh_run_on_incus_host` (`tests/unit/providers/test_incus_marker.py:26-30`), `remo_cli.providers.proxmox._run_on_node`, `remo_cli.providers.hetzner._hetzner_api` (`test_hetzner_snapshot.py`), and the `ec2` client fixture in `test_aws_snapshot.py`.

**Known breakage to budget for**:
- `tests/unit/providers/test_provider_registry_entries.py` pins exact `KnownHost` shapes per provider (e.g. `"aws:buildbox:203.0.113.7:remo:i-abc:ssm"` at `:217`). Any change to what sync writes breaks it.
- `tests/unit/cli/providers/test_incus_sync_all.py:21` and `test_proxmox_sync_all.py` assert `exit_code == 0` against a `return_value=None` mock; changing `sync` to return `int` breaks them.
- Switching AWS to `get_paginator` requires every `ec2` stub in `tests/unit/providers/test_aws_snapshot.py` to grow a `get_paginator` stub.

## R12: Out-of-scope observations

Recorded because they were found during research, **not** proposed for this feature:

- `pyproject.toml:12-19` lists `boto3` and `hcloud` as unconditional core dependencies, but `CLAUDE.md:152-153,185` documents `--extra aws` / `--extra hetzner` extras and lazy SDK imports that no longer exist. Also, `hcloud` is imported nowhere in `src/remo_cli/` — the Hetzner provider uses raw `urllib`.
- `ansible/group_vars/all.yml:5` sets `hetzner_server_type: "cx23"` while `roles/hetzner_server/defaults/main.yml:4` says `"cx22"` and the CLI summary prints `cx22` (`hetzner.py:157`). group_vars wins, so the printed summary is wrong. Same pattern for volume size (20 vs 10).
- `remo incus create` and `remo proxmox create` declare `--yes` but never pass it through (`cli/providers/incus.py:55`, `proxmox.py:62`) — an accepted-and-ignored flag.
- `aws.py:614-624` (`update`) hardcodes `access_mode="ssm"` when rewriting the registry entry, ignoring the instance's actual `remo_access_mode` tag.
- Several other unpaginated AWS calls exist (`describe_snapshots` at `aws.py:1132`, `describe_volumes` at `:1018`, IAM listings at `:245,256`). `describe_snapshots` with `OwnerIds=["self"]` is the one most likely to exceed a page in practice.
