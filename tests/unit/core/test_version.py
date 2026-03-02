"""Tests for remo_cli.core.version module."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock

import pytest

from remo_cli.core.version import (
    _parse_version,
    check_for_updates_passive,
    get_current_version,
    get_latest_release,
    version_is_newer,
)


class TestParseVersion:
    """Tests for _parse_version()."""

    def test_simple_version_with_v_prefix(self):
        assert _parse_version("v1.2.3") == (1, 2, 3, 999999)

    def test_simple_version_without_v_prefix(self):
        assert _parse_version("1.2.3") == (1, 2, 3, 999999)

    def test_rc_version(self):
        assert _parse_version("1.2.3-rc.1") == (1, 2, 3, 1)

    def test_rc_version_with_v_prefix(self):
        assert _parse_version("v1.2.3-rc.1") == (1, 2, 3, 1)

    def test_v0_8_0(self):
        assert _parse_version("v0.8.0") == (0, 8, 0, 999999)

    def test_invalid_returns_zeros(self):
        assert _parse_version("invalid") == (0, 0, 0, 0)

    def test_empty_string_returns_zeros(self):
        assert _parse_version("") == (0, 0, 0, 0)

    def test_release_sorts_above_rc(self):
        release = _parse_version("v1.0.0")
        rc = _parse_version("v1.0.0-rc.1")
        assert release > rc

    def test_higher_rc_sorts_above_lower_rc(self):
        rc2 = _parse_version("v1.0.0-rc.2")
        rc1 = _parse_version("v1.0.0-rc.1")
        assert rc2 > rc1

    def test_whitespace_stripped(self):
        assert _parse_version("  v1.2.3  ") == (1, 2, 3, 999999)

    def test_large_version_numbers(self):
        assert _parse_version("v99.88.77") == (99, 88, 77, 999999)

    def test_zero_version(self):
        assert _parse_version("v0.0.0") == (0, 0, 0, 999999)

    def test_rc_zero(self):
        assert _parse_version("v1.0.0-rc.0") == (1, 0, 0, 0)


class TestVersionIsNewer:
    """Tests for version_is_newer()."""

    def test_higher_minor_is_newer(self):
        assert version_is_newer("v1.1.0", "v1.0.0") is True

    def test_lower_minor_is_not_newer(self):
        assert version_is_newer("v1.0.0", "v1.1.0") is False

    def test_higher_major_is_newer(self):
        assert version_is_newer("v2.0.0", "v1.9.9") is True

    def test_higher_patch_is_newer(self):
        assert version_is_newer("v1.0.1", "v1.0.0") is True

    def test_same_version_is_not_newer(self):
        assert version_is_newer("v1.0.0", "v1.0.0") is False

    def test_rc2_newer_than_rc1(self):
        assert version_is_newer("v1.0.0-rc.2", "v1.0.0-rc.1") is True

    def test_release_newer_than_rc(self):
        assert version_is_newer("v1.0.0", "v1.0.0-rc.1") is True

    def test_rc_not_newer_than_release(self):
        assert version_is_newer("v1.0.0-rc.1", "v1.0.0") is False

    def test_without_v_prefix(self):
        assert version_is_newer("1.1.0", "1.0.0") is True

    def test_mixed_v_prefix(self):
        assert version_is_newer("v1.1.0", "1.0.0") is True


class TestGetCurrentVersion:
    """Tests for get_current_version()."""

    def test_returns_version_from_metadata(self, mocker):
        mocker.patch("importlib.metadata.version", return_value="0.8.0")
        result = get_current_version()
        assert result == "0.8.0"

    def test_returns_unknown_on_package_not_found(self, mocker):
        from importlib.metadata import PackageNotFoundError

        mocker.patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("remo-cli"),
        )
        result = get_current_version()
        assert result == "unknown"

    def test_returns_unknown_on_unexpected_exception(self, mocker):
        mocker.patch(
            "importlib.metadata.version",
            side_effect=RuntimeError("unexpected"),
        )
        result = get_current_version()
        assert result == "unknown"


class TestGetLatestRelease:
    """Tests for get_latest_release() via PyPI API."""

    def _mock_pypi_response(self, mocker, versions):
        """Helper to mock PyPI JSON API response."""
        data = {"releases": {v: [] for v in versions}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mocker.patch("remo_cli.core.version.urllib.request.urlopen", return_value=mock_resp)

    def test_returns_latest_stable(self, mocker):
        self._mock_pypi_response(mocker, ["1.0.0", "1.1.0", "1.2.0"])
        result = get_latest_release(include_prerelease=False)
        assert result == "1.2.0"

    def test_excludes_prerelease_by_default(self, mocker):
        self._mock_pypi_response(mocker, ["1.0.0", "1.1.0", "1.2.0-rc.1"])
        result = get_latest_release(include_prerelease=False)
        assert result == "1.1.0"

    def test_includes_prerelease_when_requested(self, mocker):
        self._mock_pypi_response(mocker, ["1.0.0", "1.1.0", "1.2.0-rc.1"])
        result = get_latest_release(include_prerelease=True)
        assert result == "1.2.0-rc.1"

    def test_returns_empty_on_network_error(self, mocker):
        mocker.patch(
            "remo_cli.core.version.urllib.request.urlopen",
            side_effect=ConnectionError("no network"),
        )
        result = get_latest_release()
        assert result == ""

    def test_returns_empty_when_no_releases(self, mocker):
        self._mock_pypi_response(mocker, [])
        result = get_latest_release()
        assert result == ""

    def test_returns_empty_when_only_prereleases_and_stable_requested(self, mocker):
        self._mock_pypi_response(mocker, ["1.0.0-rc.1", "1.0.0-beta.1"])
        result = get_latest_release(include_prerelease=False)
        assert result == ""


class TestCheckForUpdatesPassive:
    """Tests for check_for_updates_passive()."""

    def test_returns_none_when_version_unknown(self, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")

        result = check_for_updates_passive()
        assert result is None

    def test_returns_hint_when_cache_has_newer_version(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        # Create a fresh cache file with a newer version
        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("v1.1.0")

        result = check_for_updates_passive()
        assert result is not None
        assert "1.1.0" in result
        assert "Update available" in result
        assert "remo self-update" in result
        assert "uv tool upgrade remo-cli" in result

    def test_returns_none_when_up_to_date(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("v1.0.0")

        result = check_for_updates_passive()
        assert result is None

    def test_returns_none_when_cache_older(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.1.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("v1.0.0")

        result = check_for_updates_passive()
        assert result is None

    def test_spawns_background_thread_when_cache_stale(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)
        mocker.patch("remo_cli.core.version.get_latest_release", return_value="1.1.0")

        # Create a stale cache file (old mtime)
        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("1.0.0")
        stale_time = time.time() - 90000  # more than 24 hours ago
        os.utime(cache_file, (stale_time, stale_time))

        mock_thread_class = mocker.patch("remo_cli.core.version.threading.Thread")
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance

        result = check_for_updates_passive()
        assert result is None
        mock_thread_class.assert_called_once()
        mock_thread_instance.start.assert_called_once()
        mock_thread_class.assert_called_once_with(target=mocker.ANY, daemon=True)

    def test_spawns_background_thread_when_no_cache(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        # No cache file exists
        mock_thread_class = mocker.patch("remo_cli.core.version.threading.Thread")
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance

        result = check_for_updates_passive()
        assert result is None
        mock_thread_class.assert_called_once()
        mock_thread_instance.start.assert_called_once()

    def test_never_raises_exceptions(self, mocker):
        mocker.patch(
            "remo_cli.core.version.get_current_version",
            side_effect=RuntimeError("unexpected"),
        )

        # Should not raise, should return None
        result = check_for_updates_passive()
        assert result is None

    def test_cache_empty_returns_none(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("")

        result = check_for_updates_passive()
        assert result is None

    def test_hint_format_includes_versions(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("v2.0.0")

        result = check_for_updates_passive()
        assert result is not None
        assert "v1.0.0" in result
        assert "v2.0.0" in result

    def test_prerelease_track_detected(self, mocker, tmp_path):
        """When current version is an RC, include_prerelease should be True
        for the background thread refresh."""
        mocker.patch(
            "remo_cli.core.version.get_current_version", return_value="1.0.0-rc.1"
        )
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)
        mock_get_latest = mocker.patch(
            "remo_cli.core.version.get_latest_release", return_value="1.0.0-rc.2"
        )

        # No cache file -> triggers background refresh
        mock_thread_class = mocker.patch("remo_cli.core.version.threading.Thread")
        mock_thread_instance = MagicMock()
        mock_thread_class.return_value = mock_thread_instance

        result = check_for_updates_passive()
        assert result is None

        # Extract and call the refresh function to verify it passes include_prerelease
        call_kwargs = mock_thread_class.call_args
        refresh_fn = call_kwargs[1]["target"] if "target" in call_kwargs[1] else call_kwargs[0][0]
        refresh_fn()
        mock_get_latest.assert_called_once_with(True)
