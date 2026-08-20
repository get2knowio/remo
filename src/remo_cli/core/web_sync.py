"""`remo web sync` — bi-directional registry sync with a three-way merge (023).

Where `remo web push` (deprecated, one-way force) overwrites the deployment's
registry wholesale, sync computes an entry-level three-way merge:

* **base**  — the full hostEntry each instance had at the last successful
  push/sync, from push-cache v4 (`CachedInstance.entry`). A missing base
  degrades that name to a base-less two-way compare (identical entries adopt
  silently; divergent ones surface as conflicts) — safe, never an error.
* **local** — this workstation's registry.
* **remote** — the service's registry via the new `GET /setup/registry`.

Entry-level, not field-level, by design: a connection tuple's fields are
mutually dependent (host/user/port/identity move together), so a field-spliced
entry nobody ever tested is worse than a prompt. Field granularity appears
only in conflict *rendering*.

Keyed by **name** — consistent with host_keys, the push cache, and drift, all
name-keyed. The registry legally permits the same name under two types
((type, name) uniqueness): a cross-type name collision within either side
aborts with exit 1 naming the entries (rare, safe, honest). A same-name type
change *between* sides falls through the table as an ordinary unequal entry.

Equality is `registry.canonical_entry` — the exact string
`instance_fingerprint` hashes, so cache, drift and merge agree by
construction.

Concurrency: the PUT carries payload v3's `base_generation` precondition; a
409 `generation_conflict` re-GETs, re-merges against the SAME base (memoized
resolutions — only new conflicts re-prompt) and retries, bounded at 3.

Like `core/web_adopt.py` this module is stdlib + core/models only — it MUST
stay importable without the `web` extra.

Exit codes (the driver returns them; `cli/web.py` does `sys.exit`):
0 = success (including --dry-run), 1 = hard failure, 3 = user-aborted /
unresolved without consent. Never 2 (the repo-wide convention).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from remo_cli.core import registry
from remo_cli.core.output import (
    GREEN,
    NC,
    RED,
    YELLOW,
    confirm,
    print_error,
    print_info,
    print_success,
    print_warning,
)
from remo_cli.core.web_adopt import (
    OUTCOME_PULLED,
    OUTCOME_UNCHANGED,
    AdoptError,
    CachedInstance,
    GenerationConflictError,
    InstanceOutcome,
    MountConfiguredError,
    RevocationOutcome,
    SetupApiClient,
    SetupApiError,
    SYNC_PAYLOAD_VERSION,
    _MOUNT_CONFIGURED_MSG,
    _cache_from_outcomes,
    _end_session_best_effort,
    _persist_confirmed_host_keys,
    _process_instance,
    _render_fingerprints,
    _repair_auth_failures,
    _update_push_cache,
    _workstation_label,
    auth_failed_labels,
    build_adoption_payload,
    instance_fingerprint,
    is_direct_access,
    load_push_cache,
    open_via_tunnel,
    render_revocations,
    render_summary,
    render_verification,
    revoke_service_key,
)
from remo_cli.models.host import KnownHost

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ABORTED = 3

_MAX_PUT_ATTEMPTS = 3


class SyncNameCollisionError(AdoptError):
    """A name exists under two types on one side — name-keyed merge is
    ambiguous for it (exit 1)."""


class SyncLocalConflictError(AdoptError):
    """The local registry changed between planning and applying (CAS)."""


class SyncActionKind(str, Enum):
    PUSH_ADD = "push_add"
    PUSH_UPDATE = "push_update"
    PULL_ADD = "pull_add"
    PULL_UPDATE = "pull_update"
    DELETE_REMOTE = "delete_remote"
    DELETE_LOCAL = "delete_local"
    IN_SYNC = "in_sync"
    CONFLICT = "conflict"
    BOTH_DELETED = "both_deleted"


#: resolution values for a CONFLICT action.
RESOLUTION_LOCAL = "local"
RESOLUTION_REMOTE = "remote"
RESOLUTION_SKIP = "skip"


@dataclass
class SyncAction:
    name: str
    kind: SyncActionKind
    local: KnownHost | None = None
    remote: KnownHost | None = None
    base: dict[str, Any] | None = None
    #: For CONFLICT only: "local" | "remote" | "skip" | None (unresolved).
    resolution: str | None = None


@dataclass
class SyncPlan:
    actions: list[SyncAction]
    remote_generation: int
    #: Canonical strings of the local registry at plan time (the CAS baseline).
    local_baseline: frozenset[str] = frozenset()

    @property
    def conflicts(self) -> list[SyncAction]:
        return [a for a in self.actions if a.kind is SyncActionKind.CONFLICT]

    @property
    def is_noop(self) -> bool:
        return all(
            a.kind in (SyncActionKind.IN_SYNC, SyncActionKind.BOTH_DELETED)
            for a in self.actions
        )


# ---------------------------------------------------------------------------
# Pure merge
# ---------------------------------------------------------------------------


def _index_by_name(hosts: list[KnownHost], side: str) -> dict[str, KnownHost]:
    by_name: dict[str, KnownHost] = {}
    for host in hosts:
        existing = by_name.get(host.name)
        if existing is not None:
            raise SyncNameCollisionError(
                f"the {side} registry has '{host.name}' under two types "
                f"({existing.type} and {host.type}); the name-keyed sync merge "
                "cannot represent that. Rename one of the entries "
                "(remove + re-add), then re-run `remo web sync`."
            )
        by_name[host.name] = host
    return by_name


def build_sync_plan(
    base: dict[str, dict[str, Any] | None],
    local: list[KnownHost],
    remote: list[KnownHost],
    remote_generation: int,
) -> SyncPlan:
    """The pure three-way merge (module docstring case table). No I/O."""
    local_by_name = _index_by_name(local, "local")
    remote_by_name = _index_by_name(remote, "deployment's")

    actions: list[SyncAction] = []
    for name in sorted(set(base) | set(local_by_name) | set(remote_by_name)):
        b_entry = base.get(name)
        b = registry.canonical_entry_dict(b_entry) if b_entry is not None else None
        lh = local_by_name.get(name)
        rh = remote_by_name.get(name)
        l_canon = registry.canonical_entry(lh) if lh is not None else None
        r_canon = registry.canonical_entry(rh) if rh is not None else None

        if b is None:
            if lh is not None and rh is None:
                kind = SyncActionKind.PUSH_ADD
            elif lh is None and rh is not None:
                kind = SyncActionKind.PULL_ADD
            elif lh is not None and rh is not None:
                kind = (
                    SyncActionKind.IN_SYNC
                    if l_canon == r_canon
                    else SyncActionKind.CONFLICT
                )
            else:  # pragma: no cover - name came from one of the three sets
                continue
        elif lh is not None and rh is not None:
            if l_canon == b and r_canon == b:
                kind = SyncActionKind.IN_SYNC
            elif l_canon != b and r_canon == b:
                kind = SyncActionKind.PUSH_UPDATE
            elif l_canon == b and r_canon != b:
                kind = SyncActionKind.PULL_UPDATE
            else:
                kind = (
                    SyncActionKind.IN_SYNC
                    if l_canon == r_canon
                    else SyncActionKind.CONFLICT
                )
        elif lh is not None:  # remote deleted
            kind = (
                SyncActionKind.DELETE_LOCAL
                if l_canon == b
                else SyncActionKind.CONFLICT
            )
        elif rh is not None:  # local deleted
            kind = (
                SyncActionKind.DELETE_REMOTE
                if r_canon == b
                else SyncActionKind.CONFLICT
            )
        else:
            kind = SyncActionKind.BOTH_DELETED

        actions.append(
            SyncAction(name=name, kind=kind, local=lh, remote=rh, base=b_entry)
        )

    return SyncPlan(
        actions=actions,
        remote_generation=remote_generation,
        local_baseline=frozenset(registry.canonical_entry(h) for h in local),
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _flatten_entry(entry: dict[str, Any] | None) -> dict[str, str]:
    if entry is None:
        return {}
    flat: dict[str, str] = {}
    for key, value in entry.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}.{sub_key}"] = str(sub_value)
        else:
            flat[key] = str(value)
    return flat


def render_conflict_diff(action: SyncAction) -> list[str]:
    """Per-field local-vs-remote diff lines for one CONFLICT action."""
    local_flat = _flatten_entry(
        registry.known_host_to_entry(action.local) if action.local else None
    )
    remote_flat = _flatten_entry(
        registry.known_host_to_entry(action.remote) if action.remote else None
    )
    lines: list[str] = []
    if not local_flat:
        lines.append("      local:  (deleted)")
    if not remote_flat:
        lines.append("      remote: (deleted)")
    for key in sorted(set(local_flat) | set(remote_flat)):
        left = local_flat.get(key, "(absent)")
        right = remote_flat.get(key, "(absent)")
        if left != right:
            lines.append(f"      {key}: local={left!r}  remote={right!r}")
    return lines


_KIND_GLYPHS: dict[SyncActionKind, str] = {
    SyncActionKind.PUSH_ADD: f"{GREEN}→ push (add){NC}",
    SyncActionKind.PUSH_UPDATE: f"{GREEN}→ push (update){NC}",
    SyncActionKind.PULL_ADD: f"{GREEN}← pull (add){NC}",
    SyncActionKind.PULL_UPDATE: f"{GREEN}← pull (update){NC}",
    SyncActionKind.DELETE_REMOTE: f"{RED}→ delete remote{NC}",
    SyncActionKind.DELETE_LOCAL: f"{RED}← delete local{NC}",
    SyncActionKind.IN_SYNC: "= in sync",
    SyncActionKind.CONFLICT: f"{YELLOW}! conflict{NC}",
    SyncActionKind.BOTH_DELETED: "  both deleted",
}


def render_sync_plan(plan: SyncPlan, deployment_id: str) -> None:
    print()
    print_info(
        f"Sync plan against deployment {deployment_id or 'unknown'} "
        f"(generation {plan.remote_generation}):"
    )
    if not plan.actions:
        print("  (both registries are empty)")
        return
    for action in plan.actions:
        present = action.local or action.remote
        type_ = present.type if present is not None else ""
        label = f"{type_}/{action.name}" if type_ else action.name
        suffix = ""
        if action.kind is SyncActionKind.DELETE_REMOTE:
            suffix = "  (revokes the service key)"
        print(f"  {_KIND_GLYPHS[action.kind]:<28} {label}{suffix}")
        if action.kind is SyncActionKind.CONFLICT:
            for line in render_conflict_diff(action):
                print(line)


# ---------------------------------------------------------------------------
# Conflict resolution + consent
# ---------------------------------------------------------------------------


def resolve_conflicts(
    plan: SyncPlan,
    *,
    prefer: str | None,
    interactive: bool,
    memo: dict[str, str],
    input_fn: Callable[[str], str] | None = None,
) -> bool:
    """Set each CONFLICT's resolution. Returns False when any stays unresolved
    (non-interactive, no --prefer-*) — the caller aborts with exit 3.

    *memo* persists picks across 409 re-merges so only NEW conflicts re-prompt.
    """
    if input_fn is None:
        # Resolved at call time (not as a parameter default) so tests patching
        # builtins.input are honored.
        input_fn = input
    for action in plan.conflicts:
        if action.name in memo:
            action.resolution = memo[action.name]
            continue
        if prefer in (RESOLUTION_LOCAL, RESOLUTION_REMOTE):
            action.resolution = prefer
            memo[action.name] = prefer
            continue
        if not interactive:
            return False
        print()
        print_warning(f"Conflict for '{action.name}':")
        for line in render_conflict_diff(action):
            print(line)
        local_desc = "keep local" if action.local else "keep local (delete remotely)"
        remote_desc = "keep remote" if action.remote else "keep remote (delete locally)"
        while True:
            try:
                answer = (
                    input_fn(f"  [l] {local_desc} / [r] {remote_desc} / [s] skip: ")
                    .strip()
                    .lower()
                )
            except EOFError:
                # Ctrl-D at the prompt is an abort, not a crash: leave the
                # conflict unresolved so the caller takes the documented
                # nothing-was-applied exit-3 path (core/output.confirm treats
                # EOF the same way).
                return False
            if answer in ("l", "local"):
                action.resolution = RESOLUTION_LOCAL
                break
            if answer in ("r", "remote"):
                action.resolution = RESOLUTION_REMOTE
                break
            if answer in ("s", "skip"):
                action.resolution = RESOLUTION_SKIP
                break
        memo[action.name] = action.resolution
    return True


def _deletions(plan: SyncPlan) -> tuple[list[str], list[str]]:
    """(names deleted remotely, names deleted locally) after resolution."""
    remote_deletes: list[str] = []
    local_deletes: list[str] = []
    for action in plan.actions:
        if action.kind is SyncActionKind.DELETE_REMOTE:
            remote_deletes.append(action.name)
        elif action.kind is SyncActionKind.DELETE_LOCAL:
            local_deletes.append(action.name)
        elif action.kind is SyncActionKind.CONFLICT:
            if action.resolution == RESOLUTION_LOCAL and action.local is None:
                remote_deletes.append(action.name)
            elif action.resolution == RESOLUTION_REMOTE and action.remote is None:
                local_deletes.append(action.name)
    return remote_deletes, local_deletes


def gate_deletion_consent(
    plan: SyncPlan,
    *,
    assume_yes: bool,
    interactive: bool,
    consented: set[str],
) -> bool:
    """One prompt listing both deletion directions. Returns False to abort
    (exit 3, nothing applied). *consented* memoizes across 409 re-merges."""
    remote_deletes, local_deletes = _deletions(plan)
    pending = [n for n in (*remote_deletes, *local_deletes) if n not in consented]
    if not pending:
        return True
    if assume_yes:
        consented.update(pending)
        return True
    if not interactive:
        return False
    print()
    if remote_deletes:
        print_warning(
            "Will delete from the DEPLOYMENT (and best-effort revoke its "
            f"service key): {', '.join(sorted(remote_deletes))}"
        )
    if local_deletes:
        print_warning(
            f"Will delete from THIS WORKSTATION's registry: "
            f"{', '.join(sorted(local_deletes))}"
        )
    if not confirm("Apply these deletions?", default=False):
        return False
    consented.update(remote_deletes)
    consented.update(local_deletes)
    return True


# ---------------------------------------------------------------------------
# Plan projection helpers (what each side ends up with)
# ---------------------------------------------------------------------------


def _final_side(action: SyncAction) -> str | None:
    """Which side's state wins in the MERGED target: "local"/"remote"/None.

    None = the name ends deleted (both directions) or dropped (BOTH_DELETED).
    A skipped conflict projects the REMOTE state into the payload (local keeps
    its own copy locally; the cache keeps the old base so it re-surfaces).
    """
    kind = action.kind
    if kind in (SyncActionKind.PUSH_ADD, SyncActionKind.PUSH_UPDATE):
        return "local"
    if kind in (SyncActionKind.PULL_ADD, SyncActionKind.PULL_UPDATE):
        return "remote"
    if kind is SyncActionKind.IN_SYNC:
        return "local"
    if kind is SyncActionKind.CONFLICT:
        if action.resolution == RESOLUTION_LOCAL:
            return "local" if action.local is not None else None
        # remote pick and skip both project the remote state.
        return "remote" if action.remote is not None else None
    return None  # DELETE_*, BOTH_DELETED


def merged_hosts(plan: SyncPlan) -> list[KnownHost]:
    hosts: list[KnownHost] = []
    for action in plan.actions:
        side = _final_side(action)
        if side == "local" and action.local is not None:
            hosts.append(action.local)
        elif side == "remote" and action.remote is not None:
            hosts.append(action.remote)
    return hosts


def local_mutations(plan: SyncPlan) -> tuple[dict[str, KnownHost], set[str]]:
    """(names to set locally -> remote host, names to remove locally)."""
    to_set: dict[str, KnownHost] = {}
    to_remove: set[str] = set()
    for action in plan.actions:
        if action.kind in (SyncActionKind.PULL_ADD, SyncActionKind.PULL_UPDATE):
            assert action.remote is not None
            to_set[action.name] = action.remote
        elif action.kind is SyncActionKind.DELETE_LOCAL:
            to_remove.add(action.name)
        elif (
            action.kind is SyncActionKind.CONFLICT
            and action.resolution == RESOLUTION_REMOTE
        ):
            if action.remote is not None:
                to_set[action.name] = action.remote
            else:
                to_remove.add(action.name)
    return to_set, to_remove


def remote_deletions(plan: SyncPlan) -> list[SyncAction]:
    """Actions whose remote entry must be revoked after the PUT."""
    deletions: list[SyncAction] = []
    for action in plan.actions:
        if action.kind is SyncActionKind.DELETE_REMOTE:
            deletions.append(action)
        elif (
            action.kind is SyncActionKind.CONFLICT
            and action.resolution == RESOLUTION_LOCAL
            and action.local is None
        ):
            deletions.append(action)
    return deletions


def skipped_conflicts(plan: SyncPlan) -> list[SyncAction]:
    return [
        a
        for a in plan.actions
        if a.kind is SyncActionKind.CONFLICT and a.resolution == RESOLUTION_SKIP
    ]


# ---------------------------------------------------------------------------
# Remote registry parsing
# ---------------------------------------------------------------------------


@dataclass
class RemoteRegistry:
    hosts: list[KnownHost] = field(default_factory=list)
    host_keys: dict[str, list[str]] = field(default_factory=dict)
    generation: int = 0
    last_change: dict[str, Any] | None = None


def parse_remote_registry(doc: dict[str, Any]) -> RemoteRegistry:
    """Parse `GET /setup/registry`'s body; unknown-type entries are invisible
    to sync (each side preserves its own verbatim — documented contract)."""
    hosts: list[KnownHost] = []
    raw_entries = doc.get("registry")
    if isinstance(raw_entries, list):
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            host = registry.entry_to_known_host(raw)
            if host is not None:
                hosts.append(host)
    raw_keys = doc.get("host_keys")
    host_keys: dict[str, list[str]] = {}
    if isinstance(raw_keys, dict):
        for name, lines in raw_keys.items():
            if isinstance(name, str) and isinstance(lines, list):
                host_keys[name] = [line for line in lines if isinstance(line, str)]
    generation = doc.get("mirror_generation")
    if not (isinstance(generation, int) and not isinstance(generation, bool)):
        generation = 0
    last_change = doc.get("last_change")
    return RemoteRegistry(
        hosts=hosts,
        host_keys=host_keys,
        generation=generation,
        last_change=last_change if isinstance(last_change, dict) else None,
    )


def _check_sync_supported(status: dict[str, Any]) -> None:
    versions = status.get("payload_versions")
    if not isinstance(versions, list) or SYNC_PAYLOAD_VERSION not in versions:
        raise AdoptError(
            "this remo-web deployment does not support bi-directional sync "
            "(registry payload v3) — upgrade the remo-web container image, or "
            "use one-way `remo web push`."
        )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_web_sync(
    url: str,
    token: str,
    *,
    via: str | None = None,
    assume_yes: bool = False,
    prefer: str | None = None,
    allow_empty: bool = False,
    dry_run: bool = False,
    force: bool = False,
    interactive: bool | None = None,
) -> int:
    """The `remo web sync` driver. Returns the exit code (0/1/3), never raises."""
    if interactive is None:
        interactive = sys.stdin.isatty() and not assume_yes

    def _flow(client: SetupApiClient) -> int:
        return _sync_flow(
            client,
            assume_yes=assume_yes,
            prefer=prefer,
            allow_empty=allow_empty,
            dry_run=dry_run,
            force=force,
            interactive=interactive,
            display_url=url,
        )

    try:
        if via:
            print_info(f"Opening SSH tunnel via {via}...")
            with open_via_tunnel(via, url) as tunneled_url:
                try:
                    return _flow(SetupApiClient(tunneled_url, token))
                except SetupApiError as e:
                    if e.status in (400, 403):
                        raise AdoptError(
                            f"the service rejected the tunneled request (HTTP "
                            f"{e.status}) — most likely its Host allowlist. When "
                            "syncing through --via, the service's "
                            "REMO_WEB_ALLOWED_HOSTS must include 127.0.0.1."
                        ) from e
                    raise
        return _flow(SetupApiClient(url, token))
    except MountConfiguredError:
        print_error(_MOUNT_CONFIGURED_MSG)
        return EXIT_FAILURE
    except AdoptError as e:
        print_error(str(e))
        return EXIT_FAILURE


def _sync_flow(
    client: SetupApiClient,
    *,
    assume_yes: bool,
    prefer: str | None,
    allow_empty: bool,
    dry_run: bool,
    force: bool,
    interactive: bool,
    display_url: str,
) -> int:
    # Step 1: status precheck + the v3 capability gate — BEFORE any mutation.
    status = client.get_status()
    state = str(status.get("state", "unknown"))
    if state == "mount_configured":
        raise MountConfiguredError(_MOUNT_CONFIGURED_MSG)
    _check_sync_supported(status)
    print_info(
        f"Service state: {state} "
        f"({status.get('registry_instances', 0)} instances currently registered)"
    )

    # Step 2: identity.
    identity = client.get_identity()
    deployment_id = str(identity.get("deployment_id") or "")
    public_key = str(identity.get("public_key") or "")
    if not public_key:
        raise AdoptError(
            "the service returned no public key, so it cannot be authorized on "
            "any instance. The service identity may be missing — check the "
            "service's state volume and logs."
        )
    print_info(f"Service identity: remo-web@{deployment_id or 'unknown'}")

    deployment_cache = load_push_cache().get(deployment_id)
    cached_instances = dict(deployment_cache.instances) if deployment_cache else {}
    cached_generation = deployment_cache.mirror_generation if deployment_cache else 0
    base: dict[str, dict[str, Any] | None] = {
        name: cached.entry for name, cached in cached_instances.items()
    }

    # Memoized across 409 re-merges: conflict picks + deletion consent.
    resolution_memo: dict[str, str] = {}
    consent_memo: set[str] = set()

    applied: dict[str, Any] | None = None
    final_plan: SyncPlan | None = None
    outcomes: list[InstanceOutcome] = []
    host_keys: dict[str, list[str]] = {}
    revocations: list[RevocationOutcome] = []

    for attempt in range(1, _MAX_PUT_ATTEMPTS + 1):
        # Step 3: the remote registry (fresh on every attempt).
        remote = parse_remote_registry(client.get_registry())

        if attempt == 1 and remote.generation != cached_generation:
            change = remote.last_change or {}
            origin = str(change.get("origin") or "").strip()
            at = str(change.get("at") or "").strip()
            where = "in the web console" if origin == "web" else "by a workstation push"
            detail = f" at {at}" if at else ""
            print_info(
                f"The deployment's registry changed {where}{detail} "
                f"(generation {remote.generation}; this workstation last saw "
                f"{cached_generation}). Changes will be merged."
            )

        # Step 4: local registry + Step 5: plan. RegistryError is not an
        # AdoptError, so wrap it here (as the mutate path below does) to keep
        # run_web_sync's never-raises contract for a corrupt/newer registry.
        try:
            local_hosts = registry.read_registry(readonly=True).hosts
        except registry.RegistryError as e:
            raise AdoptError(f"could not read the local registry: {e}") from e
        plan = build_sync_plan(base, local_hosts, remote.hosts, remote.generation)
        render_sync_plan(plan, deployment_id)

        # Step 6: dry run stops here — GETs only, nothing written anywhere.
        if dry_run:
            print_info("Dry run: nothing was applied (local, remote, or cache).")
            return EXIT_OK

        # Step 7: resolve conflicts, then the deletion consent gate.
        if not resolve_conflicts(
            plan, prefer=prefer, interactive=interactive, memo=resolution_memo
        ):
            print_error(
                "unresolved conflicts in a non-interactive run — nothing was "
                "applied. Re-run interactively, or pass --prefer-local / "
                "--prefer-remote."
            )
            return EXIT_ABORTED
        if not gate_deletion_consent(
            plan, assume_yes=assume_yes, interactive=interactive, consented=consent_memo
        ):
            print_error("sync aborted before any change was applied.")
            return EXIT_ABORTED

        final_hosts = merged_hosts(plan)
        if not final_hosts and not allow_empty:
            raise AdoptError(
                "the merged registry is empty. Refusing to sync: an empty "
                "mirror would wipe the deployment's instance list. Re-run with "
                "--allow-empty if this is really intended."
            )

        # Step 8: local apply — one CAS-guarded mutate_registry write.
        to_set, to_remove = local_mutations(plan)
        if to_set or to_remove:
            baseline = plan.local_baseline

            def _mutator(full: list[KnownHost]) -> list[KnownHost]:
                if frozenset(registry.canonical_entry(h) for h in full) != baseline:
                    raise SyncLocalConflictError(
                        "the local registry changed while syncing; re-run "
                        "`remo web sync`."
                    )
                kept = [h for h in full if h.name not in to_remove and h.name not in to_set]
                return kept + list(to_set.values())

            try:
                registry.mutate_registry(_mutator)
            except SyncLocalConflictError:
                raise
            except registry.RegistryError as e:
                raise AdoptError(f"could not update the local registry: {e}") from e

        # Step 8b (local-only trust/identity hygiene for pulled hosts).
        _handle_pulled_hosts(plan, remote.host_keys, interactive=interactive)

        # Step 9: per-instance processing — PUSH side only. PULL entries are
        # never keyscanned/authorized (the service already holds
        # authorization; workstation reachability is not a sync precondition).
        outcomes = []
        host_keys = {}
        for action in plan.actions:
            side = _final_side(action)
            if side is None:
                continue
            if side == "remote" or action.resolution == RESOLUTION_SKIP:
                host = action.remote
                assert host is not None
                pulled_lines = remote.host_keys.get(action.name, [])
                if pulled_lines:
                    # Step 10 round-trip: the payload must stay complete —
                    # the service's known_hosts write is wholesale.
                    host_keys[action.name] = list(pulled_lines)
                outcomes.append(
                    InstanceOutcome(
                        host,
                        OUTCOME_PULLED,
                        detail="pulled from the deployment; not verified locally",
                    )
                )
                continue

            host = action.local
            assert host is not None
            cached = cached_instances.get(action.name)
            if (
                not force
                and is_direct_access(host)
                and cached is not None
                and cached.fingerprint == instance_fingerprint(host)
                and cached.host_keys
            ):
                host_keys[action.name] = list(cached.host_keys)
                outcomes.append(
                    InstanceOutcome(
                        host,
                        OUTCOME_UNCHANGED,
                        detail="unchanged since last sync; keyscan/authorize skipped",
                    )
                )
                continue
            print_info(f"Processing {host.type}/{host.name} ({host.host})...")
            outcomes.append(
                _process_instance(
                    host,
                    public_key,
                    interactive=interactive,
                    host_keys=host_keys,
                )
            )

        # Step 10/11: the v3 payload with the generation precondition.
        payload = build_adoption_payload(
            final_hosts,
            host_keys,
            allow_empty=True,
            version=SYNC_PAYLOAD_VERSION,
            base_generation=remote.generation,
        )
        payload["workstation"] = _workstation_label()
        try:
            applied = client.put_registry(payload, allow_empty=allow_empty)
        except GenerationConflictError as e:
            if attempt == _MAX_PUT_ATTEMPTS:
                change = e.last_change or {}
                origin = str(change.get("origin") or "unknown")
                at = str(change.get("at") or "unknown time")
                raise AdoptError(
                    f"the deployment's registry kept changing while syncing "
                    f"({_MAX_PUT_ATTEMPTS} attempts; last change origin="
                    f"{origin} at {at}). Re-run `remo web sync` once the other "
                    "writer has finished."
                ) from e
            print_warning(
                "the deployment's registry changed during the sync — "
                "re-merging and retrying..."
            )
            continue
        final_plan = plan
        break

    assert applied is not None and final_plan is not None
    print_success(
        f"Registry synced: {applied.get('registry_instances', 0)} instances, "
        f"host keys for {applied.get('host_key_instances', 0)}."
    )

    # Step 12: best-effort revocation for remote deletions (after the PUT, so a
    # failed PUT can never leave a de-authorized instance still mirrored). No
    # revocation for DELETE_LOCAL — the web-side removal owns its own.
    revocations = _revoke_remote_deletions(remote_deletions(final_plan), cached_instances)

    # Step 13: verify + self-heal + report (the push machinery, reused).
    print_info("Running service-side verification...")
    verify = client.post_verify()
    repaired = _repair_auth_failures(
        outcomes, verify, host_keys, interactive=interactive, public_key=public_key
    )
    repair_put_failed = False
    if repaired:
        returned = applied.get("mirror_generation")
        repair_base = returned if isinstance(returned, int) and not isinstance(returned, bool) else 0
        payload = build_adoption_payload(
            merged_hosts(final_plan),
            host_keys,
            allow_empty=True,
            version=SYNC_PAYLOAD_VERSION,
            base_generation=repair_base,
        )
        payload["workstation"] = _workstation_label()
        try:
            applied = client.put_registry(payload, allow_empty=allow_empty)
        except SetupApiError as e:
            repair_put_failed = True
            print_warning(
                f"Could not re-push the mirror after repair: {e}. The repaired "
                "instances will be re-processed in full on the next sync."
            )
        else:
            print_info("Re-running service-side verification after repair...")
            try:
                verify = client.post_verify()
            except SetupApiError as e:
                print_warning(
                    f"Could not re-run service-side verification after repair: "
                    f"{e}. The report below predates the repair."
                )

    # Step 14: cache v4 write-back.
    returned_generation = applied.get("mirror_generation")
    new_generation = (
        returned_generation
        if isinstance(returned_generation, int) and not isinstance(returned_generation, bool)
        else cached_generation
    )
    if deployment_id:
        cache_entries = _cache_from_outcomes(outcomes, host_keys)
        still_failing = auth_failed_labels(verify)
        for outcome in outcomes:
            if outcome.label in still_failing:
                cache_entries.pop(outcome.host.name, None)
            elif repair_put_failed and outcome.outcome == "repaired":
                cache_entries.pop(outcome.host.name, None)
        # A skipped conflict keeps its OLD base (or none) so it re-surfaces on
        # the next sync instead of silently adopting either side.
        for action in skipped_conflicts(final_plan):
            old = cached_instances.get(action.name)
            if old is not None:
                cache_entries[action.name] = old
            else:
                cache_entries.pop(action.name, None)
        _update_push_cache(deployment_id, cache_entries, new_generation)

    render_summary(outcomes)
    render_revocations(revocations)
    render_verification(verify, outcomes, service_url=display_url or client.base_url)

    _end_session_best_effort(client)
    return EXIT_OK


def _handle_pulled_hosts(
    plan: SyncPlan, remote_host_keys: dict[str, list[str]], *, interactive: bool
) -> None:
    """Local hygiene for entries that arrived FROM the service.

    * Pulled host-key lines are NOT silently written to ~/.ssh/known_hosts —
      the browser confirmation established trust *for the service*; this
      workstation's store records *this human at this machine*. Interactively,
      offer to record them (via the #157 machinery); declining is harmless
      (`remo shell` falls back to ssh's own TOFU prompt).
    * A pulled identity_file is kept verbatim (rewriting would ping-pong as a
      local change forever) but warned about when the path does not resolve
      here — under IdentitiesOnly a missing file is a guaranteed auth failure.
    """
    pulled = [
        a
        for a in plan.actions
        if a.kind in (SyncActionKind.PULL_ADD, SyncActionKind.PULL_UPDATE)
        or (a.kind is SyncActionKind.CONFLICT and a.resolution == RESOLUTION_REMOTE)
    ]
    for action in pulled:
        host = action.remote
        if host is None:
            continue
        identity = host.ssh_identity
        if identity and not Path(identity).expanduser().is_file():
            # One plain f-string, no .format(): implicit literal concatenation
            # would apply .format() to the WHOLE joined message, and the
            # interpolated identity/name are untrusted remote values — a brace
            # in either would crash the sync mid-apply.
            print_warning(
                f"'{host.name}' was pulled with an SSH identity path that does "
                f"not resolve on this workstation ({identity}). remo passes it "
                f"with IdentitiesOnly=yes, so `remo shell {host.name}` will "
                "fail until the key exists here (or the entry is re-added "
                "without --identity)."
            )
        if not interactive or not is_direct_access(host):
            continue
        lines = remote_host_keys.get(action.name, [])
        if not lines:
            continue
        print()
        print_info(f"'{host.name}' ({host.host}) was pulled from the deployment.")
        print("Its host keys (as trusted by the service):")
        print(_render_fingerprints(lines))
        if confirm(f"Record these keys in this workstation's known_hosts for {host.host}?"):
            warning = _persist_confirmed_host_keys(
                lines, Path.home() / ".ssh" / "known_hosts"
            )
            if warning:
                print_warning(warning)


def _revoke_remote_deletions(
    deletions: list[SyncAction], cached_instances: dict[str, CachedInstance]
) -> list[RevocationOutcome]:
    """Best-effort `remo-web@` revocation for remotely-deleted entries.

    Prefers the REMOTE entry (fresh connection tuple straight from the
    service), falling back to the cached tuple. Never fatal.
    """
    from remo_cli.core.web_adopt import (
        REVOKE_FAILED,
        REVOKE_OK,
        _host_from_cache,
        _manual_revoke_remediation,
    )

    outcomes: list[RevocationOutcome] = []
    for action in deletions:
        host = action.remote
        if host is None:
            cached = cached_instances.get(action.name)
            if cached is not None and cached.host:
                host = _host_from_cache(action.name, cached)
        if host is None or not host.host:
            outcomes.append(
                RevocationOutcome(
                    action.name,
                    REVOKE_FAILED,
                    detail="no connection details for this instance",
                    remediation=_manual_revoke_remediation("the instance"),
                )
            )
            continue
        if not is_direct_access(host):
            outcomes.append(
                RevocationOutcome(
                    action.name,
                    REVOKE_FAILED,
                    detail="SSM-routed instance (AWS-managed transport)",
                    remediation=_manual_revoke_remediation("the instance via SSM"),
                )
            )
            continue
        try:
            ok, detail = revoke_service_key(host)
        except Exception as e:  # noqa: BLE001 — revocation is never fatal
            ok, detail = False, f"unexpected error: {e}"
        if ok:
            outcomes.append(
                RevocationOutcome(action.name, REVOKE_OK, detail="service key removed")
            )
        else:
            outcomes.append(
                RevocationOutcome(
                    action.name,
                    REVOKE_FAILED,
                    detail=detail,
                    remediation=_manual_revoke_remediation(f"{host.user}@{host.host}"),
                )
            )
    return outcomes
