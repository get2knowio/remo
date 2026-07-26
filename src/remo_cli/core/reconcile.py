"""Provider-agnostic sync reconcile engine (feature 016-sync-reconcile).

A provider's sync boils down to one *probe* — "what hosts exist in this
scope, which carry the managed marker, and was the enumeration complete?"
— fed through :func:`run_sync`. Everything else (diffing against the
registry, rendering the plan, gating removals behind consent, and writing
exactly once) lives here so no provider hand-rolls it again.

House rules inherited from :mod:`core.registry`:

- Never raise :class:`SystemExit`. Every entry point returns an exit code
  (``EXIT_OK`` / ``EXIT_FAILURE`` / ``EXIT_ABORTED``); only the thin CLI
  wrapper built in a later phase calls ``sys.exit(rc)``.
- Exit code ``2`` is reserved for Click/usage errors and is never returned
  from this module.

See specs/016-sync-reconcile/data-model.md and contracts/{cli-sync,
provider-probe}.md for the authoritative contracts this module implements.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

from remo_cli.core import web_drift
from remo_cli.core.output import confirm, print_error, print_info, print_success, print_warning
from remo_cli.core.provider_registry import NameFormat, get_descriptor, is_provider_type
from remo_cli.core.registry import mutate_registry, read_registry
from remo_cli.models.host import KnownHost

# ---------------------------------------------------------------------------
# Exit codes (FR-043) -- 2 is deliberately never defined here, it is Click's.
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_ABORTED = 3


# ---------------------------------------------------------------------------
# Error taxonomy -- none of these are ever allowed to escape run_sync.
# ---------------------------------------------------------------------------


class ReconcileError(Exception):
    """Base class for all reconcile-layer errors."""


class ProbeError(ReconcileError):
    """A provider probe could not ask its provider (FR-009)."""


class ReconcileConflictError(ReconcileError):
    """The in-scope registry slice moved between plan and write (R2)."""


class AmbiguousPlanError(ReconcileError):
    """Two probed hosts resolved to the same registry name in scope (FR-037)."""


class ScopeError(ReconcileError):
    """A :class:`SyncScope` was constructed with an invalid field combination."""


# ---------------------------------------------------------------------------
# SyncScope
# ---------------------------------------------------------------------------


def _requires_region(type_name: str) -> bool:
    """True when *type_name*'s descriptor declares a ``--region`` sync
    option (018 T047) -- drives the FLAT-provider host/region requirement
    generically instead of hardcoding "aws"."""
    return any(opt.name == "--region" for opt in get_descriptor(type_name).sync_options)


@dataclass(frozen=True)
class SyncScope:
    type: str
    host: str = ""
    region: str = ""

    def __post_init__(self) -> None:
        if not is_provider_type(self.type):
            raise ScopeError(f"unknown provider type for sync scope: {self.type!r}")
        if get_descriptor(self.type).name_format is NameFormat.HOST_SCOPED:
            if not self.host:
                raise ScopeError(f"{self.type} sync scope requires a non-empty host")
        elif _requires_region(self.type):
            if not self.region:
                raise ScopeError(f"{self.type} sync scope requires a non-empty region")
        elif self.host or self.region:
            raise ScopeError(f"{self.type} sync scope must not carry a host or region")

    def in_update_scope(self, entry: KnownHost) -> bool:
        if get_descriptor(self.type).name_format is NameFormat.HOST_SCOPED:
            return entry.type == self.type and entry.name.startswith(f"{self.host}/")
        if self.type == "aws":
            # A region-less legacy AWS entry matches every region's update
            # scope, so a hit against the queried region self-heals it --
            # but it can never be *removed* while region-less (see below).
            return entry.type == "aws" and entry.region in (self.region, "")
        return entry.type == self.type

    def in_removal_scope(self, entry: KnownHost) -> bool:
        if self.type == "aws":
            return entry.type == "aws" and entry.region == self.region
        return self.in_update_scope(entry)

    def describe(self) -> str:
        descriptor = get_descriptor(self.type)
        if self.type == "aws":
            return f"aws region {self.region}"
        if self.type == "incus":
            return f"incus host {self.host} (default project)"
        if self.type == "proxmox":
            return f"proxmox node {self.host} (this node only)"
        return f"{descriptor.display_name.lower()} (all servers in project)"


# ---------------------------------------------------------------------------
# Probe seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveredHost:
    entry: KnownHost
    marked: bool
    state: str = ""
    # Eligible for addition when --all is passed and this host is unmarked
    # (FR-030). Defaults to True (most providers widen --all to "every host
    # in scope"); a provider with a narrower adoption convention -- AWS's
    # remo-* naming, per research.md R7 -- sets this per host instead.
    adopted: bool = True
    # Which KnownHost field names the provider actually observed (018,
    # contracts/sync-merge.md, closes #87). None = legacy semantics: every
    # non-empty field on `entry` counts as observed (existing Incus/Proxmox/
    # Hetzner probes need no change). A frozenset marks only the fields the
    # provider genuinely read; others carry defaults/fillers and must not
    # overwrite a hand-edited registry value on merge.
    observed: frozenset[str] | None = None


@dataclass(frozen=True)
class ProbeResult:
    hosts: list[DiscoveredHost]
    complete: bool
    incomplete_reason: str = ""
    adoption_criteria: str = ""
    warnings: list[str] = field(default_factory=list)


ProbeFn = Callable[[], ProbeResult]


# ---------------------------------------------------------------------------
# MergedEntry rule (FR-041)
# ---------------------------------------------------------------------------


def merge_entry(
    existing: KnownHost, discovered: KnownHost, observed: frozenset[str] | None = None
) -> KnownHost:
    """Merge *discovered* into *existing* (contracts/sync-merge.md, #87).

    For each mergeable field: take *discovered*'s value iff the field is
    observed (``observed is None`` -> legacy semantics, every non-empty
    field counts as observed) and non-empty; otherwise keep *existing*'s.
    """

    def pick(field_name: str, discovered_value: str) -> str:
        if observed is None:
            return discovered_value or getattr(existing, field_name)
        if field_name in observed and discovered_value:
            return discovered_value
        return getattr(existing, field_name)  # type: ignore[no-any-return]

    return KnownHost(
        type=existing.type,
        name=existing.name,
        host=pick("host", discovered.host),
        # Always the existing value: every provider hardcodes "remo" here,
        # so preserving it only ever helps a hand-edited registry.
        user=existing.user,
        instance_id=pick("instance_id", discovered.instance_id),
        access_mode=pick("access_mode", discovered.access_mode),
        region=pick("region", discovered.region),
    )


# ---------------------------------------------------------------------------
# ReconcilePlan + build_plan
# ---------------------------------------------------------------------------


@dataclass
class ReconcilePlan:
    scope: SyncScope
    added: list[KnownHost] = field(default_factory=list)
    updated: list[tuple[KnownHost, KnownHost]] = field(default_factory=list)
    unchanged: list[KnownHost] = field(default_factory=list)
    removed: list[KnownHost] = field(default_factory=list)
    skipped_unmarked: list[str] = field(default_factory=list)
    retained_unmarked: list[str] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    removals_suppressed: bool = False
    baseline: tuple[KnownHost, ...] = field(default_factory=tuple)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_removals(self) -> bool:
        return bool(self.removed)

    @property
    def is_noop(self) -> bool:
        return not self.added and not self.updated and not self.removed


def build_plan(
    current: list[KnownHost],
    probe: ProbeResult,
    scope: SyncScope,
    include_all: bool,
) -> ReconcilePlan:
    in_scope_current = [h for h in current if scope.in_update_scope(h)]
    registry_by_name = {h.name: h for h in in_scope_current}

    discovered_by_name: dict[str, DiscoveredHost] = {}
    for discovered in probe.hosts:
        name = discovered.entry.name
        if name in discovered_by_name:
            raise AmbiguousPlanError(
                f"probe returned two or more hosts named {name!r} within "
                f"{scope.describe()} -- this is a probe bug, not a diffable state"
            )
        discovered_by_name[name] = discovered

    plan = ReconcilePlan(scope=scope, baseline=tuple(in_scope_current))

    for name, discovered in discovered_by_name.items():
        existing = registry_by_name.get(name)
        if existing is None:
            if discovered.marked or (include_all and discovered.adopted):
                plan.added.append(discovered.entry)
                if discovered.state:
                    plan.states[name] = discovered.state
            else:
                plan.skipped_unmarked.append(name)
            continue

        merged = merge_entry(existing, discovered.entry, discovered.observed)
        if merged != existing:
            plan.updated.append((existing, merged))
        else:
            plan.unchanged.append(existing)
        if discovered.state:
            plan.states[name] = discovered.state
        if not discovered.marked:
            plan.retained_unmarked.append(name)

    for name, existing in registry_by_name.items():
        if name in discovered_by_name:
            continue
        if probe.complete and scope.in_removal_scope(existing):
            plan.removed.append(existing)
        else:
            plan.unchanged.append(existing)

    # Communicates "enumeration incomplete", never "out of removal scope" --
    # the latter is normal and expected, not a suppression warning.
    plan.removals_suppressed = not probe.complete
    plan.warnings = list(probe.warnings)

    return plan


# ---------------------------------------------------------------------------
# render_plan (contracts/cli-sync.md "Output contract")
# ---------------------------------------------------------------------------


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def render_plan(
    plan: ReconcilePlan,
    dry_run: bool,
    adoption_criteria: str = "",
    include_all: bool = False,
    incomplete_reason: str = "",
) -> None:
    prefix = "[dry-run] " if dry_run else ""
    print_info(f"{prefix}Reconciling {plan.scope.describe()}...")

    for warning in plan.warnings:
        print_warning(warning)

    nothing_to_report = (
        plan.is_noop
        and not plan.removals_suppressed
        and not plan.retained_unmarked
        and not plan.skipped_unmarked
    )
    if nothing_to_report:
        print_info(f"Nothing to reconcile — registry already matches {plan.scope.describe()}.")
        return

    def annotate(name: str) -> str:
        state = plan.states.get(name, "")
        return f"{name} ({state})" if state else name

    if plan.added:
        names = ", ".join(annotate(h.name) for h in plan.added)
        print_info(f"  + added      {len(plan.added)}   {names}")
    if plan.updated:
        names = ", ".join(annotate(after.name) for _before, after in plan.updated)
        print_info(f"  ~ updated    {len(plan.updated)}   {names}")
    if plan.unchanged:
        names = ", ".join(annotate(h.name) for h in plan.unchanged)
        print_info(f"  = unchanged  {len(plan.unchanged)}   {names}")
    if plan.removed:
        names = ", ".join(h.name for h in plan.removed)
        print_info(f"  - removed    {len(plan.removed)}   {names}")

    # Only the host-scoped providers (incus, proxmox) accept `--host` on
    # `update`; aws and hetzner do not, so the hint must not suggest it there.
    mark_cmd = f"remo {plan.scope.type} update --name <n>"
    if get_descriptor(plan.scope.type).name_format is NameFormat.HOST_SCOPED:
        mark_cmd += " --host <h>"

    if plan.retained_unmarked:
        names = ", ".join(plan.retained_unmarked)
        entries = _plural(len(plan.retained_unmarked), "entry is", "entries are")
        print_info(
            f"{len(plan.retained_unmarked)} retained {entries} not remo-marked: {names}"
        )
        print_info(f"Mark permanently: {mark_cmd}")

    if plan.skipped_unmarked:
        names = ", ".join(plan.skipped_unmarked)
        print_info(
            f"Skipped {len(plan.skipped_unmarked)} unmarked instance(s): {names}"
        )
        print_info("Adopt this run: rerun with --all")
        print_info(f"Mark permanently: {mark_cmd}")

    if include_all and adoption_criteria:
        print_info(f"--all: {adoption_criteria}")

    if plan.removals_suppressed:
        reason = incomplete_reason or "enumeration did not complete"
        print_warning(
            f"Removals skipped: the provider listing was incomplete ({reason}). "
            "Additions and updates were applied."
        )

    if plan.has_removals and not dry_run:
        entry_word = _plural(len(plan.removed), "entry", "entries")
        print_info(
            f"The following {len(plan.removed)} {entry_word} will be REMOVED from the registry:"
        )
        for h in plan.removed:
            details = ", ".join(v for v in (h.instance_id, h.region) if v)
            print_info(f"  - {h.name} ({details})" if details else f"  - {h.name}")


# ---------------------------------------------------------------------------
# Consent gate (FR-011 -- FR-014)
# ---------------------------------------------------------------------------


class ConsentOutcome(Enum):
    NOT_REQUIRED = "not_required"
    APPLY = "apply"
    DECLINED = "declined"
    NON_INTERACTIVE = "non_interactive"


def gate_consent(plan: ReconcilePlan, auto_confirm: bool) -> ConsentOutcome:
    if not plan.has_removals:
        return ConsentOutcome.NOT_REQUIRED
    if auto_confirm:
        return ConsentOutcome.NOT_REQUIRED
    if not sys.stdin.isatty():
        return ConsentOutcome.NON_INTERACTIVE

    count = len(plan.removed)
    prompt = "Remove it?" if count == 1 else f"Remove {count} entries?"
    return ConsentOutcome.APPLY if confirm(prompt) else ConsentOutcome.DECLINED


# ---------------------------------------------------------------------------
# apply_plan -- the single mutate_registry() call (R1, R2)
# ---------------------------------------------------------------------------


def _host_fingerprint_set(
    hosts: Iterable[KnownHost],
) -> frozenset[tuple[str, str, str, str, str, str, str]]:
    return frozenset(
        (h.type, h.name, h.host, h.user, h.instance_id, h.access_mode, h.region) for h in hosts
    )


def apply_plan(plan: ReconcilePlan) -> None:
    if plan.is_noop:
        return

    def _mutator(full: list[KnownHost]) -> list[KnownHost]:
        # Pure list-in/list-out: the registry lock is not reentrant, so no
        # other registry function may be called from in here (R1).
        current_in_scope = tuple(h for h in full if plan.scope.in_update_scope(h))
        if _host_fingerprint_set(current_in_scope) != _host_fingerprint_set(plan.baseline):
            raise ReconcileConflictError(
                f"the registry changed for {plan.scope.describe()} after this "
                "plan was built"
            )

        out_of_scope = [h for h in full if not plan.scope.in_update_scope(h)]
        reconciled = (
            [after for _before, after in plan.updated] + plan.unchanged + plan.added
        )
        return out_of_scope + reconciled

    mutate_registry(_mutator)


# ---------------------------------------------------------------------------
# run_sync -- the top-level driver; the only boundary that never raises
# ---------------------------------------------------------------------------


def run_sync(
    scope: SyncScope,
    probe_fn: ProbeFn,
    auto_confirm: bool = False,
    dry_run: bool = False,
    include_all: bool = False,
) -> int:
    # NOTE: contracts/provider-probe.md's illustrative `run_sync(...)` example
    # omits include_all because the provider's own probe closure already
    # captures it for eligibility widening. build_plan/render_plan also need
    # it (for the added-vs-skipped split and the adoption-criteria line), so
    # it is accepted here too -- providers pass the same flag twice: once
    # baked into their probe lambda, once explicitly to run_sync.
    try:
        probe = probe_fn()
    except ProbeError as exc:
        print_error(str(exc))
        return EXIT_FAILURE

    # The initial read only builds the plan and the concurrency baseline; the
    # authoritative write -- and any lazy legacy known_hosts -> registry.json
    # migration -- happens under the lock in apply_plan -> mutate_registry.
    # Reading read-only on a dry-run keeps the "dry-run writes nothing"
    # contract (FR-042) even when a legacy store still needs migrating.
    current = read_registry(readonly=dry_run).hosts

    try:
        plan = build_plan(current, probe, scope, include_all)
    except AmbiguousPlanError as exc:
        print_error(str(exc))
        return EXIT_FAILURE

    render_plan(
        plan,
        dry_run,
        adoption_criteria=probe.adoption_criteria,
        include_all=include_all,
        incomplete_reason=probe.incomplete_reason,
    )

    if dry_run:
        return EXIT_OK

    outcome = gate_consent(plan, auto_confirm)

    if outcome in (ConsentOutcome.NOT_REQUIRED, ConsentOutcome.APPLY):
        try:
            apply_plan(plan)
        except ReconcileConflictError as exc:
            print_error(f"{exc}; re-run 'remo {scope.type} sync' to try again")
            return EXIT_FAILURE
        if plan.has_removals:
            entry_word = _plural(len(plan.removed), "entry", "entries")
            print_success(f"Removed {len(plan.removed)} {entry_word} from the registry.")
        # Out-of-date nudge (017 US2, FR-013): only when the registry actually
        # changed (never on a no-op, dry-run, or aborted sync). One site covers
        # all four providers' sync via this shared engine (Spec 016). The nudge
        # lives here, not in the CLI layer, because only run_sync distinguishes
        # "applied" from "dry-run/no-op".
        if not plan.is_noop:
            notice = web_drift.out_of_date_notice()
            if notice is not None:
                print_info(notice)
        return EXIT_OK

    if outcome is ConsentOutcome.DECLINED:
        print_warning("Aborted: no changes were made to the registry.")
        return EXIT_ABORTED

    # NON_INTERACTIVE
    print_warning(
        "Removal needs confirmation, but no terminal is attached and --yes "
        "was not given. Aborted: no changes were made to the registry."
    )
    return EXIT_ABORTED
