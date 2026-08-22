"""Unit tests for `web/mirror_meta.py` (023): read_mirror_meta / record_change.

The marker is advisory (reads degrade to None, writes are best-effort) and
additive over 017's shape: `last_push` is written only by push-origin changes
and preserved verbatim otherwise; `last_change` is stamped on every change.
"""

from __future__ import annotations

import json

from remo_cli.web.mirror_meta import read_mirror_meta, record_change


def _meta_path(state_dir):
    return state_dir.web_identity_dir / "mirror-meta.json"


class TestReadMirrorMeta:
    def test_missing_file_is_none(self, state_dir):
        assert read_mirror_meta(state_dir.settings()) is None

    def test_corrupt_file_is_none(self, state_dir):
        state_dir.web_identity_dir.mkdir(mode=0o700, exist_ok=True)
        _meta_path(state_dir).write_text("{not json")
        assert read_mirror_meta(state_dir.settings()) is None

    def test_non_dict_document_is_none(self, state_dir):
        state_dir.web_identity_dir.mkdir(mode=0o700, exist_ok=True)
        _meta_path(state_dir).write_text("[1, 2]")
        assert read_mirror_meta(state_dir.settings()) is None


class TestRecordChange:
    def test_first_push_writes_generation_1_with_last_push_and_last_change(self, state_dir):
        settings = state_dir.settings()
        assert record_change(settings, origin="push", workstation="wk1") == 1
        doc = json.loads(_meta_path(state_dir).read_text())
        assert doc["generation"] == 1
        assert doc["last_push"]["workstation"] == "wk1"
        assert doc["last_change"]["origin"] == "push"
        assert doc["last_change"]["workstation"] == "wk1"
        assert doc["last_change"]["at"] == doc["last_push"]["at"]

    def test_web_change_preserves_last_push_verbatim(self, state_dir):
        settings = state_dir.settings()
        record_change(settings, origin="push", workstation="wk1")
        pushed = json.loads(_meta_path(state_dir).read_text())["last_push"]

        assert record_change(settings, origin="web") == 2
        doc = json.loads(_meta_path(state_dir).read_text())
        assert doc["generation"] == 2
        assert doc["last_push"] == pushed
        assert doc["last_change"] == {
            "at": doc["last_change"]["at"],
            "origin": "web",
            "workstation": None,
        }

    def test_web_change_with_no_prior_push_omits_last_push(self, state_dir):
        settings = state_dir.settings()
        assert record_change(settings, origin="web") == 1
        doc = json.loads(_meta_path(state_dir).read_text())
        assert "last_push" not in doc
        assert doc["last_change"]["origin"] == "web"

    def test_generation_is_monotonic_across_origins(self, state_dir):
        settings = state_dir.settings()
        assert record_change(settings, origin="web") == 1
        assert record_change(settings, origin="push", workstation="w") == 2
        assert record_change(settings, origin="web") == 3

    def test_push_without_workstation_defaults_label(self, state_dir):
        settings = state_dir.settings()
        record_change(settings, origin="push")
        doc = json.loads(_meta_path(state_dir).read_text())
        assert doc["last_push"]["workstation"] == "unknown"
        assert doc["last_change"]["workstation"] is None

    def test_corrupt_generation_restarts_from_1(self, state_dir):
        state_dir.web_identity_dir.mkdir(mode=0o700, exist_ok=True)
        _meta_path(state_dir).write_text(json.dumps({"generation": "nine"}))
        assert record_change(state_dir.settings(), origin="web") == 1

    def test_write_failure_returns_none(self, state_dir, monkeypatch):
        from remo_cli.web import mirror_meta as module

        def boom(path, doc):
            raise OSError("read-only")

        monkeypatch.setattr(module, "_write_doc", boom)
        assert record_change(state_dir.settings(), origin="web") is None
