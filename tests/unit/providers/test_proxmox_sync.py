"""Tests for the Proxmox sync-reconcile probe (providers/proxmox.py `_probe`).

Covers marked/unmarked classification from `pct list` + the bulk tag dump,
the SSH-failure regression guard for `_read_tags_by_vmid` (research.md R5 #1
-- an SSH failure must raise ProbeError, never silently report zero marked
containers), `pct list` failure, `--use-ip` soft IP-lookup failure, and
read-only behaviour. `_probe` is the provider's only contribution to
`sync()`: the diffing, consent, and write logic all live in
`core/reconcile.py` and are exercised end-to-end in
tests/integration/test_sync_reconcile.py. All SSH is mocked; no live Proxmox
node is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.reconcile import ProbeError, SyncScope
from remo_cli.providers import proxmox as providers_proxmox


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


_PCT_LIST = (
    "VMID       Status     Lock         Name\n"
    "100        running                 dev1\n"
    "101        running                 plex\n"
    "102        stopped                 web\n"
)

_TAG_DUMP = (
    "@@@/etc/pve/lxc/100.conf\n"
    "arch: amd64\n"
    "tags: remo\n"
    "@@@/etc/pve/lxc/101.conf\n"
    "arch: amd64\n"
    "@@@/etc/pve/lxc/102.conf\n"
    "tags: media\n"
)


def _wire_ssh(mocker, pct_list_result=None, tag_dump_result=None):
    """Route `pct list` and the bulk conf dump through `_run_on_node`."""

    def side_effect(host, user, cmd):
        if cmd == "pct list":
            return pct_list_result if pct_list_result is not None else _completed(
                0, stdout=_PCT_LIST
            )
        if cmd.startswith("for f in /etc/pve/lxc/"):
            return tag_dump_result if tag_dump_result is not None else _completed(
                0, stdout=_TAG_DUMP
            )
        return _completed(0)

    return mocker.patch(
        "remo_cli.providers.proxmox._run_on_node", side_effect=side_effect
    )


@pytest.fixture
def scope() -> SyncScope:
    return SyncScope(type="proxmox", host="node")


# ---------------------------------------------------------------------------
# Marked / unmarked classification
# ---------------------------------------------------------------------------


class TestProbeClassification:
    def test_returns_marked_and_unmarked_alike(self, scope, mocker):
        _wire_ssh(mocker)
        result = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=False
        )

        by_name = {h.entry.name: h for h in result.hosts}
        assert set(by_name) == {"node/dev1", "node/plex", "node/web"}
        assert by_name["node/dev1"].marked is True
        assert by_name["node/plex"].marked is False
        assert by_name["node/web"].marked is False

    def test_include_all_does_not_change_what_probe_returns(self, scope, mocker):
        _wire_ssh(mocker)

        without_all = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=False
        )
        with_all = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=True
        )

        assert {h.entry.name for h in without_all.hosts} == {
            h.entry.name for h in with_all.hosts
        }

    def test_adoption_criteria_is_set(self, scope, mocker):
        _wire_ssh(mocker)
        result = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=True
        )
        assert result.adoption_criteria

    def test_complete_is_always_true(self, scope, mocker):
        _wire_ssh(mocker)
        result = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=False
        )
        assert result.complete is True

    def test_entry_shape_matches_create(self, scope, mocker):
        _wire_ssh(mocker)
        result = providers_proxmox._probe(
            scope, user="root", use_ip=False, include_all=False
        )
        by_name = {h.entry.name: h for h in result.hosts}
        entry = by_name["node/dev1"].entry
        assert entry.type == "proxmox"
        assert entry.name == "node/dev1"
        assert entry.host == "dev1"
        assert entry.user == "remo"
        assert entry.instance_id == "100"
        assert entry.access_mode == "direct"
        assert entry.region == "root"

    def test_region_defaults_to_root_when_no_user_given(self, scope, mocker):
        _wire_ssh(mocker)
        result = providers_proxmox._probe(
            scope, user="", use_ip=False, include_all=False
        )
        assert result.hosts[0].entry.region == "root"


# ---------------------------------------------------------------------------
# The SSH-failure regression guard (research.md R5 #1 / T044)
# ---------------------------------------------------------------------------


class TestProbeTagReadFailure:
    def test_tag_dump_failure_raises_probe_error_not_zero_marked(self, scope, mocker):
        # This is the exact bug this phase fixes: a non-zero returncode on
        # the tag-read command must never be swallowed into "nothing is
        # marked" -- that would make a default sync propose deleting every
        # container on the node.
        _wire_ssh(mocker, tag_dump_result=_completed(255, stderr="ssh: connection refused"))
        with pytest.raises(ProbeError):
            providers_proxmox._probe(
                scope, user="root", use_ip=False, include_all=False
            )

    def test_pct_list_failure_also_raises_probe_error(self, scope, mocker):
        _wire_ssh(mocker, pct_list_result=_completed(1, stderr="boom"))
        with pytest.raises(ProbeError):
            providers_proxmox._probe(
                scope, user="root", use_ip=False, include_all=False
            )


# ---------------------------------------------------------------------------
# --use-ip soft failure
# ---------------------------------------------------------------------------


class TestProbeUseIp:
    def test_use_ip_resolves_addresses(self, scope, mocker):
        _wire_ssh(mocker)
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip",
            return_value="10.0.0.5",
        )
        result = providers_proxmox._probe(
            scope, user="root", use_ip=True, include_all=False
        )
        by_name = {h.entry.name: h for h in result.hosts}
        assert by_name["node/dev1"].entry.host == "10.0.0.5"

    def test_soft_ip_failure_keeps_host_and_warns(self, scope, mocker):
        _wire_ssh(mocker)
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip", return_value=""
        )
        result = providers_proxmox._probe(
            scope, user="root", use_ip=True, include_all=False
        )

        # No container is dropped; each entry carries an empty host so
        # merge_entry preserves the previously recorded address.
        names = {h.entry.name for h in result.hosts}
        assert names == {"node/dev1", "node/plex", "node/web"}
        for h in result.hosts:
            assert h.entry.host == ""
        assert len(result.warnings) == 3

    def test_partial_ip_failure_only_warns_for_failed_container(self, scope, mocker):
        _wire_ssh(mocker)

        def fake_resolve(name, host, user, vmid=""):
            return "10.0.0.5" if name == "dev1" else ""

        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip",
            side_effect=fake_resolve,
        )
        result = providers_proxmox._probe(
            scope, user="root", use_ip=True, include_all=False
        )

        by_name = {h.entry.name: h for h in result.hosts}
        assert by_name["node/dev1"].entry.host == "10.0.0.5"
        assert by_name["node/plex"].entry.host == ""
        assert by_name["node/web"].entry.host == ""
        assert len(result.warnings) == 2


# ---------------------------------------------------------------------------
# Read-only behaviour
# ---------------------------------------------------------------------------


class TestProbeReadOnly:
    def test_probe_never_issues_pct_set(self, scope, mocker):
        node = _wire_ssh(mocker)
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip",
            return_value="10.0.0.5",
        )
        providers_proxmox._probe(scope, user="root", use_ip=True, include_all=True)

        for call in node.call_args_list:
            cmd = call.args[2]
            assert "pct set" not in cmd

    def test_probe_issues_exactly_two_bulk_calls(self, scope, mocker):
        # FR-013: one `pct list` + one bulk tag dump, no per-container loop
        # (the --use-ip lookups go through _resolve_container_ip, mocked
        # here, so they don't inflate this count).
        node = _wire_ssh(mocker)
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip",
            return_value="10.0.0.5",
        )
        providers_proxmox._probe(scope, user="root", use_ip=True, include_all=False)
        assert node.call_count == 2
