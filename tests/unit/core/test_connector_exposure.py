"""Exposure gate: default deny (specs/025-ssm-connector data-model.md E2)."""

from __future__ import annotations

import json

import pytest

from remo_cli.core.connector import ErrorCode, load_exposure


def test_missing_file_exposes_nothing(tmp_path) -> None:
    cfg = load_exposure(tmp_path / "exposure.json")
    assert cfg.is_exposed("lab", "remo") is False


def test_empty_exposures_list_exposes_nothing(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(json.dumps({"version": 1, "exposures": []}))
    cfg = load_exposure(path)
    assert cfg.is_exposed("lab", "remo") is False


def test_version_2_exposes_nothing(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(
        json.dumps({"version": 2, "exposures": [{"host": "lab", "projects": ["remo"]}]})
    )
    cfg = load_exposure(path)
    assert cfg.is_exposed("lab", "remo") is False


def test_matching_pair_is_exposed(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(
        json.dumps({"version": 1, "exposures": [{"host": "lab", "projects": ["remo", "blog"]}]})
    )
    cfg = load_exposure(path)
    assert cfg.is_exposed("lab", "remo") is True
    assert cfg.is_exposed("lab", "blog") is True


def test_same_host_other_project_is_not_exposed(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(
        json.dumps({"version": 1, "exposures": [{"host": "lab", "projects": ["remo"]}]})
    )
    cfg = load_exposure(path)
    assert cfg.is_exposed("lab", "other-project") is False


def test_other_host_same_project_is_not_exposed(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(
        json.dumps({"version": 1, "exposures": [{"host": "lab", "projects": ["remo"]}]})
    )
    cfg = load_exposure(path)
    assert cfg.is_exposed("other-host", "remo") is False


def test_invalid_json_raises_config_unreadable_naming_the_path(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text("{not valid json")
    with pytest.raises(Exception) as exc_info:
        load_exposure(path)
    assert exc_info.value.code == ErrorCode.CONFIG_UNREADABLE  # type: ignore[attr-defined]
    assert str(path) in exc_info.value.message  # type: ignore[attr-defined]


def test_non_object_top_level_raises_config_unreadable(tmp_path) -> None:
    path = tmp_path / "exposure.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(Exception) as exc_info:
        load_exposure(path)
    assert exc_info.value.code == ErrorCode.CONFIG_UNREADABLE  # type: ignore[attr-defined]
