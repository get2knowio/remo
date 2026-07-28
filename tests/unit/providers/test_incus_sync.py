"""Tests for the Incus sync-reconcile probe (providers/incus.py `_probe`).

Covers marked/unmarked classification, `--use-ip` soft IP-lookup failure,
listing failure -> ProbeError, and read-only behaviour. `_probe` is the
provider's only contribution to `sync()`: the diffing, consent, and write
logic all live in `core/reconcile.py` and are exercised end-to-end in
tests/integration/test_sync_reconcile.py. All SSH is mocked; no live Incus
host is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.reconcile import ProbeError, SyncScope
from remo_cli.providers import incus as providers_incus


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


@pytest.fixture
def patch_host(mocker):
    """Patch the per-host SSH helper used by both listing and IP lookup."""
    return mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", autospec=True
    )


@pytest.fixture
def scope() -> SyncScope:
    return SyncScope(type="incus", host="h")


# ---------------------------------------------------------------------------
# Marked / unmarked classification
# ---------------------------------------------------------------------------


class TestProbeClassification:
    def test_returns_marked_and_unmarked_alike(self, patch_host, scope):
        patch_host.return_value = _completed(
            0, stdout="dev1,true\nplex,\nweb,false\n"
        )
        result = providers_incus._probe(scope, host_user="u", use_ip=False, include_all=False)

        by_name = {h.entry.name: h for h in result.hosts}
        assert set(by_name) == {"h/dev1", "h/plex", "h/web"}
        assert by_name["h/dev1"].marked is True
        assert by_name["h/plex"].marked is False
        assert by_name["h/web"].marked is False

    def test_include_all_does_not_change_what_probe_returns(self, patch_host, scope):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")

        without_all = providers_incus._probe(
            scope, host_user="u", use_ip=False, include_all=False
        )
        with_all = providers_incus._probe(
            scope, host_user="u", use_ip=False, include_all=True
        )

        assert {h.entry.name for h in without_all.hosts} == {
            h.entry.name for h in with_all.hosts
        }

    def test_adoption_criteria_is_set(self, patch_host, scope):
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        result = providers_incus._probe(scope, host_user="u", use_ip=False, include_all=True)
        assert result.adoption_criteria

    def test_complete_is_always_true(self, patch_host, scope):
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        result = providers_incus._probe(scope, host_user="u", use_ip=False, include_all=False)
        assert result.complete is True

    def test_entry_shape_matches_create(self, patch_host, scope):
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        result = providers_incus._probe(scope, host_user="u", use_ip=False, include_all=False)
        entry = result.hosts[0].entry
        assert entry.type == "incus"
        assert entry.name == "h/dev1"
        assert entry.host == "dev1"
        assert entry.user == "remo"
        assert entry.instance_id == "u"
        assert entry.access_mode == "direct"


# ---------------------------------------------------------------------------
# --use-ip soft failure
# ---------------------------------------------------------------------------


class TestProbeUseIp:
    def test_use_ip_resolves_addresses(self, patch_host, scope, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", return_value="10.0.0.5"
        )
        result = providers_incus._probe(scope, host_user="u", use_ip=True, include_all=False)
        assert result.hosts[0].entry.host == "10.0.0.5"
        assert result.hosts[0].entry.name == "h/dev1"

    def test_soft_ip_failure_keeps_host_and_warns(self, patch_host, scope, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", return_value=""
        )
        result = providers_incus._probe(scope, host_user="u", use_ip=True, include_all=False)

        # Neither container was dropped, both entries carry an empty host so
        # merge_entry preserves the previously recorded address.
        names = {h.entry.name for h in result.hosts}
        assert names == {"h/dev1", "h/plex"}
        for h in result.hosts:
            assert h.entry.host == ""
        assert len(result.warnings) == 2
        assert any("dev1" in w for w in result.warnings)
        assert any("plex" in w for w in result.warnings)

    def test_partial_ip_failure_only_warns_for_failed_container(
        self, patch_host, scope, mocker
    ):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")

        def fake_resolve(name, host, user):
            return "10.0.0.5" if name == "dev1" else ""

        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", side_effect=fake_resolve
        )
        result = providers_incus._probe(scope, host_user="u", use_ip=True, include_all=False)

        by_name = {h.entry.name: h for h in result.hosts}
        assert by_name["h/dev1"].entry.host == "10.0.0.5"
        assert by_name["h/plex"].entry.host == ""
        assert len(result.warnings) == 1
        assert "plex" in result.warnings[0]


# ---------------------------------------------------------------------------
# Listing failure
# ---------------------------------------------------------------------------


class TestProbeListingFailure:
    def test_listing_failure_raises_probe_error(self, patch_host, scope):
        patch_host.return_value = _completed(1, stderr="boom")
        with pytest.raises(ProbeError):
            providers_incus._probe(scope, host_user="u", use_ip=False, include_all=False)


# ---------------------------------------------------------------------------
# Read-only behaviour
# ---------------------------------------------------------------------------


class TestProbeReadOnly:
    def test_probe_never_issues_config_set(self, patch_host, scope, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", return_value="10.0.0.5"
        )
        providers_incus._probe(scope, host_user="u", use_ip=True, include_all=True)

        for call in patch_host.call_args_list:
            cmd = call.args[2]
            assert "config set" not in cmd
