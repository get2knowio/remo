"""Tests for the Proxmox managed-marker feature (providers/proxmox.py).

Covers tag apply (union, preserve, no-op), bulk tag read, and create/tag
wiring — including FR-003 (preserve tags), FR-010 (read-only), and FR-013
(bounded queries). All SSH is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.providers import proxmox as providers_proxmox
from remo_cli.models.host import KnownHost


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
# create() / tag() / upgrade() wiring
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
        result = providers_proxmox.create(name="dev1", host="node", node_user="root")
        assert result is None
        apply.assert_called_once_with("node", "root", "100")

    def test_tag_writes_marker_when_untagged(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch(
            "remo_cli.providers.proxmox._run_on_node",
            autospec=True,
            return_value=_completed(0, stdout="tags: mytag\n"),
        )
        apply = mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(True, ""),
        )
        result = providers_proxmox.tag(name="dev1", host="node", node_user="root")
        assert result is None
        # The already-parsed tag set is handed down so the write costs one SSH
        # round-trip, not a second `pct config` inside _apply_managed_marker.
        apply.assert_called_once_with("node", "root", "100", ["mytag"])

    def test_tag_is_a_noop_when_already_marked(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch(
            "remo_cli.providers.proxmox._run_on_node",
            autospec=True,
            return_value=_completed(0, stdout="tags: mytag;remo\n"),
        )
        apply = mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(True, ""),
        )
        result = providers_proxmox.tag(name="dev1", host="node", node_user="root")
        assert result is None
        apply.assert_not_called()

    def test_tag_raises_on_marker_write_failure(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch(
            "remo_cli.providers.proxmox._run_on_node",
            autospec=True,
            return_value=_completed(0, stdout="tags: mytag\n"),
        )
        mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(False, "pct set failed"),
        )
        with pytest.raises(OperationFailedError, match="pct set failed"):
            providers_proxmox.tag(name="dev1", host="node", node_user="root")

    def test_tag_raises_on_unresolvable_vmid(self, mocker):
        # Both the registry-cached lookup and the host-side fallback fail --
        # modeled here by mocking _resolve_vmid (which owns both paths) to
        # return "".
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="")
        node = mocker.patch("remo_cli.providers.proxmox._run_on_node", autospec=True)
        apply = mocker.patch("remo_cli.providers.proxmox._apply_managed_marker")
        with pytest.raises(PreconditionError, match="VMID"):
            providers_proxmox.tag(name="dev1", host="node", node_user="root")
        node.assert_not_called()
        apply.assert_not_called()

    def test_upgrade_never_touches_the_marker(self, mocker):
        """Neither `upgrade()` nor its `update_entry` wrapper writes
        provider-side managed-marker state (SC-001) -- tagging is now its own
        single-intent verb (`tag`).

        update_entry is the ONLY caller of upgrade() from `remo shell`
        (cli/shell.py). Tagging is a node-side write over SSH to the
        hypervisor -- a machine the user never named at the prompt -- so
        `upgrade`/`update_entry` never perform it (the post-migration notice
        points at `sync`/`tag` instead).
        """
        resolve_vmid = mocker.patch(
            "remo_cli.providers.proxmox._resolve_vmid", return_value="100"
        )
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

        providers_proxmox.upgrade(name="dev1", host="node", node_user="root")
        apply.assert_not_called()
        resolve_vmid.assert_not_called()

        entry = KnownHost(
            type="proxmox",
            name="node/dev1",
            host="10.0.0.9",
            user="remo",
            instance_id="100",
            access_mode="direct",
            region="root",
        )
        providers_proxmox.update_entry(entry)

        apply.assert_not_called()
        # The VMID is only needed for tagging or a resize; neither applies, so
        # the SSH round-trip that resolves it must not happen either.
        resolve_vmid.assert_not_called()


# ---------------------------------------------------------------------------
# sync()'s probe now lives in providers/proxmox.py `_probe` and is covered by
# tests/unit/providers/test_proxmox_sync.py (feature 016-sync-reconcile).
# The direct-write internals this class used to exercise (save_known_host
# called straight from sync(), clear_known_hosts_by_prefix) no longer exist:
# sync() delegates to core/reconcile.run_sync, which owns diffing, consent,
# and the single registry write.
# ---------------------------------------------------------------------------
