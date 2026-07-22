"""Tests for the Incus managed-marker feature (providers/incus.py).

Covers marker apply/read helpers, create/update wiring, and filtered sync —
including FR-010 (sync is read-only) and FR-013 (sync makes a bounded number of
host queries). All SSH is mocked; no live Incus host is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.providers import incus as providers_incus


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


@pytest.fixture
def patch_host(mocker):
    """Patch the per-host SSH helper used for all marker host commands."""
    return mocker.patch(
        "remo_cli.providers.incus._ssh_run_on_incus_host", autospec=True
    )


# ---------------------------------------------------------------------------
# _apply_managed_marker
# ---------------------------------------------------------------------------


class TestApplyMarker:
    def test_runs_incus_config_set(self, patch_host):
        patch_host.return_value = _completed(0)
        ok, err = providers_incus._apply_managed_marker("h", "u", "dev1")
        assert ok is True
        assert err == ""
        cmd = patch_host.call_args.args[2]
        assert cmd == "incus config set dev1 user.remo=true"

    def test_failure_returns_message_not_exception(self, patch_host):
        patch_host.return_value = _completed(1, stderr="boom")
        ok, err = providers_incus._apply_managed_marker("h", "u", "dev1")
        assert ok is False
        assert "boom" in err


# ---------------------------------------------------------------------------
# _list_containers_with_marker
# ---------------------------------------------------------------------------


class TestListWithMarker:
    def test_parses_marked_and_unmarked(self, patch_host):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        rows = providers_incus._list_containers_with_marker("h", "u")
        assert rows == [("dev1", True), ("plex", False)]

    def test_uses_single_bulk_query(self, patch_host):
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        providers_incus._list_containers_with_marker("h", "u")
        assert patch_host.call_count == 1  # FR-013: one bulk query
        assert "incus list -f csv -c n,user.remo" in patch_host.call_args.args[2]

    def test_failure_raises(self, patch_host):
        patch_host.return_value = _completed(1, stderr="nope")
        with pytest.raises(RuntimeError):
            providers_incus._list_containers_with_marker("h", "u")


# ---------------------------------------------------------------------------
# create() wiring
# ---------------------------------------------------------------------------


class TestCreateMarks:
    def test_create_applies_marker(self, mocker):
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.remove_known_host")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.providers.incus.get_current_version", return_value="unknown"
        )
        apply = mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(True, ""),
        )
        rc = providers_incus.create(name="dev1", host="h", user="u")
        assert rc == 0
        apply.assert_called_once_with("h", "u", "dev1")

    def test_marker_failure_warns_but_create_succeeds(self, mocker):
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.remove_known_host")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.providers.incus.get_current_version", return_value="unknown"
        )
        mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(False, "denied"),
        )
        warn = mocker.patch("remo_cli.providers.incus.print_warning")
        rc = providers_incus.create(name="dev1", host="h", user="u")
        assert rc == 0  # FR-005: create still succeeds
        assert warn.called


# ---------------------------------------------------------------------------
# update() wiring (backfill)
# ---------------------------------------------------------------------------


class TestUpdateBackfill:
    def test_update_applies_marker(self, mocker):
        apply = mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(True, ""),
        )
        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", return_value="10.0.0.5"
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.providers.incus.get_current_version", return_value="unknown"
        )
        rc = providers_incus.update(name="dev1", host="h", user="u")
        assert rc == 0
        apply.assert_called_once_with("h", "u", "dev1")


# ---------------------------------------------------------------------------
# sync() — filtering, hint, FR-010 (read-only), FR-013 (bounded)
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_registry(mocker):
    save = mocker.patch("remo_cli.providers.incus.save_known_host")
    mocker.patch("remo_cli.providers.incus.clear_known_hosts_by_prefix")
    return save


class TestSyncFiltering:
    def test_default_registers_only_marked(self, patch_host, patch_registry, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        info = mocker.patch("remo_cli.providers.incus.print_info")
        warn = mocker.patch("remo_cli.providers.incus.print_warning")

        providers_incus.sync(host="h", user="u")

        # Only dev1 registered.
        saved = [c.args[0].name for c in patch_registry.call_args_list]
        assert saved == ["h/dev1"]
        # Hint names the skipped container.
        warn_text = " ".join(str(c.args[0]) for c in warn.call_args_list)
        assert "plex" in warn_text
        info_text = " ".join(str(c.args[0]) for c in info.call_args_list)
        assert "--all" in info_text and "remo incus update" in info_text

    def test_all_registers_everything(self, patch_host, patch_registry, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        warn = mocker.patch("remo_cli.providers.incus.print_warning")
        mocker.patch("remo_cli.providers.incus.print_info")

        providers_incus.sync(host="h", user="u", include_all=True)

        saved = sorted(c.args[0].name for c in patch_registry.call_args_list)
        assert saved == ["h/dev1", "h/plex"]
        warn_text = " ".join(str(c.args[0]) for c in warn.call_args_list)
        assert "plex" in warn_text
        # The adopted-unmarked summary must actually be emitted (FR-009).
        assert "not remo-created" in warn_text

    def test_sync_is_read_only_and_bounded(self, patch_host, patch_registry, mocker):
        patch_host.return_value = _completed(0, stdout="dev1,true\nplex,\n")
        mocker.patch("remo_cli.providers.incus.print_info")
        mocker.patch("remo_cli.providers.incus.print_warning")

        providers_incus.sync(host="h", user="u")

        # FR-013: a single bulk host query, regardless of container count.
        assert patch_host.call_count == 1
        # FR-010: sync issues no marker mutation.
        for call in patch_host.call_args_list:
            assert "config set" not in call.args[2]

    def test_registry_shape_unchanged(self, patch_host, patch_registry, mocker):
        # FR-012: the KnownHost written by sync keeps its pre-feature fields;
        # marker state is not recorded in the registry.
        patch_host.return_value = _completed(0, stdout="dev1,true\n")
        mocker.patch("remo_cli.providers.incus.print_info")
        mocker.patch("remo_cli.providers.incus.print_warning")

        providers_incus.sync(host="h", user="u")

        kh = patch_registry.call_args.args[0]
        assert kh.type == "incus"
        assert kh.name == "h/dev1"
        assert kh.user == "remo"
        assert kh.instance_id == "u"
        assert kh.access_mode == "direct"
        # No marker attribute leaked onto the registry entry.
        assert not hasattr(kh, "marker")
        assert not hasattr(kh, "managed")
