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

    def test_rc_version_pep440(self):
        assert _parse_version("1.2.3rc1") == (1, 2, 3, 1)

    def test_rc_version_with_v_prefix_pep440(self):
        assert _parse_version("v1.2.3rc1") == (1, 2, 3, 1)

    def test_rc_version_with_dash(self):
        assert _parse_version("1.2.3-rc.1") == (1, 2, 3, 1)

    def test_dev_version_pep440(self):
        assert _parse_version("0.8.0.dev0") == (0, 8, 0, 0)

    def test_dev_version_with_dash(self):
        assert _parse_version("0.8.0-dev.1") == (0, 8, 0, 1)

    def test_v0_8_0(self):
        assert _parse_version("v0.8.0") == (0, 8, 0, 999999)

    def test_invalid_returns_zeros(self):
        assert _parse_version("invalid") == (0, 0, 0, 0)

    def test_empty_string_returns_zeros(self):
        assert _parse_version("") == (0, 0, 0, 0)

    def test_release_sorts_above_rc(self):
        release = _parse_version("v1.0.0")
        rc = _parse_version("v1.0.0rc1")
        assert release > rc

    def test_higher_rc_sorts_above_lower_rc(self):
        rc2 = _parse_version("v1.0.0rc2")
        rc1 = _parse_version("v1.0.0rc1")
        assert rc2 > rc1

    def test_whitespace_stripped(self):
        assert _parse_version("  v1.2.3  ") == (1, 2, 3, 999999)

    def test_large_version_numbers(self):
        assert _parse_version("v99.88.77") == (99, 88, 77, 999999)

    def test_zero_version(self):
        assert _parse_version("v0.0.0") == (0, 0, 0, 999999)

    def test_rc_zero(self):
        assert _parse_version("v1.0.0rc0") == (1, 0, 0, 0)


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
        assert version_is_newer("v1.0.0rc2", "v1.0.0rc1") is True

    def test_release_newer_than_rc(self):
        assert version_is_newer("v1.0.0", "v1.0.0rc1") is True

    def test_rc_not_newer_than_release(self):
        assert version_is_newer("v1.0.0rc1", "v1.0.0") is False

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
        self._mock_pypi_response(mocker, ["1.0.0", "1.1.0", "1.2.0rc1"])
        result = get_latest_release(include_prerelease=False)
        assert result == "1.1.0"

    def test_includes_prerelease_when_requested(self, mocker):
        self._mock_pypi_response(mocker, ["1.0.0", "1.1.0", "1.2.0rc1"])
        result = get_latest_release(include_prerelease=True)
        assert result == "1.2.0rc1"

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
        self._mock_pypi_response(mocker, ["1.0.0rc1", "1.0.0beta1"])
        result = get_latest_release(include_prerelease=False)
        assert result == ""


class TestCheckForUpdatesPassive:
    """Tests for check_for_updates_passive()."""

    def test_returns_none_when_version_unknown(self, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")

        result = check_for_updates_passive()
        assert result is None

    def test_returns_hint_when_cache_has_newer_version(self, mocker, tmp_path):
        mocker.patch("remo_cli.core.version.detect_origin", return_value=InstallOrigin.SOURCE)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)

        # Create a fresh cache file with a newer version
        cache_file = tmp_path / "latest_version_cache"
        cache_file.write_text("v1.1.0")

        result = check_for_updates_passive()
        assert result is not None
        assert "1.1.0" in result
        assert "Update available" in result
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
            "remo_cli.core.version.get_current_version", return_value="1.0.0rc1"
        )
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)
        mock_get_latest = mocker.patch(
            "remo_cli.core.version.get_latest_release", return_value="1.0.0rc2"
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


# ---------------------------------------------------------------------------
# Spec 031: GitHub release lookup, a/b ordering, origin-aware notice
# ---------------------------------------------------------------------------

import email.message  # noqa: E402
import urllib.error  # noqa: E402

from remo_cli.core.install_record import InstallOrigin, InstallRecord  # noqa: E402
from remo_cli.core.version import (  # noqa: E402
    ReleaseLookupError,
    get_latest_github_release,
)


def _resp(mocker, payload):
    r = MagicMock()
    r.read.return_value = json.dumps(payload).encode()
    r.__enter__ = lambda s: s
    r.__exit__ = MagicMock(return_value=False)
    return mocker.patch("remo_cli.core.version.urllib.request.urlopen", return_value=r)


class TestGithubLookup:
    def test_stable_reads_latest_and_strips_v(self, mocker):
        m = _resp(mocker, {"tag_name": "v5.0.1"})
        assert get_latest_github_release("o/r", "stable") == "5.0.1"
        req = m.call_args[0][0]
        assert req.full_url == "https://api.github.com/repos/o/r/releases/latest"

    def test_prerelease_picks_first_rc_prerelease(self, mocker):
        m = _resp(
            mocker,
            [
                {"tag_name": "v5.0.0", "prerelease": False},
                {"tag_name": "rc-5.1.0rc2", "prerelease": True},
                {"tag_name": "rc-5.1.0rc1", "prerelease": True},
            ],
        )
        assert get_latest_github_release("o/r", "prerelease") == "5.1.0rc2"
        assert m.call_args[0][0].full_url.endswith("/releases?per_page=20")

    def test_prerelease_none_found(self, mocker):
        _resp(mocker, [{"tag_name": "v5.0.0", "prerelease": False}])
        with pytest.raises(ReleaseLookupError, match="pre-release"):
            get_latest_github_release("o/r", "prerelease")

    def test_stable_missing_tag(self, mocker):
        _resp(mocker, {"nope": 1})
        with pytest.raises(ReleaseLookupError):
            get_latest_github_release("o/r", "stable")

    def test_network_error(self, mocker):
        mocker.patch(
            "remo_cli.core.version.urllib.request.urlopen",
            side_effect=urllib.error.URLError("down"),
        )
        with pytest.raises(ReleaseLookupError, match="down"):
            get_latest_github_release("o/r", "stable")

    def test_bad_json(self, mocker):
        r = MagicMock()
        r.read.return_value = b"<html>"
        r.__enter__ = lambda s: s
        r.__exit__ = MagicMock(return_value=False)
        mocker.patch("remo_cli.core.version.urllib.request.urlopen", return_value=r)
        with pytest.raises(ReleaseLookupError):
            get_latest_github_release("o/r", "stable")

    def test_rate_limit_named(self, mocker):
        hdrs = email.message.Message()
        hdrs["X-RateLimit-Remaining"] = "0"
        mocker.patch(
            "remo_cli.core.version.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 403, "Forbidden", hdrs, None),
        )
        with pytest.raises(ReleaseLookupError, match="rate limit"):
            get_latest_github_release("o/r", "stable")

    def test_other_http_error(self, mocker):
        mocker.patch(
            "remo_cli.core.version.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "Not Found", email.message.Message(), None),
        )
        with pytest.raises(ReleaseLookupError, match="404"):
            get_latest_github_release("o/r", "stable")


class TestAlphaBetaOrdering:
    def test_order(self):
        a, b, rc, rel = (
            _parse_version("5.0.0a1"),
            _parse_version("5.0.0b2"),
            _parse_version("5.0.0rc1"),
            _parse_version("5.0.0"),
        )
        assert a < b < rc < rel

    def test_newer(self):
        assert version_is_newer("5.0.0b1", "5.0.0a9")
        assert not version_is_newer("5.0.0a1", "5.0.0rc1")


def _record(channel="stable"):
    return InstallRecord(
        repo="o/r", channel=channel, version="1.0.0", installer="uv",
        extras=(), plugins=(), with_pkg=(), with_url=(), updated_at="2026-01-01T00:00:00Z",
    )


class TestNoticeByOrigin:
    def _setup(self, mocker, tmp_path, origin, cached="v2.0.0", current="1.0.0", record=None):
        mocker.patch("remo_cli.core.version.get_current_version", return_value=current)
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)
        mocker.patch("remo_cli.core.version.detect_origin", return_value=origin)
        mocker.patch(
            "remo_cli.core.version.load_record",
            return_value=(record, None),
        )
        if cached is not None:
            name = "latest_version_cache" + (f".{record.channel}" if record else "")
            (tmp_path / name).write_text(cached)

    def test_recorded(self, mocker, tmp_path):
        self._setup(mocker, tmp_path, InstallOrigin.RECORDED, record=_record())
        out = check_for_updates_passive()
        assert out == "Update available: v1.0.0 → v2.0.0. Run 'remo self-update' to upgrade."

    def test_pypi(self, mocker, tmp_path):
        self._setup(mocker, tmp_path, InstallOrigin.PYPI)
        out = check_for_updates_passive()
        assert out is not None
        assert "Switch to the installer once:" in out
        assert (
            "curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash"
            in out
        )

    @pytest.mark.parametrize("origin", [InstallOrigin.SOURCE, InstallOrigin.UNKNOWN])
    def test_source_and_unknown_keep_todays_text(self, mocker, tmp_path, origin):
        self._setup(mocker, tmp_path, origin)
        out = check_for_updates_passive()
        assert out is not None and "uv tool upgrade remo-cli" in out

    def test_recorded_refresh_uses_github_on_record_channel(self, mocker, tmp_path):
        self._setup(mocker, tmp_path, InstallOrigin.RECORDED, cached=None, record=_record("prerelease"))
        gh = mocker.patch("remo_cli.core.version.get_latest_github_release", return_value="2.0.0rc1")
        thread_cls = mocker.patch("remo_cli.core.version.threading.Thread")
        assert check_for_updates_passive() is None
        thread_cls.call_args[1]["target"]()
        gh.assert_called_once_with("o/r", "prerelease")
        assert (tmp_path / "latest_version_cache.prerelease").read_text() == "2.0.0rc1"

    def test_recorded_refresh_lookup_error_is_swallowed(self, mocker, tmp_path):
        self._setup(mocker, tmp_path, InstallOrigin.RECORDED, cached=None, record=_record())
        mocker.patch(
            "remo_cli.core.version.get_latest_github_release",
            side_effect=ReleaseLookupError("x"),
        )
        thread_cls = mocker.patch("remo_cli.core.version.threading.Thread")
        check_for_updates_passive()
        thread_cls.call_args[1]["target"]()
        assert not (tmp_path / "latest_version_cache.stable").exists()


class TestReviewFollowUps:
    def test_local_segment_does_not_read_as_alpha(self):
        # the sha's trailing "a27" must not demote a dev build to an alpha
        assert _parse_version("4.4.0.dev5+g3f9a27") == _parse_version("4.4.0.dev5")
        assert _parse_version("5.0.0b1+g1a2") == _parse_version("5.0.0b1")

    @pytest.mark.parametrize(
        ("version", "pre"),
        [("5.0.0a1", True), ("5.0.0b2", True), ("5.0.0rc1", True), ("5.0.0.dev3", True),
         ("5.0.0", False), ("10.0.0", False)],
    )
    def test_short_prerelease_forms_are_prereleases(self, version, pre):
        from remo_cli.core.version import _is_prerelease

        assert _is_prerelease(version) is pre

    def test_pypi_stable_track_skips_beta(self, mocker):
        _resp(mocker, {"releases": {"1.0.0": [], "1.1.0b1": []}})
        from remo_cli.core.version import get_latest_release

        assert get_latest_release(include_prerelease=False) == "1.0.0"

    def test_other_channel_cache_is_not_announced(self, mocker, tmp_path):
        """A prerelease value left by an earlier channel must not tell a stable
        install to self-update to it."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="1.0.0")
        mocker.patch("remo_cli.core.version.get_remo_home", return_value=tmp_path)
        mocker.patch("remo_cli.core.version.detect_origin", return_value=InstallOrigin.RECORDED)
        mocker.patch("remo_cli.core.version.load_record", return_value=(_record("stable"), None))
        mocker.patch("remo_cli.core.version.threading.Thread")
        (tmp_path / "latest_version_cache").write_text("2.0.0")
        (tmp_path / "latest_version_cache.prerelease").write_text("2.0.0rc1")
        assert check_for_updates_passive() is None
