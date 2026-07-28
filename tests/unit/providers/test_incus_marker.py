"""Tests for the Incus managed-marker feature (providers/incus.py).

Covers marker apply/read helpers and create/update wiring. `sync()` itself
is now a thin wrapper over the reconcile engine (016-sync-reconcile); its
probe is covered by tests/unit/providers/test_incus_sync.py and the
read-only / consent / write behaviour by tests/integration/test_sync_reconcile.py.
All SSH is mocked; no live Incus host is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.providers import incus as providers_incus
from remo_cli.models.host import KnownHost


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
        with pytest.raises(OperationFailedError):
            providers_incus._list_containers_with_marker("h", "u")


# ---------------------------------------------------------------------------
# create() wiring
# ---------------------------------------------------------------------------


class TestCreateMarks:
    def test_create_applies_marker(self, mocker):
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.remove_known_host")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="unknown"
        )
        apply = mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(True, ""),
        )
        providers_incus.create(name="dev1", host="h", host_user="u")
        apply.assert_called_once_with("h", "u", "dev1")

    def test_marker_failure_warns_but_create_succeeds(self, mocker):
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.providers.incus.remove_known_host")
        mocker.patch("remo_cli.providers.incus.save_known_host")
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="unknown"
        )
        mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(False, "denied"),
        )
        warn = mocker.patch("remo_cli.providers.incus.print_warning")
        providers_incus.create(name="dev1", host="h", host_user="u")  # FR-005: create still succeeds
        assert warn.called


# ---------------------------------------------------------------------------
# tag() wiring (021-cli-plane-separation: managed-marker write, split out of
# the former three-intent `update`)
# ---------------------------------------------------------------------------


class TestTag:
    def test_tag_untagged_writes_marker_once(self, mocker, patch_host):
        # First call: read-before-write pre-check (not yet tagged).
        patch_host.return_value = _completed(0, stdout="")
        apply = mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(True, ""),
        )
        providers_incus.tag(name="dev1", host="h", host_user="u")
        apply.assert_called_once_with("h", "u", "dev1")

    def test_tag_already_tagged_is_noop(self, mocker, patch_host):
        patch_host.return_value = _completed(0, stdout="true")
        apply = mocker.patch("remo_cli.providers.incus._apply_managed_marker")
        # Must not raise, and must perform zero writes.
        providers_incus.tag(name="dev1", host="h", host_user="u")
        apply.assert_not_called()

    def test_tag_write_failure_raises(self, mocker, patch_host):
        patch_host.return_value = _completed(0, stdout="")
        mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(False, "denied"),
        )
        # Unlike create()'s best-effort marker, tag() fails the command on a
        # write failure rather than merely warning.
        with pytest.raises(OperationFailedError):
            providers_incus.tag(name="dev1", host="h", host_user="u")


# ---------------------------------------------------------------------------
# upgrade() / update_entry() invariant: never touch the managed marker
# (SC-001 — zero provider-side writes from the tools-refresh path).
# ---------------------------------------------------------------------------


class TestUpgradeDoesNotTouchMarker:
    """`remo incus upgrade` and `remo shell`'s tools-update path must not
    write host-side managed-marker state — that's `tag`'s job now."""

    def _patch_upgrade_internals(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus._resolve_container_ip", return_value="10.0.0.9"
        )
        mocker.patch("remo_cli.providers.incus.run_playbook", return_value=0)
        mocker.patch("remo_cli.core.ssh.detect_timezone", return_value="")
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="unknown"
        )
        return mocker.patch(
            "remo_cli.providers.incus._apply_managed_marker",
            return_value=(True, ""),
        )

    def test_update_entry_never_applies_marker(self, mocker):
        apply = self._patch_upgrade_internals(mocker)
        entry = KnownHost(
            type="incus",
            name="myhost/dev1",
            host="10.0.0.9",
            user="remo",
            instance_id="paul",
            access_mode="direct",
            region="",
        )
        providers_incus.update_entry(entry)
        apply.assert_not_called()

    def test_upgrade_never_applies_marker(self, mocker):
        apply = self._patch_upgrade_internals(mocker)
        providers_incus.upgrade(name="dev1", host="myhost", host_user="paul")
        apply.assert_not_called()
