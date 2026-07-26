"""Tests for the Proxmox managed-marker feature (providers/proxmox.py).

Covers tag apply (union, preserve, no-op), bulk tag read, and create/update
wiring — including FR-003 (preserve tags), FR-010 (read-only), and FR-013
(bounded queries). All SSH is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.providers import proxmox as providers_proxmox


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


# ---------------------------------------------------------------------------
# _apply_managed_marker — union, preserve, idempotent no-op
# ---------------------------------------------------------------------------


class TestApplyMarker:
    def test_appends_remo_preserving_existing_tags(self, mocker):
        node = mocker.patch(
            "remo_cli.providers.proxmox._run_on_node", autospec=True
        )
        node.side_effect = [
            _completed(0, stdout="hostname: dev1\ntags: mytag\ncores: 2\n"),
            _completed(0),  # pct set
        ]
        ok, err = providers_proxmox._apply_managed_marker("h", "u", "100")
        assert ok is True and err == ""
        set_cmd = node.call_args_list[1].args[2]
        # existing tag preserved + appended (shell-quoted for the ; separator)
        assert "--tags 'mytag;remo'" in set_cmd

    def test_noop_when_already_marked(self, mocker):
        node = mocker.patch(
            "remo_cli.providers.proxmox._run_on_node", autospec=True
        )
        node.return_value = _completed(0, stdout="tags: mytag;remo\n")
        ok, err = providers_proxmox._apply_managed_marker("h", "u", "100")
        assert ok is True
        # FR-002/SC-005: no `pct set` issued — only the config read.
        assert node.call_count == 1

    def test_empty_vmid_is_a_soft_failure(self, mocker):
        node = mocker.patch("remo_cli.providers.proxmox._run_on_node")
        ok, err = providers_proxmox._apply_managed_marker("h", "u", "")
        assert ok is False and err
        node.assert_not_called()


# ---------------------------------------------------------------------------
# _read_tags_by_vmid — one bulk grep
# ---------------------------------------------------------------------------


class TestReadTags:
    def test_parses_conf_dump(self, mocker):
        node = mocker.patch(
            "remo_cli.providers.proxmox._run_on_node", autospec=True
        )
        node.return_value = _completed(
            0,
            stdout=(
                "@@@/etc/pve/lxc/100.conf\n"
                "arch: amd64\n"
                "tags: remo\n"
                "@@@/etc/pve/lxc/101.conf\n"
                "tags: media;plex\n"
            ),
        )
        mapping = providers_proxmox._read_tags_by_vmid("h", "u")
        assert mapping == {"100": {"remo"}, "101": {"media", "plex"}}
        assert node.call_count == 1  # FR-013

    def test_ignores_snapshot_section_tags(self, mocker):
        # A snapshot section's tags: line must NOT shadow the live tags —
        # regression for the grep-last-wins mis-classification bug.
        node = mocker.patch(
            "remo_cli.providers.proxmox._run_on_node", autospec=True
        )
        node.return_value = _completed(
            0,
            stdout=(
                "@@@/etc/pve/lxc/100.conf\n"
                "tags: media;remo\n"        # current: marked
                "[pre-upgrade]\n"
                "tags: media\n"             # old snapshot: no remo
                "@@@/etc/pve/lxc/101.conf\n"
                "tags: media\n"             # current: unmarked
                "[snap]\n"
                "tags: media;remo\n"        # old snapshot: had remo
            ),
        )
        mapping = providers_proxmox._read_tags_by_vmid("h", "u")
        assert mapping == {"100": {"media", "remo"}, "101": {"media"}}

    def test_nonzero_returncode_raises_instead_of_reporting_empty(self, mocker):
        # T044/research.md R5 #1: an SSH failure must never be interpreted
        # as "no container has any tags" -- that reads every container as
        # unmarked and a default sync would wipe the node's registry slice.
        node = mocker.patch(
            "remo_cli.providers.proxmox._run_on_node", autospec=True
        )
        node.return_value = _completed(1, stderr="ssh: connection refused")
        with pytest.raises(OperationFailedError, match="connection refused"):
            providers_proxmox._read_tags_by_vmid("h", "u")


# ---------------------------------------------------------------------------
# create() / update() wiring
# ---------------------------------------------------------------------------


class TestCreateUpdateWiring:
    def test_create_marks_resolved_vmid(self, mocker):
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        mocker.patch("remo_cli.providers.proxmox.save_known_host")
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="unknown"
        )
        mocker.patch(
            "remo_cli.providers.proxmox.resolve_devcontainer_runtime",
            return_value="devcontainer",
        )
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        apply = mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(True, ""),
        )
        result = providers_proxmox.create(name="dev1", host="node", user="root")
        assert result is None
        apply.assert_called_once_with("node", "root", "100")

    def test_update_backfills_marker(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip", return_value="10.0.0.9"
        )
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="unknown"
        )
        mocker.patch(
            "remo_cli.providers.proxmox.resolve_devcontainer_runtime",
            return_value="devcontainer",
        )
        apply = mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(True, ""),
        )
        result = providers_proxmox.update(name="dev1", host="node", user="root")
        assert result is None
        apply.assert_called_once_with("node", "root", "100")


# ---------------------------------------------------------------------------
# sync()'s probe now lives in providers/proxmox.py `_probe` and is covered by
# tests/unit/providers/test_proxmox_sync.py (feature 016-sync-reconcile).
# The direct-write internals this class used to exercise (save_known_host
# called straight from sync(), clear_known_hosts_by_prefix) no longer exist:
# sync() delegates to core/reconcile.run_sync, which owns diffing, consent,
# and the single registry write.
# ---------------------------------------------------------------------------
