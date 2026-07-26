"""Tests for the Hetzner sync-reconcile probe (providers/hetzner.py `_probe`).

Hetzner's `sync` used to build `?label_selector=remo` by hand with no
pagination, so a plain `remo hetzner sync` always queried zero servers and
the old clear-then-repopulate logic wiped the entire registry every run
(FR-044). `_probe` is the provider's only contribution to the new
reconcile-based `sync()`: the diffing, consent, and write logic live in
`core/reconcile.py`. All HTTP is mocked via `_hetzner_api`; no live Hetzner
project is required.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.core.reconcile import ProbeError, SyncScope, build_plan
from remo_cli.models.host import KnownHost
from remo_cli.providers import hetzner as providers_hetzner


@pytest.fixture
def scope() -> SyncScope:
    return SyncScope(type="hetzner")


def _server(
    name: str,
    ip: str | None = "1.2.3.4",
    labels: dict | None = None,
    status: str = "running",
) -> dict:
    server: dict = {"name": name, "status": status, "labels": labels or {}}
    if ip is not None:
        server["public_net"] = {"ipv4": {"ip": ip}}
    return server


def _page(servers: list[dict], next_page: int | None) -> dict:
    return {"servers": servers, "meta": {"pagination": {"next_page": next_page}}}


# ---------------------------------------------------------------------------
# Pagination (T024/T029)
# ---------------------------------------------------------------------------


class TestProbePagination:
    def test_two_page_walk_to_exhaustion_returns_all_servers_and_complete(
        self, scope, mocker
    ):
        page1 = _page([_server("dev1"), _server("dev2")], next_page=2)
        page2 = _page([_server("dev3")], next_page=None)
        patched = mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[page1, page2]
        )

        result = providers_hetzner._probe(scope, include_all=False)

        assert {h.entry.name for h in result.hosts} == {"dev1", "dev2", "dev3"}
        assert result.complete is True
        assert result.incomplete_reason == ""
        assert patched.call_count == 2

    def test_first_page_failure_raises_probe_error(self, scope, mocker):
        mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=OperationFailedError("boom")
        )

        with pytest.raises(ProbeError):
            providers_hetzner._probe(scope, include_all=False)

    def test_second_page_failure_yields_complete_false_without_probe_error(
        self, scope, mocker
    ):
        page1 = _page([_server("dev1"), _server("dev2")], next_page=2)
        mocker.patch.object(
            providers_hetzner,
            "_hetzner_api",
            side_effect=[page1, OperationFailedError("boom")],
        )

        # Partial-but-answered must not raise -- only a first-page failure
        # (we could not ask at all) does.
        result = providers_hetzner._probe(scope, include_all=False)

        assert result.complete is False
        assert result.incomplete_reason
        # What was gathered on the successful first page is preserved.
        assert {h.entry.name for h in result.hosts} == {"dev1", "dev2"}

    def test_query_never_includes_label_selector(self, scope, mocker):
        page1 = _page([_server("dev1")], next_page=None)
        patched = mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[page1]
        )

        providers_hetzner._probe(scope, include_all=False)

        for call in patched.call_args_list:
            method, path = call.args[0], call.args[1]
            assert method == "GET"
            assert "label_selector" not in path
            assert path.startswith("/servers")


# ---------------------------------------------------------------------------
# Entry shape / classification (T025)
# ---------------------------------------------------------------------------


class TestProbeClassification:
    def test_server_without_ipv4_is_still_included_with_empty_host(self, scope, mocker):
        page = _page([_server("noip", ip=None)], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)

        assert len(result.hosts) == 1
        assert result.hosts[0].entry.name == "noip"
        assert result.hosts[0].entry.host == ""

    def test_marked_true_when_remo_label_true(self, scope, mocker):
        page = _page([_server("dev1", labels={"remo": "true"})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)

        assert result.hosts[0].marked is True

    def test_marked_false_when_label_absent(self, scope, mocker):
        page = _page([_server("dev1", labels={})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)

        assert result.hosts[0].marked is False

    def test_marked_false_when_label_present_with_other_value(self, scope, mocker):
        page = _page([_server("dev1", labels={"remo": "false"})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)

        assert result.hosts[0].marked is False

    def test_entry_shape_matches_create(self, scope, mocker):
        page = _page([_server("dev1", ip="5.6.7.8")], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)
        entry = result.hosts[0].entry

        assert entry.type == "hetzner"
        assert entry.name == "dev1"
        assert entry.host == "5.6.7.8"
        assert entry.user == "remo"
        assert entry.instance_id == ""
        assert entry.access_mode == ""
        assert entry.region == ""

    def test_state_reflects_server_status(self, scope, mocker):
        page = _page([_server("dev1", status="stopped")], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=False)

        assert result.hosts[0].state == "stopped"

    def test_adoption_criteria_names_the_whole_project(self, scope, mocker):
        page = _page([_server("dev1")], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        result = providers_hetzner._probe(scope, include_all=True)

        assert result.adoption_criteria == "every server in this Hetzner project"

    def test_include_all_does_not_change_what_probe_returns(self, scope, mocker):
        page = _page([_server("dev1", labels={"remo": "true"}), _server("dev2")], next_page=None)
        mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[page, _page(page["servers"], None)]
        )

        without_all = providers_hetzner._probe(scope, include_all=False)
        with_all = providers_hetzner._probe(scope, include_all=True)

        assert {h.entry.name for h in without_all.hosts} == {
            h.entry.name for h in with_all.hosts
        }


# ---------------------------------------------------------------------------
# Adoption (T060, US5): --all widens eligibility for a new, unmarked server
# to every server in the project (R7 -- Hetzner has no naming convention to
# narrow on, unlike AWS's remo-* prefix).
# ---------------------------------------------------------------------------


class TestAdoption:
    def test_skipped_without_all_for_new_unmarked_server(self, scope, mocker):
        page = _page([_server("dev1", labels={})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        probe = providers_hetzner._probe(scope, include_all=False)
        plan = build_plan([], probe, scope, include_all=False)

        assert plan.added == []
        assert plan.skipped_unmarked == ["dev1"]

    def test_adopted_with_all_for_new_unmarked_server(self, scope, mocker):
        page = _page([_server("dev1", labels={})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        probe = providers_hetzner._probe(scope, include_all=True)
        plan = build_plan([], probe, scope, include_all=True)

        assert {h.name for h in plan.added} == {"dev1"}
        assert plan.skipped_unmarked == []
        assert probe.adoption_criteria == "every server in this Hetzner project"


# ---------------------------------------------------------------------------
# Marker-independence regression (T030, FR-044, SC-015)
#
# This is the direct regression guard for the bug this phase exists to fix:
# the old sync's server-side `label_selector=remo` query always returned
# zero servers (nothing in the codebase ever applied that label), so every
# invocation wiped the entire Hetzner registry. An unlabelled-but-live
# server must be retained, never proposed for removal, purely because the
# probe still *saw* it.
# ---------------------------------------------------------------------------


class TestMarkerIndependenceRegression:
    def test_unlabelled_but_live_server_is_retained_not_removed(self, scope, mocker):
        existing = KnownHost(type="hetzner", name="legacy", host="9.9.9.9", user="remo")
        page = _page([_server("legacy", ip="9.9.9.9", labels={})], next_page=None)
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        probe = providers_hetzner._probe(scope, include_all=False)
        plan = build_plan([existing], probe, scope, include_all=False)

        removed_names = {h.name for h in plan.removed}
        assert "legacy" not in removed_names
        retained_or_unchanged = set(plan.retained_unmarked) | {
            h.name for h in plan.unchanged
        }
        assert "legacy" in retained_or_unchanged

    def test_a_plain_sync_no_longer_wipes_the_registry(self, scope, mocker):
        # The exact failure mode fixed by this phase: previously the query
        # was `?label_selector=remo`, which matched nothing remo ever
        # created, so `probe.hosts` was always empty and every entry looked
        # deletable. Now the (unfiltered) query returns the live server, so
        # it survives even though it carries no label at all.
        existing = [
            KnownHost(type="hetzner", name="alpha", host="1.1.1.1", user="remo"),
            KnownHost(type="hetzner", name="beta", host="2.2.2.2", user="remo"),
        ]
        page = _page(
            [
                _server("alpha", ip="1.1.1.1", labels={}),
                _server("beta", ip="2.2.2.2", labels={}),
            ],
            next_page=None,
        )
        mocker.patch.object(providers_hetzner, "_hetzner_api", side_effect=[page])

        probe = providers_hetzner._probe(scope, include_all=False)
        plan = build_plan(existing, probe, scope, include_all=False)

        assert plan.removed == []
