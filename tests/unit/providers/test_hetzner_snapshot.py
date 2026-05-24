"""Tests for Hetzner snapshot business-logic (providers/hetzner.py snapshot_*)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from remo_cli.models.snapshot import Snapshot, SnapshotStatus
from remo_cli.providers import hetzner as providers_hetzner


SERVER_RESPONSE = {
    "servers": [
        {
            "id": 42,
            "name": "dev1",
            "public_net": {"ipv4": {"ip": "5.6.7.8"}},
        }
    ]
}


def _img(
    snap_id: int = 100,
    snap_name: str = "pre-x",
    status: str = "available",
    size_gb: int = 20,
    description: str = "",
) -> dict:
    return {
        "id": snap_id,
        "type": "snapshot",
        "status": status,
        "image_size": size_gb,
        "disk_size": size_gb,
        "description": description or f"remo snapshot of dev1",
        "created": "2026-05-24T10:15:30+00:00",
        "labels": {
            "remo": "true",
            "remo-snapshot-name": snap_name,
            "remo-source-server-id": "42",
        },
    }


@pytest.fixture
def api(mocker):
    """Patch the _hetzner_api transport. Returns the mock so tests set
    return-value or side-effect per call."""
    return mocker.patch(
        "remo_cli.providers.hetzner._hetzner_api", autospec=True
    )


# ---------------------------------------------------------------------------
# _list_snapshots_for_server
# ---------------------------------------------------------------------------


class TestListSnapshotsForServer:
    def test_available_image(self, api):
        api.return_value = {"images": [_img(status="available")]}
        result = providers_hetzner._list_snapshots_for_server(42, "dev1")  # noqa: SLF001
        assert len(result) == 1
        assert result[0].status is SnapshotStatus.AVAILABLE
        assert result[0].name == "pre-x"

    def test_creating_image_is_pending(self, api):
        api.return_value = {"images": [_img(status="creating")]}
        result = providers_hetzner._list_snapshots_for_server(42, "dev1")  # noqa: SLF001
        assert result[0].status is SnapshotStatus.PENDING

    def test_label_selector_includes_source_id(self, api):
        api.return_value = {"images": []}
        providers_hetzner._list_snapshots_for_server(42, "dev1")  # noqa: SLF001
        # URL-encoded path; decode the query string before asserting.
        import urllib.parse

        _, _, qs = api.call_args.args[1].partition("?")
        params = urllib.parse.parse_qs(qs)
        assert params["type"] == ["snapshot"]
        # label_selector is a single string containing comma-separated entries
        selector = params["label_selector"][0]
        assert "remo=true" in selector
        assert "remo-source-server-id=42" in selector


# ---------------------------------------------------------------------------
# snapshot_create
# ---------------------------------------------------------------------------


class TestSnapshotCreate:
    def test_happy_path_async_hint(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,  # GET /servers?name=dev1
            {"images": []},   # GET /images?... (existing list)
            {"action": {"id": 1}},  # POST create_image
        ]
        rc = providers_hetzner.snapshot_create(
            server_name="dev1",
            snap_name="pre-x",
            description="before x",
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "will take several minutes" in out
        # Verify the POST body included the right labels
        body = api.call_args_list[-1].args[2]
        assert body["type"] == "snapshot"
        labels = body["labels"]
        assert labels["remo"] == "true"
        assert labels["remo-snapshot-name"] == "pre-x"
        assert labels["remo-source-server-id"] == "42"

    def test_unknown_server(self, mocker, api, capsys):
        api.side_effect = [{"servers": []}]
        rc = providers_hetzner.snapshot_create(
            server_name="ghost", snap_name="pre-x"
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "No Hetzner server found" in err

    def test_duplicate_name(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img(snap_name="pre-x")]},
        ]
        rc = providers_hetzner.snapshot_create(
            server_name="dev1", snap_name="pre-x"
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "already exists" in err
        # No POST call was made
        post_calls = [c for c in api.call_args_list if c.args[0] == "POST"]
        assert post_calls == []


# ---------------------------------------------------------------------------
# snapshot_restore
# ---------------------------------------------------------------------------


class TestSnapshotRestore:
    def test_pending_fails_fast(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img(status="creating")]},
        ]
        rc = providers_hetzner.snapshot_restore(
            server_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "still pending" in err

    def test_missing(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": []},
        ]
        rc = providers_hetzner.snapshot_restore(
            server_name="dev1", snap_name="ghost", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_confirm_decline(self, mocker, api):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img()]},
        ]
        mocker.patch("remo_cli.providers.hetzner.confirm", return_value=False)
        rc = providers_hetzner.snapshot_restore(
            server_name="dev1", snap_name="pre-x", auto_confirm=False
        )
        assert rc == 1
        # No POST rebuild call
        post_calls = [c for c in api.call_args_list if c.args[0] == "POST"]
        assert post_calls == []

    def test_happy_path_rebuild_polls_action(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img(snap_id=100, status="available")]},
            {"action": {"id": 7}},   # POST rebuild
        ]
        mocker.patch(
            "remo_cli.providers.hetzner._wait_for_action", return_value=True
        )
        rc = providers_hetzner.snapshot_restore(
            server_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 0
        # POST rebuild called with the right image id
        post = next(c for c in api.call_args_list if c.args[0] == "POST")
        assert post.args[1].endswith("/actions/rebuild")
        assert post.args[2] == {"image": 100}
        out = capsys.readouterr().out
        assert "Restored 'pre-x'" in out

    def test_rebuild_action_fails(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img()]},
            {"action": {"id": 7}},
        ]
        mocker.patch(
            "remo_cli.providers.hetzner._wait_for_action", return_value=False
        )
        rc = providers_hetzner.snapshot_restore(
            server_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "did not complete successfully" in err


# ---------------------------------------------------------------------------
# snapshot_delete
# ---------------------------------------------------------------------------


class TestSnapshotDelete:
    def test_pending_fails_fast(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img(status="creating")]},
        ]
        rc = providers_hetzner.snapshot_delete(
            server_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "still pending" in err

    def test_missing(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": []},
        ]
        rc = providers_hetzner.snapshot_delete(
            server_name="dev1", snap_name="ghost", auto_confirm=True
        )
        assert rc == 1

    def test_happy_path(self, mocker, api, capsys):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img(snap_id=100)]},
            {},  # DELETE
        ]
        rc = providers_hetzner.snapshot_delete(
            server_name="dev1", snap_name="pre-x", auto_confirm=True
        )
        assert rc == 0
        delete_call = next(c for c in api.call_args_list if c.args[0] == "DELETE")
        assert delete_call.args[1] == "/images/100"

    def test_confirm_decline(self, mocker, api):
        api.side_effect = [
            SERVER_RESPONSE,
            {"images": [_img()]},
        ]
        mocker.patch("remo_cli.providers.hetzner.confirm", return_value=False)
        rc = providers_hetzner.snapshot_delete(
            server_name="dev1", snap_name="pre-x", auto_confirm=False
        )
        assert rc == 1
        delete_calls = [c for c in api.call_args_list if c.args[0] == "DELETE"]
        assert delete_calls == []
