"""US6 T092: one-time exit-to-instance-shell warning persistence (state.yml)."""

from __future__ import annotations

import stat

import pytest
import yaml

from remo_cli.core.config import get_state_file_path


def _read_state() -> dict:
    path = get_state_file_path()
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _write_state(data: dict) -> None:
    import os
    path = get_state_file_path()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.chmod(path, 0o600)


def test_state_file_path_under_remo_home(tmp_config_dir):
    p = get_state_file_path()
    assert p.parent == tmp_config_dir
    assert p.name == "state.yml"


def test_state_write_round_trip(tmp_config_dir):
    _write_state({"exit_warning_shown": True})
    assert _read_state() == {"exit_warning_shown": True}


def test_state_file_mode_0600(tmp_config_dir):
    _write_state({"exit_warning_shown": True})
    mode = stat.S_IMODE(get_state_file_path().stat().st_mode)
    assert mode == 0o600
