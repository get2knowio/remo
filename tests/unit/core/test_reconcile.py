"""Tests for the provider-agnostic sync reconcile engine (016-sync-reconcile).

Covers, per Constitution Principle II (enumerate branches, don't sample):
- SyncScope predicates/describe()/validation for all four provider shapes
  (T012).
- merge_entry's field-by-field precedence rules (T012).
- build_plan's full classification matrix, one test per row, plus
  AmbiguousPlanError (T013).
- gate_consent's five outcomes and run_sync's exit codes, asserting 2 is
  never returned (T014).
- apply_plan's single-write guarantee, out-of-scope byte-for-byte
  preservation, and the optimistic-concurrency conflict check (T015).
"""

from __future__ import annotations

import sys

import pytest

from remo_cli.core.reconcile import (
    AmbiguousPlanError,
    ConsentOutcome,
    DiscoveredHost,
    ProbeResult,
    ReconcileConflictError,
    ScopeError,
    SyncScope,
    apply_plan,
    build_plan,
    gate_consent,
    merge_entry,
    render_plan,
    run_sync,
)
from remo_cli.core.registry import read_registry
from remo_cli.models.host import KnownHost
from tests.conftest import seed_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kh(
    type_: str,
    name: str,
    host: str = "1.2.3.4",
    user: str = "remo",
    instance_id: str = "",
    access_mode: str = "",
    region: str = "",
) -> KnownHost:
    return KnownHost(
        type=type_,
        name=name,
        host=host,
        user=user,
        instance_id=instance_id,
        access_mode=access_mode,
        region=region,
    )


def _dh(
    entry: KnownHost,
    marked: bool = True,
    state: str = "",
    adopted: bool = True,
    observed: frozenset[str] | None = None,
) -> DiscoveredHost:
    return DiscoveredHost(entry=entry, marked=marked, state=state, adopted=adopted, observed=observed)


def _probe(
    hosts: list[DiscoveredHost],
    complete: bool = True,
    incomplete_reason: str = "",
    adoption_criteria: str = "",
    warnings: list[str] | None = None,
) -> ProbeResult:
    return ProbeResult(
        hosts=hosts,
        complete=complete,
        incomplete_reason=incomplete_reason,
        adoption_criteria=adoption_criteria,
        warnings=warnings or [],
    )


class FakeStdin:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


# ---------------------------------------------------------------------------
# SyncScope: predicates
# ---------------------------------------------------------------------------


class TestSyncScopeIncusProxmox:
    @pytest.mark.parametrize("ptype", ["incus", "proxmox"])
    def test_matches_same_type_and_host_prefix(self, ptype):
        scope = SyncScope(type=ptype, host="node1")
        entry = _kh(ptype, "node1/dev1")
        assert scope.in_update_scope(entry) is True
        assert scope.in_removal_scope(entry) is True

    @pytest.mark.parametrize("ptype", ["incus", "proxmox"])
    def test_does_not_match_different_host_prefix(self, ptype):
        scope = SyncScope(type=ptype, host="node1")
        entry = _kh(ptype, "node2/dev1")
        assert scope.in_update_scope(entry) is False
        assert scope.in_removal_scope(entry) is False

    @pytest.mark.parametrize("ptype", ["incus", "proxmox"])
    def test_does_not_match_different_type(self, ptype):
        scope = SyncScope(type=ptype, host="node1")
        other_type = "proxmox" if ptype == "incus" else "incus"
        entry = _kh(other_type, "node1/dev1")
        assert scope.in_update_scope(entry) is False
        assert scope.in_removal_scope(entry) is False

    def test_prefix_match_is_not_a_substring_match(self):
        # "node1" must not match "node10/dev1" -- the "/" makes it a prefix,
        # not a bare startswith on the host string.
        scope = SyncScope(type="incus", host="node1")
        entry = _kh("incus", "node10/dev1")
        assert scope.in_update_scope(entry) is False


class TestSyncScopeAws:
    def test_exact_region_match_is_in_both_scopes(self):
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("aws", "devbox", region="us-west-2")
        assert scope.in_update_scope(entry) is True
        assert scope.in_removal_scope(entry) is True

    def test_different_region_is_in_neither_scope(self):
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("aws", "devbox", region="eu-central-1")
        assert scope.in_update_scope(entry) is False
        assert scope.in_removal_scope(entry) is False

    def test_empty_region_matches_update_scope_but_never_removal_scope(self):
        # The legacy/region-less asymmetry: a region-less AWS entry can be
        # matched (and thus self-heal its region) but can never be removed.
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("aws", "legacybox", region="")
        assert scope.in_update_scope(entry) is True
        assert scope.in_removal_scope(entry) is False

    def test_non_aws_type_never_matches_aws_scope(self):
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("hetzner", "devbox", region="us-west-2")
        assert scope.in_update_scope(entry) is False
        assert scope.in_removal_scope(entry) is False


class TestSyncScopeHetzner:
    def test_matches_unconditionally_regardless_of_host_or_region_fields(self):
        scope = SyncScope(type="hetzner")
        entry = _kh("hetzner", "web1")
        assert scope.in_update_scope(entry) is True
        assert scope.in_removal_scope(entry) is True

    def test_non_hetzner_type_never_matches(self):
        scope = SyncScope(type="hetzner")
        entry = _kh("aws", "web1", region="us-west-2")
        assert scope.in_update_scope(entry) is False
        assert scope.in_removal_scope(entry) is False


class TestSyncScopeDescribe:
    def test_aws(self):
        assert SyncScope(type="aws", region="us-west-2").describe() == "aws region us-west-2"

    def test_incus(self):
        assert (
            SyncScope(type="incus", host="prox01").describe()
            == "incus host prox01 (default project)"
        )

    def test_proxmox(self):
        assert (
            SyncScope(type="proxmox", host="pve1").describe()
            == "proxmox node pve1 (this node only)"
        )

    def test_hetzner(self):
        assert SyncScope(type="hetzner").describe() == "hetzner (all servers in project)"


class TestSyncScopeValidation:
    @pytest.mark.parametrize("ptype", ["incus", "proxmox"])
    def test_empty_host_raises_scope_error(self, ptype):
        with pytest.raises(ScopeError):
            SyncScope(type=ptype, host="")

    def test_aws_empty_region_raises_scope_error(self):
        with pytest.raises(ScopeError):
            SyncScope(type="aws", region="")

    def test_hetzner_non_empty_host_raises_scope_error(self):
        with pytest.raises(ScopeError):
            SyncScope(type="hetzner", host="node1")

    def test_hetzner_non_empty_region_raises_scope_error(self):
        with pytest.raises(ScopeError):
            SyncScope(type="hetzner", region="us-west-2")

    def test_unknown_type_raises_scope_error(self):
        with pytest.raises(ScopeError):
            SyncScope(type="digitalocean")

    def test_frozen_scope_cannot_be_mutated(self):
        scope = SyncScope(type="hetzner")
        with pytest.raises(Exception):
            scope.type = "aws"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# merge_entry
# ---------------------------------------------------------------------------


class TestMergeEntry:
    def test_non_empty_discovered_values_win(self):
        existing = _kh(
            "aws", "devbox", host="1.1.1.1", instance_id="i-old", access_mode="ssm", region="us-east-1"
        )
        discovered = _kh(
            "aws", "devbox", host="2.2.2.2", instance_id="i-new", access_mode="direct", region="us-west-2"
        )
        merged = merge_entry(existing, discovered)
        assert merged.host == "2.2.2.2"
        assert merged.instance_id == "i-new"
        assert merged.access_mode == "direct"
        assert merged.region == "us-west-2"

    def test_empty_discovered_values_preserve_existing(self):
        existing = _kh(
            "aws", "devbox", host="1.1.1.1", instance_id="i-old", access_mode="ssm", region="us-east-1"
        )
        discovered = _kh("aws", "devbox", host="", instance_id="", access_mode="", region="")
        merged = merge_entry(existing, discovered)
        assert merged.host == "1.1.1.1"
        assert merged.instance_id == "i-old"
        assert merged.access_mode == "ssm"
        assert merged.region == "us-east-1"

    def test_stopped_instance_empty_host_preserves_last_known_address(self):
        existing = _kh("aws", "devbox", host="203.0.113.7", instance_id="i-abc", region="us-west-2")
        discovered = _kh("aws", "devbox", host="", instance_id="i-abc", region="us-west-2")
        merged = merge_entry(existing, discovered)
        assert merged.host == "203.0.113.7"

    def test_user_always_preserved_from_existing(self):
        existing = _kh("hetzner", "web1", user="remo")
        discovered = _kh("hetzner", "web1", user="somebody-else")
        merged = merge_entry(existing, discovered)
        assert merged.user == "remo"

    def test_type_and_name_never_change(self):
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        # A discovered entry with a different type/name would be a probe
        # bug, but merge_entry must not let it leak through identity.
        discovered = _kh("incus", "node1/dev1", host="2.2.2.2")
        merged = merge_entry(existing, discovered)
        assert merged.type == existing.type
        assert merged.name == existing.name


# ---------------------------------------------------------------------------
# merge_entry: observed-vs-default semantics (018, contracts/sync-merge.md, #87)
# ---------------------------------------------------------------------------


class TestMergeEntryObserved:
    def test_observed_none_is_legacy_semantics(self):
        """observed=None (the default) behaves exactly like today: any
        non-empty discovered value wins, regardless of field name."""
        existing = _kh("aws", "devbox", access_mode="ssh", region="us-east-1")
        discovered = _kh("aws", "devbox", access_mode="ssm", region="us-west-2")
        merged = merge_entry(existing, discovered, observed=None)
        assert merged.access_mode == "ssm"
        assert merged.region == "us-west-2"

    def test_unobserved_field_preserves_existing_even_if_discovered_nonempty(self):
        """The core of #87: a field the provider filled with a default
        (not observed) must never clobber a hand-edited existing value,
        even though the discovered value is non-empty."""
        existing = _kh("aws", "devbox", access_mode="ssh", region="us-east-1")
        discovered = _kh("aws", "devbox", access_mode="ssm", region="us-west-2")
        merged = merge_entry(existing, discovered, observed=frozenset({"region"}))
        assert merged.access_mode == "ssh"  # not observed -> existing wins
        assert merged.region == "us-west-2"  # observed -> discovered wins

    def test_observed_field_with_empty_discovered_value_preserves_existing(self):
        existing = _kh("aws", "devbox", host="203.0.113.7")
        discovered = _kh("aws", "devbox", host="")
        merged = merge_entry(existing, discovered, observed=frozenset({"host"}))
        assert merged.host == "203.0.113.7"

    def test_empty_observed_set_preserves_everything(self):
        existing = _kh(
            "aws", "devbox", host="1.1.1.1", instance_id="i-old", access_mode="ssm", region="us-east-1"
        )
        discovered = _kh(
            "aws", "devbox", host="2.2.2.2", instance_id="i-new", access_mode="direct", region="us-west-2"
        )
        merged = merge_entry(existing, discovered, observed=frozenset())
        assert merged == existing


class TestBuildPlanObservedIdempotence:
    def test_unobserved_default_never_produces_a_phantom_update(self):
        """#87 acceptance: an existing entry with a hand-set access_mode
        that the probe didn't actually observe (e.g. untagged instance)
        must not show up as an update, and running the same probe twice in
        a row must both yield an empty (no-op) plan."""
        scope = SyncScope(type="aws", region="us-west-2")
        existing = _kh("aws", "devbox", access_mode="ssh")
        # The provider always fills access_mode with a default ("ssm") but
        # only actually observed host/instance_id this round.
        discovered = _kh("aws", "devbox", access_mode="ssm")

        for _ in range(2):  # same probe result, run twice -> idempotent
            plan = build_plan(
                [existing],
                _probe([_dh(discovered, marked=True, observed=frozenset({"host", "instance_id"}))]),
                scope,
                include_all=False,
            )
            assert plan.updated == []
            assert plan.unchanged == [existing]
            assert plan.is_noop


# ---------------------------------------------------------------------------
# build_plan: classification matrix (data-model.md)
# ---------------------------------------------------------------------------


class TestBuildPlanClassification:
    def test_added_when_marked(self):
        scope = SyncScope(type="incus", host="node1")
        entry = _kh("incus", "node1/dev1")
        plan = build_plan([], _probe([_dh(entry, marked=True)]), scope, include_all=False)
        assert plan.added == [entry]
        assert plan.skipped_unmarked == []

    def test_added_via_include_all_when_unmarked(self):
        scope = SyncScope(type="incus", host="node1")
        entry = _kh("incus", "node1/dev1")
        plan = build_plan([], _probe([_dh(entry, marked=False)]), scope, include_all=True)
        assert plan.added == [entry]
        assert plan.skipped_unmarked == []

    def test_skipped_unmarked_when_unmarked_and_not_include_all(self):
        scope = SyncScope(type="incus", host="node1")
        entry = _kh("incus", "node1/dev1")
        plan = build_plan([], _probe([_dh(entry, marked=False)]), scope, include_all=False)
        assert plan.added == []
        assert plan.skipped_unmarked == ["node1/dev1"]

    def test_skipped_unmarked_when_include_all_but_not_adopted(self):
        # A provider with a narrower adoption convention than "every host in
        # scope" (AWS's remo-* naming, R7) reports adopted=False for a host
        # that doesn't qualify -- --all must not sweep it in regardless.
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("aws", "unrelated-prod-box", region="us-west-2")
        plan = build_plan(
            [], _probe([_dh(entry, marked=False, adopted=False)]), scope, include_all=True
        )
        assert plan.added == []
        assert plan.skipped_unmarked == ["unrelated-prod-box"]

    def test_added_via_include_all_when_unmarked_and_adopted(self):
        # The narrower-provider counterpart to the unconditional case above:
        # adopted=True is what actually lets --all sweep an unmarked host in.
        scope = SyncScope(type="aws", region="us-west-2")
        entry = _kh("aws", "remo-devbox", region="us-west-2")
        plan = build_plan(
            [], _probe([_dh(entry, marked=False, adopted=True)]), scope, include_all=True
        )
        assert plan.added == [entry]
        assert plan.skipped_unmarked == []

    def test_updated_when_merge_differs(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=True)]), scope, include_all=False
        )
        assert len(plan.updated) == 1
        before, after = plan.updated[0]
        assert before == existing
        assert after.host == "2.2.2.2"
        assert plan.unchanged == []

    def test_unchanged_when_merge_does_not_differ(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        discovered_entry = _kh("incus", "node1/dev1", host="1.1.1.1")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=True)]), scope, include_all=False
        )
        assert plan.unchanged == [existing]
        assert plan.updated == []

    def test_retained_unmarked_co_occurs_with_unchanged(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        discovered_entry = _kh("incus", "node1/dev1", host="1.1.1.1")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=False)]), scope, include_all=False
        )
        assert plan.unchanged == [existing]
        assert plan.retained_unmarked == ["node1/dev1"]

    def test_retained_unmarked_co_occurs_with_updated(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=False)]), scope, include_all=False
        )
        assert len(plan.updated) == 1
        assert plan.retained_unmarked == ["node1/dev1"]

    def test_removed_when_complete_and_in_removal_scope(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/gone")
        plan = build_plan([existing], _probe([], complete=True), scope, include_all=False)
        assert plan.removed == [existing]
        assert plan.unchanged == []
        assert plan.removals_suppressed is False

    def test_suppressed_not_removed_when_incomplete(self):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/gone")
        plan = build_plan(
            [existing],
            _probe([], complete=False, incomplete_reason="ssh timed out"),
            scope,
            include_all=False,
        )
        assert plan.removed == []
        assert plan.unchanged == [existing]
        assert plan.removals_suppressed is True

    def test_out_of_removal_scope_not_removed_aws_region_less_entry(self):
        # complete=True, but the region-less legacy entry is outside removal
        # scope (FR-023) -- retained, and this alone must not trip
        # removals_suppressed (that flag communicates incomplete enumeration
        # only).
        scope = SyncScope(type="aws", region="us-west-2")
        existing = _kh("aws", "legacybox", region="")
        plan = build_plan([existing], _probe([], complete=True), scope, include_all=False)
        assert plan.removed == []
        assert plan.unchanged == [existing]
        assert plan.removals_suppressed is False

    def test_out_of_update_scope_entry_is_completely_invisible(self):
        scope = SyncScope(type="incus", host="node1")
        other_host_entry = _kh("incus", "node2/other")
        other_type_entry = _kh("aws", "devbox", region="us-west-2")
        plan = build_plan(
            [other_host_entry, other_type_entry], _probe([], complete=True), scope, include_all=False
        )
        assert plan.added == []
        assert plan.updated == []
        assert plan.unchanged == []
        assert plan.removed == []
        assert plan.skipped_unmarked == []
        assert plan.retained_unmarked == []
        assert plan.baseline == ()

    def test_ambiguous_plan_error_on_duplicate_probe_names(self):
        scope = SyncScope(type="incus", host="node1")
        entry_a = _kh("incus", "node1/dup", host="1.1.1.1")
        entry_b = _kh("incus", "node1/dup", host="2.2.2.2")
        with pytest.raises(AmbiguousPlanError):
            build_plan(
                [], _probe([_dh(entry_a), _dh(entry_b)]), scope, include_all=False
            )

    def test_states_populated_for_added_updated_unchanged_but_not_skipped(self):
        scope = SyncScope(type="aws", region="us-west-2")
        existing_unchanged = _kh("aws", "alpha", host="1.1.1.1", region="us-west-2")
        added_entry = _kh("aws", "beta", host="2.2.2.2", region="us-west-2")
        skipped_entry = _kh("aws", "gamma", host="3.3.3.3", region="us-west-2")
        plan = build_plan(
            [existing_unchanged],
            _probe(
                [
                    _dh(existing_unchanged, marked=True, state="stopped"),
                    _dh(added_entry, marked=True, state="running"),
                    _dh(skipped_entry, marked=False, state="running"),
                ]
            ),
            scope,
            include_all=False,
        )
        assert plan.states == {"alpha": "stopped", "beta": "running"}
        assert "gamma" not in plan.states
        assert plan.skipped_unmarked == ["gamma"]

    def test_is_noop_and_has_removals_derived_properties(self):
        scope = SyncScope(type="hetzner")
        existing = _kh("hetzner", "web1", host="1.1.1.1")
        noop_plan = build_plan(
            [existing], _probe([_dh(existing, marked=True)]), scope, include_all=False
        )
        assert noop_plan.is_noop is True
        assert noop_plan.has_removals is False

        removal_plan = build_plan([existing], _probe([], complete=True), scope, include_all=False)
        assert removal_plan.is_noop is False
        assert removal_plan.has_removals is True

    def test_baseline_is_exactly_the_in_scope_slice(self):
        scope = SyncScope(type="incus", host="node1")
        in_scope = _kh("incus", "node1/dev1")
        out_of_scope = _kh("incus", "node2/other")
        plan = build_plan(
            [in_scope, out_of_scope],
            _probe([_dh(in_scope, marked=True)]),
            scope,
            include_all=False,
        )
        assert plan.baseline == (in_scope,)


# ---------------------------------------------------------------------------
# render_plan: "Mark permanently" remedy names `tag`, truthfully (SC-003)
# ---------------------------------------------------------------------------


class TestRenderPlanMarkPermanentlyRemedy:
    def test_host_scoped_type_gets_tag_with_host_flag(self, capsys):
        scope = SyncScope(type="incus", host="node1")
        existing = _kh("incus", "node1/dev1", host="1.1.1.1")
        discovered_entry = _kh("incus", "node1/dev1", host="1.1.1.1")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=False)]), scope, include_all=False
        )
        render_plan(plan, dry_run=False)
        out = capsys.readouterr().out
        assert "Mark permanently: remo incus tag <n> --host <h>" in out

    def test_flat_type_gets_tag_without_host_flag(self, capsys):
        scope = SyncScope(type="hetzner")
        existing = _kh("hetzner", "web1", host="1.2.3.4")
        discovered_entry = _kh("hetzner", "web1", host="1.2.3.4")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=False)]), scope, include_all=False
        )
        render_plan(plan, dry_run=False)
        out = capsys.readouterr().out
        assert "Mark permanently: remo hetzner tag <n>" in out
        assert "--host" not in out

    def test_marker_less_type_gets_no_tag_remedy(self, capsys):
        """AWS has `supports_managed_marker=False`, so no `tag` command is
        generated -- the remedy must stay silent rather than name a command
        Click would reject with "No such command 'tag'" (SC-003)."""
        scope = SyncScope(type="aws", region="us-west-2")
        existing = _kh("aws", "box1", host="1.2.3.4")
        discovered_entry = _kh("aws", "box1", host="1.2.3.4")
        plan = build_plan(
            [existing], _probe([_dh(discovered_entry, marked=False)]), scope, include_all=False
        )
        render_plan(plan, dry_run=False)
        out = capsys.readouterr().out
        assert "not remo-marked" in out  # the diagnosis still prints
        assert "Mark permanently" not in out
        assert "remo aws tag" not in out


# ---------------------------------------------------------------------------
# gate_consent: five outcomes
# ---------------------------------------------------------------------------


class TestGateConsent:
    def _plan_with_removals(self, count: int = 1):
        scope = SyncScope(type="hetzner")
        from remo_cli.core.reconcile import ReconcilePlan

        removed = [_kh("hetzner", f"gone{i}") for i in range(count)]
        return ReconcilePlan(scope=scope, removed=removed)

    def _plan_without_removals(self):
        scope = SyncScope(type="hetzner")
        from remo_cli.core.reconcile import ReconcilePlan

        return ReconcilePlan(scope=scope)

    def test_no_removals_is_not_required_regardless_of_flags(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=False))
        plan = self._plan_without_removals()
        assert gate_consent(plan, auto_confirm=False) is ConsentOutcome.NOT_REQUIRED
        assert gate_consent(plan, auto_confirm=True) is ConsentOutcome.NOT_REQUIRED

    def test_auto_confirm_skips_prompt_when_removals_present(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))
        plan = self._plan_with_removals()
        assert gate_consent(plan, auto_confirm=True) is ConsentOutcome.NOT_REQUIRED

    def test_confirmed_via_tty_and_confirm_returns_apply(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))
        monkeypatch.setattr("remo_cli.core.reconcile.confirm", lambda *a, **k: True)
        plan = self._plan_with_removals()
        assert gate_consent(plan, auto_confirm=False) is ConsentOutcome.APPLY

    def test_declined_via_tty_and_confirm_returns_false(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))
        monkeypatch.setattr("remo_cli.core.reconcile.confirm", lambda *a, **k: False)
        plan = self._plan_with_removals()
        assert gate_consent(plan, auto_confirm=False) is ConsentOutcome.DECLINED

    def test_non_interactive_when_stdin_is_not_a_tty(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=False))
        plan = self._plan_with_removals()
        assert gate_consent(plan, auto_confirm=False) is ConsentOutcome.NON_INTERACTIVE


# ---------------------------------------------------------------------------
# run_sync: driver + exit codes (never 2)
# ---------------------------------------------------------------------------


class TestRunSyncExitCodes:
    def test_probe_error_returns_failure_and_does_not_write(self, tmp_config_dir, capsys):
        seed_registry(tmp_config_dir, [_kh("hetzner", "web1", host="1.1.1.1")])
        expected = read_registry().hosts
        scope = SyncScope(type="hetzner")

        from remo_cli.core.reconcile import ProbeError

        def failing_probe():
            raise ProbeError("could not reach hetzner API")

        rc = run_sync(scope, failing_probe)
        assert rc == 1
        assert rc != 2
        assert read_registry().hosts == expected
        assert "could not reach hetzner API" in capsys.readouterr().err

    def test_ambiguous_plan_returns_failure(self, tmp_config_dir):
        seed_registry(tmp_config_dir, [])
        scope = SyncScope(type="hetzner")
        dup_a = _dh(_kh("hetzner", "dup", host="1.1.1.1"))
        dup_b = _dh(_kh("hetzner", "dup", host="2.2.2.2"))
        rc = run_sync(scope, lambda: _probe([dup_a, dup_b]))
        assert rc == 1
        assert rc != 2

    def test_dry_run_returns_ok_and_does_not_write(self, tmp_config_dir, monkeypatch):
        seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="1.1.1.1")])
        expected = read_registry().hosts
        scope = SyncScope(type="hetzner")
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))

        rc = run_sync(scope, lambda: _probe([], complete=True), dry_run=True)
        assert rc == 0
        assert read_registry().hosts == expected

    def test_declined_returns_aborted_and_does_not_write(self, tmp_config_dir, monkeypatch):
        seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="1.1.1.1")])
        expected = read_registry().hosts
        scope = SyncScope(type="hetzner")
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))
        monkeypatch.setattr("remo_cli.core.reconcile.confirm", lambda *a, **k: False)

        rc = run_sync(scope, lambda: _probe([], complete=True))
        assert rc == 3
        assert rc != 2
        assert read_registry().hosts == expected

    def test_non_interactive_returns_aborted_and_does_not_write(self, tmp_config_dir, monkeypatch):
        seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="1.1.1.1")])
        expected = read_registry().hosts
        scope = SyncScope(type="hetzner")
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=False))

        rc = run_sync(scope, lambda: _probe([], complete=True))
        assert rc == 3
        assert rc != 2
        assert read_registry().hosts == expected

    def test_applied_returns_ok_and_writes_once(self, tmp_config_dir, monkeypatch):
        existing = _kh("hetzner", "gone", host="1.1.1.1")
        seed_registry(tmp_config_dir, [existing])
        scope = SyncScope(type="hetzner")

        rc = run_sync(scope, lambda: _probe([], complete=True), auto_confirm=True)
        assert rc == 0
        assert read_registry().hosts == []

    def test_conflict_returns_failure(self, tmp_config_dir, monkeypatch):
        existing = _kh("hetzner", "gone", host="1.1.1.1")
        seed_registry(tmp_config_dir, [existing])
        scope = SyncScope(type="hetzner")
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))

        def sneaky_confirm(*_a, **_k):
            # Simulate a concurrent change to the same scope landing between
            # plan-build and write: rewrite the registry mid-prompt.
            seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="9.9.9.9")])
            return True

        monkeypatch.setattr("remo_cli.core.reconcile.confirm", sneaky_confirm)

        rc = run_sync(scope, lambda: _probe([], complete=True))
        assert rc == 1
        assert rc != 2

    def test_no_exit_code_path_ever_returns_2(self, tmp_config_dir, monkeypatch):
        codes = []

        # failure
        from remo_cli.core.reconcile import ProbeError

        seed_registry(tmp_config_dir, [])
        codes.append(run_sync(SyncScope(type="hetzner"), lambda: (_ for _ in ()).throw(ProbeError("x"))))

        # ambiguous
        dup_a = _dh(_kh("hetzner", "dup"))
        dup_b = _dh(_kh("hetzner", "dup", host="2.2.2.2"))
        codes.append(run_sync(SyncScope(type="hetzner"), lambda: _probe([dup_a, dup_b])))

        # dry-run
        codes.append(
            run_sync(SyncScope(type="hetzner"), lambda: _probe([], complete=True), dry_run=True)
        )

        # declined / non-interactive / applied all need a removal candidate
        seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="1.1.1.1")])
        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=True))
        monkeypatch.setattr("remo_cli.core.reconcile.confirm", lambda *a, **k: False)
        codes.append(run_sync(SyncScope(type="hetzner"), lambda: _probe([], complete=True)))

        monkeypatch.setattr(sys, "stdin", FakeStdin(is_tty=False))
        codes.append(run_sync(SyncScope(type="hetzner"), lambda: _probe([], complete=True)))

        seed_registry(tmp_config_dir, [_kh("hetzner", "gone", host="1.1.1.1")])
        codes.append(
            run_sync(SyncScope(type="hetzner"), lambda: _probe([], complete=True), auto_confirm=True)
        )

        assert 2 not in codes


# ---------------------------------------------------------------------------
# apply_plan
# ---------------------------------------------------------------------------


class TestApplyPlan:
    def test_calls_mutate_registry_exactly_once(self, tmp_config_dir, monkeypatch):
        import remo_cli.core.reconcile as reconcile_module
        from remo_cli.core.registry import mutate_registry as real_mutate_registry

        seed_registry(tmp_config_dir, [_kh("incus", "node1/dev1", host="1.1.1.1")])
        current = read_registry().hosts
        scope = SyncScope(type="incus", host="node1")

        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            current, _probe([_dh(discovered_entry, marked=True)]), scope, include_all=False
        )

        call_count = 0

        def spy(mutator):
            nonlocal call_count
            call_count += 1
            return real_mutate_registry(mutator)

        monkeypatch.setattr(reconcile_module, "mutate_registry", spy)
        apply_plan(plan)
        assert call_count == 1

    def test_no_op_plan_does_not_write(self, tmp_config_dir, monkeypatch):
        import remo_cli.core.reconcile as reconcile_module

        existing = _kh("hetzner", "web1", host="1.1.1.1")
        seed_registry(tmp_config_dir, [existing])
        scope = SyncScope(type="hetzner")
        plan = build_plan([existing], _probe([_dh(existing, marked=True)]), scope, include_all=False)
        assert plan.is_noop is True

        called = False

        def spy(mutator):
            nonlocal called
            called = True
            raise AssertionError("mutate_registry must not be called for a no-op plan")

        monkeypatch.setattr(reconcile_module, "mutate_registry", spy)
        apply_plan(plan)
        assert called is False

    def test_out_of_scope_entries_preserved_byte_for_byte(self, tmp_config_dir):
        seed_registry(
            tmp_config_dir,
            [
                _kh("incus", "node1/dev1", host="1.1.1.1"),
                _kh(
                    "aws",
                    "prodbox",
                    host="9.9.9.9",
                    instance_id="i-xyz",
                    access_mode="ssm",
                    region="us-east-1",
                ),
            ],
        )
        current = read_registry().hosts
        out_of_scope = next(h for h in current if h.name == "prodbox")
        scope = SyncScope(type="incus", host="node1")

        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            current,
            _probe([_dh(discovered_entry, marked=True)]),
            scope,
            include_all=False,
        )
        apply_plan(plan)

        after = read_registry().hosts
        matching = [h for h in after if h.type == "aws" and h.name == "prodbox"]
        assert matching == [out_of_scope]

    def test_conflict_raised_when_same_scope_changes_on_disk(self, tmp_config_dir):
        seed_registry(tmp_config_dir, [_kh("incus", "node1/dev1", host="1.1.1.1")])
        current = read_registry().hosts
        scope = SyncScope(type="incus", host="node1")

        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            current, _probe([_dh(discovered_entry, marked=True)]), scope, include_all=False
        )

        # Simulate a concurrent write to the SAME scope between plan-build
        # and apply_plan.
        seed_registry(tmp_config_dir, [_kh("incus", "node1/dev1", host="3.3.3.3")])

        with pytest.raises(ReconcileConflictError):
            apply_plan(plan)

    def test_no_conflict_when_only_a_different_scope_changed_on_disk(self, tmp_config_dir):
        seed_registry(
            tmp_config_dir,
            [
                _kh("incus", "node1/dev1", host="1.1.1.1"),
                _kh("incus", "node2/dev2", host="5.5.5.5"),
            ],
        )
        current = read_registry().hosts
        in_scope_existing = next(h for h in current if h.name == "node1/dev1")
        scope = SyncScope(type="incus", host="node1")

        discovered_entry = _kh("incus", "node1/dev1", host="2.2.2.2")
        plan = build_plan(
            current,
            _probe([_dh(discovered_entry, marked=True)]),
            scope,
            include_all=False,
        )

        # Concurrent change to a DIFFERENT scope (node2) must not trip the
        # conflict check (FR-046/SC-016).
        seed_registry(
            tmp_config_dir,
            [in_scope_existing, _kh("incus", "node2/dev2", host="6.6.6.6")],
        )

        apply_plan(plan)  # must not raise

        after = read_registry().hosts
        dev1 = next(h for h in after if h.name == "node1/dev1")
        assert dev1.host == "2.2.2.2"
