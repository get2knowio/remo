"""Tests for the Proxmox managed-marker feature (providers/proxmox.py).

Covers tag apply (union, preserve, no-op), bulk tag read, create/update wiring,
and filtered sync — including FR-003 (preserve tags), FR-010 (read-only), and
FR-013 (bounded queries). All SSH is mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


# ---------------------------------------------------------------------------
# create() / update() wiring
# ---------------------------------------------------------------------------


class TestCreateUpdateWiring:
    def test_create_marks_resolved_vmid(self, mocker):
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox.remove_known_host")
        mocker.patch("remo_cli.providers.proxmox.save_known_host")
        mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.providers.proxmox.get_current_version", return_value="unknown"
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
        rc = providers_proxmox.create(name="dev1", host="node", user="root")
        assert rc == 0
        apply.assert_called_once_with("node", "root", "100")

    def test_update_backfills_marker(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        mocker.patch(
            "remo_cli.providers.proxmox._resolve_container_ip", return_value="10.0.0.9"
        )
        mocker.patch("remo_cli.providers.proxmox.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.providers.proxmox.get_current_version", return_value="unknown"
        )
        mocker.patch(
            "remo_cli.providers.proxmox.resolve_devcontainer_runtime",
            return_value="devcontainer",
        )
        apply = mocker.patch(
            "remo_cli.providers.proxmox._apply_managed_marker",
            return_value=(True, ""),
        )
        rc = providers_proxmox.update(name="dev1", host="node", user="root")
        assert rc == 0
        apply.assert_called_once_with("node", "root", "100")


# ---------------------------------------------------------------------------
# sync() — filtering, hint, FR-010 (read-only), FR-013 (bounded)
# ---------------------------------------------------------------------------


_PCT_LIST = (
    "VMID       Status     Lock         Name\n"
    "100        running                 dev1\n"
    "101        running                 plex\n"
)


@pytest.fixture
def patch_registry(mocker):
    save = mocker.patch("remo_cli.providers.proxmox.save_known_host")
    mocker.patch("remo_cli.providers.proxmox.clear_known_hosts_by_prefix")
    return save


class TestSyncFiltering:
    def _wire_ssh(self, mocker):
        """Route `pct list` and the bulk conf dump through _ssh_run."""
        def side_effect(host, user, cmd):
            if cmd == "pct list":
                return _completed(0, stdout=_PCT_LIST)
            if cmd.startswith("for f in /etc/pve/lxc/"):
                return _completed(
                    0, stdout="@@@/etc/pve/lxc/100.conf\ntags: remo\n"
                )
            return _completed(0)

        return mocker.patch(
            "remo_cli.providers.proxmox._ssh_run", side_effect=side_effect
        )

    def test_default_registers_only_marked(self, patch_registry, mocker):
        self._wire_ssh(mocker)
        mocker.patch("remo_cli.providers.proxmox.print_info")
        warn = mocker.patch("remo_cli.providers.proxmox.print_warning")

        providers_proxmox.sync(host="node", user="root")

        saved = [c.args[0].name for c in patch_registry.call_args_list]
        assert saved == ["node/dev1"]
        warn_text = " ".join(str(c.args[0]) for c in warn.call_args_list)
        assert "plex" in warn_text

    def test_all_registers_everything(self, patch_registry, mocker):
        self._wire_ssh(mocker)
        mocker.patch("remo_cli.providers.proxmox.print_info")
        mocker.patch("remo_cli.providers.proxmox.print_warning")

        providers_proxmox.sync(host="node", user="root", include_all=True)

        saved = sorted(c.args[0].name for c in patch_registry.call_args_list)
        assert saved == ["node/dev1", "node/plex"]

    def test_read_only_and_bounded(self, patch_registry, mocker):
        ssh = self._wire_ssh(mocker)
        mocker.patch("remo_cli.providers.proxmox.print_info")
        mocker.patch("remo_cli.providers.proxmox.print_warning")

        providers_proxmox.sync(host="node", user="root")

        # FR-013: two bulk calls (pct list + one grep), no per-container loop.
        assert ssh.call_count == 2
        # FR-010: no marker mutation during sync.
        for call in ssh.call_args_list:
            assert "pct set" not in call.args[2]

    def test_registry_shape_unchanged(self, patch_registry, mocker):
        # FR-012: the KnownHost written by sync keeps its pre-feature fields.
        self._wire_ssh(mocker)
        mocker.patch("remo_cli.providers.proxmox.print_info")
        mocker.patch("remo_cli.providers.proxmox.print_warning")

        providers_proxmox.sync(host="node", user="root")

        kh = patch_registry.call_args.args[0]
        assert kh.type == "proxmox"
        assert kh.name == "node/dev1"
        assert kh.user == "remo"
        assert kh.instance_id == "100"
        assert kh.region == "root"
        assert kh.access_mode == "direct"
        assert not hasattr(kh, "marker")
        assert not hasattr(kh, "managed")
