"""Unit tests for offline drift + the shared nudge (017-web-adopt-simplify US2).

Covers `core/web_drift.py`:

* `diff_registry_against_cache` classification (new/changed/removed/in_sync),
  reusing the same fingerprint the push mirrors.
* `select_deployment` (implicit single, explicit-required multi with the known
  ids listed, unknown selector error).
* `out_of_date_notice` gating: non-None iff a non-empty push cache exists.
* `build_drift_report` / `DriftReport` convenience counts.
"""

from __future__ import annotations

import pytest

from remo_cli.core.web_adopt import (
    CachedInstance,
    DeploymentCache,
    instance_fingerprint,
    save_push_cache,
)
from remo_cli.core.web_drift import (
    DriftError,
    DriftState,
    build_drift_report,
    diff_registry_against_cache,
    out_of_date_notice,
    select_deployment,
)
from remo_cli.models.host import KnownHost

DEP_A = "dep-aaaa1111"
DEP_B = "dep-bbbb2222"


def _host(name="node1/dev", host="10.0.0.1", type_="incus", user="remo", **kw):
    return KnownHost(type=type_, name=name, host=host, user=user, **kw)


def _entry(host: KnownHost) -> CachedInstance:
    return CachedInstance(
        fingerprint=instance_fingerprint(host),
        host_keys=["line"],
        host=host.host,
        user=host.user,
        access=host.access_mode or "direct",
        type=host.type,
    )


# ---------------------------------------------------------------------------
# diff_registry_against_cache
# ---------------------------------------------------------------------------


class TestDiff:
    def test_in_sync_when_fingerprint_matches(self):
        host = _host()
        entries = diff_registry_against_cache([host], {host.name: _entry(host)})
        assert [(e.name, e.state) for e in entries] == [(host.name, DriftState.IN_SYNC)]

    def test_new_when_not_in_cache(self):
        host = _host()
        entries = diff_registry_against_cache([host], {})
        assert entries[0].state is DriftState.NEW

    def test_changed_when_fingerprint_differs(self):
        cached_host = _host()
        current = _host(host="10.0.0.99")  # different host -> different fingerprint
        entries = diff_registry_against_cache([current], {current.name: _entry(cached_host)})
        assert entries[0].state is DriftState.CHANGED

    def test_removed_when_in_cache_but_not_registry(self):
        gone = _host(name="gone", host="5.6.7.8", type_="hetzner")
        entries = diff_registry_against_cache([], {"gone": _entry(gone)})
        assert [(e.name, e.state, e.type) for e in entries] == [
            ("gone", DriftState.REMOVED, "hetzner")
        ]

    def test_mixed_one_of_each(self):
        keep = _host(name="keep")
        change_cached = _host(name="chg")
        change_now = _host(name="chg", host="10.9.9.9")
        new = _host(name="new", host="2.2.2.2", type_="hetzner")
        gone = _host(name="gone", host="3.3.3.3", type_="hetzner")
        cache = {
            "keep": _entry(keep),
            "chg": _entry(change_cached),
            "gone": _entry(gone),
        }
        entries = diff_registry_against_cache([keep, change_now, new], cache)
        by_name = {e.name: e.state for e in entries}
        assert by_name == {
            "keep": DriftState.IN_SYNC,
            "chg": DriftState.CHANGED,
            "new": DriftState.NEW,
            "gone": DriftState.REMOVED,
        }

    def test_entries_sorted_by_name(self):
        a = _host(name="aaa", host="1.1.1.1")
        z = _host(name="zzz", host="2.2.2.2")
        entries = diff_registry_against_cache([z, a], {})
        assert [e.name for e in entries] == ["aaa", "zzz"]


# ---------------------------------------------------------------------------
# DriftReport / build_drift_report
# ---------------------------------------------------------------------------


class TestDriftReport:
    def test_counts_and_is_in_sync(self, tmp_config_dir):
        keep = _host(name="keep")
        cache = {DEP_A: DeploymentCache(instances={"keep": _entry(keep)})}
        report = build_drift_report(DEP_A, cache, [keep])
        assert report.deployment_id == DEP_A
        assert report.is_in_sync is True
        assert (len(report.new), len(report.changed), len(report.removed)) == (0, 0, 0)

    def test_not_in_sync_with_drift(self, tmp_config_dir):
        keep = _host(name="keep")
        gone = _host(name="gone", host="9.9.9.9", type_="hetzner")
        new = _host(name="new", host="2.2.2.2", type_="hetzner")
        cache = {DEP_A: DeploymentCache(instances={"keep": _entry(keep), "gone": _entry(gone)})}
        report = build_drift_report(DEP_A, cache, [keep, new])
        assert report.is_in_sync is False
        assert len(report.new) == 1 and len(report.removed) == 1


# ---------------------------------------------------------------------------
# select_deployment
# ---------------------------------------------------------------------------


class TestSelectDeployment:
    def test_implicit_single(self):
        cache = {DEP_A: DeploymentCache(mirror_generation=1)}
        assert select_deployment(cache, None) == DEP_A

    def test_explicit_selector_matches(self):
        cache = {DEP_A: DeploymentCache(mirror_generation=1), DEP_B: DeploymentCache(mirror_generation=1)}
        assert select_deployment(cache, DEP_B) == DEP_B

    def test_multi_without_selector_errors_listing_ids(self):
        cache = {DEP_A: DeploymentCache(mirror_generation=1), DEP_B: DeploymentCache(mirror_generation=1)}
        with pytest.raises(DriftError) as exc:
            select_deployment(cache, None)
        message = str(exc.value)
        assert DEP_A in message and DEP_B in message
        assert "--deployment" in message

    def test_unknown_selector_errors_listing_ids(self):
        cache = {DEP_A: DeploymentCache(mirror_generation=1)}
        with pytest.raises(DriftError) as exc:
            select_deployment(cache, "nope")
        assert DEP_A in str(exc.value)

    def test_empty_cache_errors(self):
        with pytest.raises(DriftError):
            select_deployment({}, None)


# ---------------------------------------------------------------------------
# out_of_date_notice gating
# ---------------------------------------------------------------------------


class TestOutOfDateNotice:
    def test_none_when_no_cache(self, tmp_config_dir):
        assert out_of_date_notice() is None

    def test_notice_when_cache_has_a_deployment(self, tmp_config_dir):
        host = _host()
        save_push_cache({DEP_A: DeploymentCache(instances={host.name: _entry(host)})})
        notice = out_of_date_notice()
        assert notice is not None
        assert "out of date" in notice
        assert "remo web status" in notice and "remo web push" in notice

    def test_none_when_cache_file_is_empty_junk(self, tmp_config_dir):
        # A file that parses to no valid deployments is treated as empty.
        from remo_cli.core.web_adopt import push_cache_path

        push_cache_path().write_text("{}")
        assert out_of_date_notice() is None
