"""Tests for remo_cli.core.install_record (spec 031, contracts/install-record.md)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from remo_cli.core import install_record as ir
from remo_cli.core.install_record import InstallOrigin, detect_origin, load_record, record_path

VALID = {
    "format": 1,
    "repo": "get2knowio/remo",
    "channel": "prerelease",
    "version": "5.0.0rc1",
    "installer": "uv",
    "extras": ["web"],
    "plugins": ["ec2", "hetzner"],
    "with_pkg": [],
    "with_url": [],
    "updated_at": "2026-11-27T15:00:00Z",
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMO_HOME", str(tmp_path))
    return tmp_path


def _write(home: Path, data) -> None:
    text = data if isinstance(data, str) else json.dumps(data)
    (home / "install.json").write_text(text)


class TestRecordPath:
    def test_remo_home_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("REMO_HOME", str(tmp_path / "a"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
        assert record_path() == tmp_path / "a" / "install.json"

    def test_xdg_next(self, monkeypatch, tmp_path):
        monkeypatch.delenv("REMO_HOME", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "x"))
        assert record_path() == tmp_path / "x" / "remo" / "install.json"

    def test_default(self, monkeypatch, tmp_path):
        monkeypatch.delenv("REMO_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert record_path() == tmp_path / ".config" / "remo" / "install.json"


class TestLoadRecord:
    def test_valid(self, home):
        _write(home, VALID)
        rec, reason = load_record()
        assert reason is None
        assert rec is not None
        assert rec.channel == "prerelease"
        assert rec.plugins == ("ec2", "hetzner")
        assert rec.extras == ("web",)
        assert rec.repo == "get2knowio/remo"

    def test_missing_file_is_absent_without_reason(self, home):
        assert load_record() == (None, None)

    @pytest.mark.parametrize(
        "content",
        [
            "{not json",
            json.dumps({**VALID, "format": 2}),
            json.dumps({**VALID, "repo": 'a"b/c'}),
            json.dumps({**VALID, "plugins": ["ec2;rm"]}),
            json.dumps({**VALID, "channel": "nightly"}),
            json.dumps({**VALID, "installer": "pip"}),
            json.dumps({**VALID, "extras": ["api"]}),
            json.dumps([1, 2]),
            json.dumps({k: v for k, v in VALID.items() if k != "version"}),
            json.dumps({**VALID, "plugins": "ec2"}),
        ],
    )
    def test_invalid_returns_none_and_reason(self, home, content):
        _write(home, content)
        rec, reason = load_record()
        assert rec is None
        assert reason

    def test_unreadable_file(self, home):
        (home / "install.json").mkdir()
        rec, reason = load_record()
        assert rec is None and reason


def _fake_dist(direct_url):
    return SimpleNamespace(read_text=lambda name: direct_url)


class TestDetectOrigin:
    def test_recorded(self, home):
        _write(home, VALID)
        assert detect_origin() is InstallOrigin.RECORDED

    def test_source(self, home, monkeypatch):
        d = json.dumps({"url": "file:///src", "dir_info": {"editable": True}})
        monkeypatch.setattr(ir, "_distribution", lambda: _fake_dist(d))
        assert detect_origin() is InstallOrigin.SOURCE

    def test_pypi(self, home, monkeypatch):
        monkeypatch.setattr(ir, "_distribution", lambda: _fake_dist(None))
        assert detect_origin() is InstallOrigin.PYPI

    def test_url_without_dir_info_is_unknown(self, home, monkeypatch):
        d = json.dumps({"url": "file:///x.whl", "archive_info": {}})
        monkeypatch.setattr(ir, "_distribution", lambda: _fake_dist(d))
        assert detect_origin() is InstallOrigin.UNKNOWN

    def test_garbage_direct_url_is_unknown(self, home, monkeypatch):
        monkeypatch.setattr(ir, "_distribution", lambda: _fake_dist("{nope"))
        assert detect_origin() is InstallOrigin.UNKNOWN

    def test_unreadable_record_is_unknown(self, home, monkeypatch):
        _write(home, "{bad")
        monkeypatch.setattr(ir, "_distribution", lambda: _fake_dist(None))
        assert detect_origin() is InstallOrigin.UNKNOWN

    def test_distribution_missing_is_unknown(self, home, monkeypatch):
        def boom():
            raise ir.metadata.PackageNotFoundError("remo-cli")

        monkeypatch.setattr(ir, "_distribution", boom)
        assert detect_origin() is InstallOrigin.UNKNOWN

    def test_real_distribution_lookup_path(self, home, monkeypatch):
        # Exercise the unpatched metadata call via the documented seam.
        monkeypatch.setattr(
            ir.metadata, "distribution", lambda name: _fake_dist(None)
        )
        assert detect_origin() is InstallOrigin.PYPI
