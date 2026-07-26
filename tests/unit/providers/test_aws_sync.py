"""Tests for the AWS sync-reconcile probe (providers/aws.py `_probe`).

AWS's old `sync` did a single-shot `describe_instances` filtered on
`tag:remo=true` AND `instance-state-name=running`, then cleared *every*
existing AWS registry entry regardless of region before re-populating from
that one-region query -- so a bare `remo aws sync` (defaulting to
`us-west-2`) destroyed every other region's entries, and a stopped instance
(excluded by the `running` filter) was destroyed by the very next sync.
`_probe` is the provider's only contribution to the new reconcile-based
`sync()`: the diffing, consent, and write logic all live in
`core/reconcile.py`. All boto3 calls are mocked; no live AWS account is
required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.reconcile import (
    DiscoveredHost,
    ProbeError,
    ProbeResult,
    SyncScope,
    build_plan,
)
from remo_cli.models.host import KnownHost
from remo_cli.providers import aws as providers_aws


@pytest.fixture
def ec2(mocker):
    """Stub `_boto3_session(...).client('ec2')` to return a MagicMock."""
    ec2_client = MagicMock()
    session = MagicMock()
    session.client.return_value = ec2_client
    mocker.patch("remo_cli.providers.aws._boto3_session", return_value=session)
    return ec2_client


@pytest.fixture
def scope() -> SyncScope:
    return SyncScope(type="aws", region="us-west-2")


def _instance(
    instance_id: str = "i-1",
    state: str = "running",
    ip: str | None = "1.2.3.4",
    tags: dict | None = None,
) -> dict:
    instance: dict = {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": [{"Key": k, "Value": v} for k, v in (tags or {}).items()],
    }
    if ip is not None:
        instance["PublicIpAddress"] = ip
    return instance


def _page(instances: list[dict]) -> dict:
    return {"Reservations": [{"Instances": instances}]}


# ---------------------------------------------------------------------------
# Pagination (T031/T035)
# ---------------------------------------------------------------------------


class TestProbePagination:
    def test_two_page_walk_to_exhaustion_returns_all_and_complete(self, ec2, scope):
        page1 = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        page2 = _page([_instance("i-2", tags={"remo_resource_name": "dev2"})])
        ec2.get_paginator.return_value.paginate.return_value = [page1, page2]

        result = providers_aws._probe(scope, include_all=False)

        assert {h.entry.name for h in result.hosts} == {"dev1", "dev2"}
        assert result.complete is True
        assert result.incomplete_reason == ""

    def test_first_call_failure_raises_probe_error(self, ec2, scope):
        ec2.get_paginator.return_value.paginate.side_effect = RuntimeError("boom")

        with pytest.raises(ProbeError):
            providers_aws._probe(scope, include_all=False)

    def test_second_page_failure_yields_complete_false_without_probe_error(
        self, ec2, scope
    ):
        page1 = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])

        def _pages():
            yield page1
            raise RuntimeError("boom")

        ec2.get_paginator.return_value.paginate.return_value = _pages()

        # Partial-but-answered must not raise -- only a first-call failure
        # (we could not ask at all) does.
        result = providers_aws._probe(scope, include_all=False)

        assert result.complete is False
        assert result.incomplete_reason
        assert {h.entry.name for h in result.hosts} == {"dev1"}

    def test_query_filters_on_state_only_never_tag_remo(self, ec2, scope):
        ec2.get_paginator.return_value.paginate.return_value = [_page([])]

        providers_aws._probe(scope, include_all=False)

        ec2.get_paginator.assert_called_with("describe_instances")
        _, kwargs = ec2.get_paginator.return_value.paginate.call_args
        filters = kwargs["Filters"]
        assert len(filters) == 1
        assert filters[0]["Name"] == "instance-state-name"
        assert set(filters[0]["Values"]) == {
            "pending",
            "running",
            "stopping",
            "stopped",
        }
        assert not any(f["Name"] == "tag:remo" for f in filters)


# ---------------------------------------------------------------------------
# Entry shape / classification (T032)
# ---------------------------------------------------------------------------


class TestProbeClassification:
    def test_marked_true_when_tag_remo_true(self, ec2, scope):
        page = _page(
            [_instance("i-1", tags={"remo": "true", "remo_resource_name": "dev1"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].marked is True

    def test_marked_false_when_tag_remo_absent(self, ec2, scope):
        page = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].marked is False

    def test_marked_false_when_tag_remo_other_value(self, ec2, scope):
        page = _page(
            [_instance("i-1", tags={"remo": "false", "remo_resource_name": "dev1"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].marked is False

    def test_entry_shape_matches_create(self, ec2, scope):
        page = _page(
            [
                _instance(
                    "i-1",
                    ip="5.6.7.8",
                    tags={
                        "remo_resource_name": "dev1",
                        "remo_access_mode": "direct",
                    },
                )
            ]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        entry = result.hosts[0].entry
        assert entry.type == "aws"
        assert entry.name == "dev1"
        assert entry.host == "5.6.7.8"
        assert entry.user == "remo"
        assert entry.instance_id == "i-1"
        assert entry.access_mode == "direct"
        assert entry.region == "us-west-2"

    def test_access_mode_defaults_to_ssm(self, ec2, scope):
        page = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].entry.access_mode == "ssm"

    def test_adoption_criteria_is_set(self, ec2, scope):
        page = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=True)
        assert result.adoption_criteria == (
            "also matching instances named remo-* without the remo tag"
        )

    def test_adopted_true_for_remo_dash_prefixed_name_tag(self, ec2, scope):
        # Widens under --all (R7) -- but only for instances actually named
        # by the remo convention, not any untagged instance in the region.
        page = _page(
            [_instance("i-1", tags={"remo_resource_name": "dev1", "Name": "remo-dev1"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=True)
        assert result.hosts[0].adopted is True

    def test_adopted_false_for_unrelated_name_tag(self, ec2, scope):
        page = _page(
            [_instance("i-1", tags={"remo_resource_name": "dev1", "Name": "some-other-box"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=True)
        assert result.hosts[0].adopted is False

    def test_all_only_adds_remo_dash_named_instance_via_build_plan(self, ec2, scope):
        # End-to-end proof of the narrowing: an unmarked, untagged instance
        # with an unrelated Name is never swept in by --all; a remo-*-named
        # one is.
        page = _page(
            [
                _instance("i-1", tags={"remo_resource_name": "dev1", "Name": "remo-dev1"}),
                _instance(
                    "i-2", tags={"remo_resource_name": "prodbox", "Name": "some-other-box"}
                ),
            ]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        probe = providers_aws._probe(scope, include_all=True)
        plan = build_plan([], probe, scope, include_all=True)
        assert {h.name for h in plan.added} == {"dev1"}
        assert plan.skipped_unmarked == ["prodbox"]

    def test_include_all_does_not_change_what_probe_returns(self, ec2, scope):
        page = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        without_all = providers_aws._probe(scope, include_all=False)

        page = _page([_instance("i-1", tags={"remo_resource_name": "dev1"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        with_all = providers_aws._probe(scope, include_all=True)

        assert {h.entry.name for h in without_all.hosts} == {
            h.entry.name for h in with_all.hosts
        }

    def test_running_state_is_not_annotated(self, ec2, scope):
        page = _page(
            [_instance("i-1", state="running", tags={"remo_resource_name": "dev1"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].state == ""

    def test_non_running_state_is_populated(self, ec2, scope):
        page = _page(
            [_instance("i-1", state="pending", tags={"remo_resource_name": "dev1"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].state == "pending"


# ---------------------------------------------------------------------------
# Name derivation (R8)
# ---------------------------------------------------------------------------


class TestNameDerivation:
    def test_prefers_remo_resource_name_tag(self, ec2, scope):
        page = _page(
            [
                _instance(
                    "i-1",
                    tags={"remo_resource_name": "dev1", "Name": "remo-other"},
                )
            ]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].entry.name == "dev1"

    def test_falls_back_to_name_tag_minus_remo_prefix(self, ec2, scope):
        page = _page([_instance("i-1", tags={"Name": "remo-dev2"})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts[0].entry.name == "dev2"

    def test_instance_with_neither_tag_is_skipped(self, ec2, scope):
        page = _page([_instance("i-1", tags={})])
        ec2.get_paginator.return_value.paginate.return_value = [page]
        result = providers_aws._probe(scope, include_all=False)
        assert result.hosts == []


# ---------------------------------------------------------------------------
# Region-boundary asymmetry (T035, US2)
#
# A region-less legacy entry matches every region's *update* scope (so a
# hit in the queried region self-heals it) but can never be proposed for
# *removal* while region-less -- this is SyncScope.in_update_scope vs
# in_removal_scope, already implemented in core/reconcile.py. Tested at the
# build_plan level per the task guidance, to keep it fast and precise.
# ---------------------------------------------------------------------------


class TestRegionScopingAsymmetry:
    def test_region_less_entry_is_matched_and_stamped_when_discovered(self, scope):
        existing = KnownHost(
            type="aws",
            name="legacy",
            host="1.1.1.1",
            user="remo",
            instance_id="i-legacy",
            access_mode="ssm",
            region="",
        )
        discovered_entry = KnownHost(
            type="aws",
            name="legacy",
            host="1.1.1.1",
            user="remo",
            instance_id="i-legacy",
            access_mode="ssm",
            region="us-west-2",
        )
        probe = ProbeResult(
            hosts=[DiscoveredHost(entry=discovered_entry, marked=True)],
            complete=True,
        )

        plan = build_plan([existing], probe, scope, include_all=False)

        updated = {after.name: after for _before, after in plan.updated}
        assert "legacy" in updated
        assert updated["legacy"].region == "us-west-2"

    def test_region_less_entry_never_removed_even_when_absent_from_probe(self, scope):
        existing = KnownHost(
            type="aws",
            name="legacy",
            host="1.1.1.1",
            user="remo",
            instance_id="i-legacy",
            access_mode="ssm",
            region="",
        )
        probe = ProbeResult(hosts=[], complete=True)

        plan = build_plan([existing], probe, scope, include_all=False)

        assert "legacy" not in {h.name for h in plan.removed}
        assert "legacy" in {h.name for h in plan.unchanged}

    def test_out_of_region_entry_absent_from_probe_still_never_removed(self, scope):
        # An entry correctly stamped for a *different* region is out of
        # in_update_scope entirely (region != "" and != scope.region), so it
        # is not even a candidate in this region's plan -- the direct
        # region-wipe regression guard.
        other_region = KnownHost(
            type="aws",
            name="eastbox",
            host="2.2.2.2",
            user="remo",
            instance_id="i-east",
            access_mode="ssm",
            region="eu-central-1",
        )
        probe = ProbeResult(hosts=[], complete=True)

        plan = build_plan([other_region], probe, scope, include_all=False)

        assert plan.removed == []
        assert plan.unchanged == []
        assert plan.updated == []
        assert plan.added == []


# ---------------------------------------------------------------------------
# Marker-independence regression (FR-044, SC-015)
# ---------------------------------------------------------------------------


class TestMarkerIndependenceRegression:
    def test_untagged_but_live_instance_is_retained_not_removed(self, ec2, scope):
        existing = KnownHost(
            type="aws",
            name="legacy",
            host="9.9.9.9",
            user="remo",
            instance_id="i-legacy",
            access_mode="ssm",
            region="us-west-2",
        )
        # No tag:remo=true -- the old server-side filter would have made
        # this instance invisible and proposed it for deletion.
        page = _page(
            [_instance("i-legacy", ip="9.9.9.9", tags={"remo_resource_name": "legacy"})]
        )
        ec2.get_paginator.return_value.paginate.return_value = [page]

        probe = providers_aws._probe(scope, include_all=False)
        plan = build_plan([existing], probe, scope, include_all=False)

        assert "legacy" not in {h.name for h in plan.removed}
        retained_or_unchanged = set(plan.retained_unmarked) | {
            h.name for h in plan.unchanged
        }
        assert "legacy" in retained_or_unchanged


# ---------------------------------------------------------------------------
# Stopped / terminated instances (T038-T042, US3)
# ---------------------------------------------------------------------------


class TestStoppedInstance:
    def test_stopped_instance_has_empty_host_and_reports_state(self, ec2, scope):
        # Real AWS responses omit PublicIpAddress entirely rather than
        # sending an empty string.
        instance = _instance(
            "i-1", state="stopped", ip=None, tags={"remo_resource_name": "dev1"}
        )
        assert "PublicIpAddress" not in instance
        ec2.get_paginator.return_value.paginate.return_value = [_page([instance])]

        result = providers_aws._probe(scope, include_all=False)

        assert len(result.hosts) == 1
        host = result.hosts[0]
        assert host.entry.host == ""
        assert host.state == "stopped"

    def test_stopped_instance_preserves_last_known_address_via_merge(
        self, ec2, scope
    ):
        existing = KnownHost(
            type="aws",
            name="dev1",
            host="1.2.3.4",
            user="remo",
            instance_id="i-1",
            access_mode="ssm",
            region="us-west-2",
        )
        instance = _instance(
            "i-1",
            state="stopped",
            ip=None,
            tags={"remo_resource_name": "dev1", "remo": "true"},
        )
        ec2.get_paginator.return_value.paginate.return_value = [_page([instance])]

        probe = providers_aws._probe(scope, include_all=False)
        plan = build_plan([existing], probe, scope, include_all=False)

        merged_by_name = {after.name: after for _before, after in plan.updated}
        merged_by_name.update({h.name: h for h in plan.unchanged})
        assert merged_by_name["dev1"].host == "1.2.3.4"
        assert merged_by_name["dev1"].region == "us-west-2"
        assert plan.states.get("dev1") == "stopped"
        # FR-019: reported, never persisted -- KnownHost carries no state field.
        assert not hasattr(merged_by_name["dev1"], "state")

    def test_terminated_instance_correctly_lands_in_removed(self, scope):
        # The state filter already excludes shutting-down/terminated
        # server-side, so a terminated instance simply never appears in a
        # probe result -- exercised here as absence rather than a synthetic
        # terminated payload, since AWS would never return one to us given
        # the filter.
        existing = KnownHost(
            type="aws",
            name="gone",
            host="1.1.1.1",
            user="remo",
            instance_id="i-gone",
            access_mode="ssm",
            region="us-west-2",
        )
        probe = ProbeResult(hosts=[], complete=True)

        plan = build_plan([existing], probe, scope, include_all=False)

        assert "gone" in {h.name for h in plan.removed}
