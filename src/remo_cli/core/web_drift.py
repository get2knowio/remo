"""Offline registry-vs-push-cache drift + the post-mutation nudge (017 US2).

Both ``remo web status`` and the out-of-date nudge emitted after every
registry-mutating command reuse this module. Like :mod:`core.web_adopt` it is
**stdlib + core/models only** — it MUST stay importable without the ``web``
extra installed (never import anything from ``remo_cli.web.*`` or an optional
dependency), so the nudge fires from the ordinary CLI on a machine that has
never installed the web service.

The drift comparison is pure and offline: it diffs the current local registry
against the non-secret push cache a previous ``remo web push`` wrote, making
**zero** network or SSH connections (FR-010). See
specs/017-web-adopt-simplify/contracts/cli-web-status.md and data-model.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from remo_cli.core.output import GREEN, NC, RED, YELLOW, print_info
from remo_cli.core.web_adopt import (
    CachedInstance,
    PushCache,
    instance_fingerprint,
    load_push_cache,
)
from remo_cli.models.host import KnownHost


class DriftError(Exception):
    """Deployment selection failed (ambiguous multi-deployment, or unknown id)."""


class DriftState(str, Enum):
    NEW = "new"
    CHANGED = "changed"
    REMOVED = "removed"
    IN_SYNC = "in_sync"


@dataclass
class InstanceDrift:
    name: str
    state: DriftState
    type: str = ""


@dataclass
class DriftReport:
    deployment_id: str
    entries: list[InstanceDrift] = field(default_factory=list)

    @property
    def new(self) -> list[InstanceDrift]:
        return [e for e in self.entries if e.state is DriftState.NEW]

    @property
    def changed(self) -> list[InstanceDrift]:
        return [e for e in self.entries if e.state is DriftState.CHANGED]

    @property
    def removed(self) -> list[InstanceDrift]:
        return [e for e in self.entries if e.state is DriftState.REMOVED]

    @property
    def in_sync(self) -> list[InstanceDrift]:
        return [e for e in self.entries if e.state is DriftState.IN_SYNC]

    @property
    def is_in_sync(self) -> bool:
        """True when nothing would change on a push (no new/changed/removed)."""
        return not (self.new or self.changed or self.removed)


def diff_registry_against_cache(
    hosts: list[KnownHost], cached_instances: dict[str, CachedInstance]
) -> list[InstanceDrift]:
    """Classify each registry entry / cached instance as new/changed/removed/in_sync.

    Reuses :func:`core.web_adopt.instance_fingerprint` so "changed" means the
    exact same field set the push mirrors moved. Pure and offline.
    """
    entries: list[InstanceDrift] = []
    host_names = {h.name for h in hosts}
    for host in hosts:
        cached = cached_instances.get(host.name)
        if cached is None:
            state = DriftState.NEW
        elif cached.fingerprint != instance_fingerprint(host):
            state = DriftState.CHANGED
        else:
            state = DriftState.IN_SYNC
        entries.append(InstanceDrift(host.name, state, host.type))

    for name, cached in cached_instances.items():
        if name not in host_names:
            entries.append(InstanceDrift(name, DriftState.REMOVED, cached.type))

    entries.sort(key=lambda e: e.name)
    return entries


def select_deployment(cache: PushCache, selector: str | None) -> str:
    """Pick which cached deployment to report against (Clarifications Q4).

    Implicit when exactly one deployment is cached; requires an explicit
    ``selector`` (a deployment id) otherwise, raising :class:`DriftError` with
    the known ids listed. A ``selector`` that matches no cached deployment is
    also a :class:`DriftError`.
    """
    ids = sorted(cache)
    if not ids:
        raise DriftError("no deployments are recorded in the push cache")
    if selector:
        if selector in cache:
            return selector
        raise DriftError(
            f"no cached deployment matches {selector!r}; known deployments: "
            f"{', '.join(ids)}"
        )
    if len(ids) == 1:
        return ids[0]
    raise DriftError(
        "this workstation has pushed to more than one deployment; re-run with "
        f"--deployment <id> (known: {', '.join(ids)})"
    )


def build_drift_report(deployment_id: str, cache: PushCache, hosts: list[KnownHost]) -> DriftReport:
    """Convenience: build a :class:`DriftReport` for one cached deployment."""
    deployment = cache.get(deployment_id)
    cached_instances = deployment.instances if deployment else {}
    return DriftReport(
        deployment_id=deployment_id,
        entries=diff_registry_against_cache(hosts, cached_instances),
    )


def render_drift(report: DriftReport) -> None:
    """Render the drift table (name, type, state), colored by state (FR-012)."""
    print_info(f"Drift against deployment {report.deployment_id}:")
    if not report.entries:
        print("  (registry is empty and the deployment cached no instances)")
        return

    _color = {
        DriftState.NEW: GREEN,
        DriftState.CHANGED: YELLOW,
        DriftState.REMOVED: RED,
        DriftState.IN_SYNC: NC,
    }
    name_width = max(len(e.name) for e in report.entries)
    type_width = max(len(e.type) for e in report.entries)
    for e in report.entries:
        color = _color[e.state]
        print(
            f"  {e.name:<{name_width}}  {e.type:<{type_width}}  "
            f"{color}{e.state.value}{NC}"
        )

    print(
        f"\n  {len(report.new)} new, {len(report.changed)} changed, "
        f"{len(report.removed)} removed, {len(report.in_sync)} in sync"
    )


def out_of_date_notice() -> str | None:
    """One-line "you may be out of date" notice, or None (FR-013/FR-014).

    Returns the notice **iff** a non-empty push cache exists (this workstation
    has pushed at least once); returns None otherwise so callers print nothing
    when there is no deployment to be out of date. Gated on cache existence
    only — a rare false positive after a no-op mutation is acceptable because
    ``remo web status`` is the cheap authoritative follow-up.
    """
    if not load_push_cache():
        return None
    return (
        "Your web deployment may now be out of date — run 'remo web status' to "
        "see what changed, or 'remo web push' to re-sync."
    )


def emit_out_of_date_notice() -> None:
    """Print :func:`out_of_date_notice` when non-None (shared nudge call site).

    Used by every registry-mutating CLI command (provider create/destroy,
    add/remove); the ``sync`` path calls :func:`out_of_date_notice` directly so
    it can also gate on "the plan actually applied".
    """
    notice = out_of_date_notice()
    if notice is not None:
        print_info(notice)
