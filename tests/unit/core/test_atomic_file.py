"""Tests for core/atomic_file.py — the shared atomic-write + lock helper (#244)."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import stat

import pytest

from remo_cli.core import atomic_file
from remo_cli.core.atomic_file import (
    LockBusyError,
    advisory_lock,
    atomic_write_json,
    atomic_write_text,
)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_write_replaces_existing_content(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old")
    atomic_write_text(target, "new\n")
    assert target.read_bytes() == b"new\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_default_mode_is_mkstemp_0600(tmp_path):
    target = tmp_path / "state"
    atomic_write_text(target, "x")
    assert _mode(target) == 0o600


def test_explicit_mode_is_applied(tmp_path):
    target = tmp_path / "state"
    atomic_write_text(target, "x", mode=0o640)
    assert _mode(target) == 0o640


def test_failure_unlinks_temp_and_leaves_target_intact(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("committed")

    def boom(*_a, **_k):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_text(target, "torn", prefix=".tmp_")
    assert target.read_text() == "committed"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]


def test_keyboard_interrupt_mid_write_also_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "state"

    def interrupt(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_text(target, "x")
    assert list(tmp_path.iterdir()) == []


def test_prefix_and_suffix_shape_the_temp_name(tmp_path, monkeypatch):
    seen: list[str] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    atomic_write_text(tmp_path / "a", "x")
    atomic_write_text(tmp_path / "b", "x", prefix=".web-service.", suffix=".json.tmp")
    assert seen[0].startswith(".a.")
    assert seen[1].startswith(".web-service.") and seen[1].endswith(".json.tmp")


def test_json_matches_json_dumps_bytes(tmp_path):
    doc = {"b": [1, 2], "a": "é"}
    compact = tmp_path / "compact.json"
    pretty = tmp_path / "pretty.json"
    atomic_write_json(compact, doc)
    atomic_write_json(pretty, doc, indent=2, trailing_newline=True)
    assert compact.read_text(encoding="utf-8") == json.dumps(doc)
    assert pretty.read_text(encoding="utf-8") == json.dumps(doc, indent=2) + "\n"


def test_lock_contention_times_out_with_default_error(tmp_path):
    lock = tmp_path / "x.lock"
    holder = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(LockBusyError, match="busy"):
            with advisory_lock(lock, timeout_s=0.1):
                pass
    finally:
        os.close(holder)


def test_lock_contention_raises_caller_error(tmp_path):
    class Busy(Exception):
        pass

    lock = tmp_path / "x.lock"
    holder = os.open(str(lock), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(Busy) as info:
            with advisory_lock(lock, timeout_s=0.1, busy_error=lambda: Busy("taken")):
                pass
        assert info.value.__suppress_context__
    finally:
        os.close(holder)


def test_lock_creates_sidecar_with_mode_and_releases(tmp_path):
    lock = tmp_path / "nested" / "x.lock"
    with advisory_lock(lock, file_mode=0o600):
        assert lock.exists()
    assert _mode(lock) == 0o600
    # Released: an immediate non-blocking re-acquire succeeds.
    with advisory_lock(lock, timeout_s=0.0):
        pass


def _fake_unsupported_flock(err: int):
    real_flock = fcntl.flock

    def fake(fd, operation):
        if operation & fcntl.LOCK_EX:
            raise OSError(err, os.strerror(err))
        return real_flock(fd, operation)

    return fake


@pytest.mark.parametrize("err", [errno.ENOLCK, errno.EOPNOTSUPP])
def test_unsupported_flock_degrades_with_one_warning(tmp_path, monkeypatch, capsys, err):
    monkeypatch.setattr(atomic_file, "_unsupported_warned", set())
    monkeypatch.setattr(fcntl, "flock", _fake_unsupported_flock(err))
    lock = tmp_path / "x.lock"

    ran = 0
    for _ in range(2):
        with advisory_lock(lock, timeout_s=1.0):
            ran += 1
    assert ran == 2
    out = capsys.readouterr().out
    assert out.count("locking unavailable") == 1
    assert "x.lock" in out


def test_unsupported_flock_uses_caller_callback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(atomic_file, "_unsupported_warned", set())
    monkeypatch.setattr(fcntl, "flock", _fake_unsupported_flock(errno.ENOLCK))
    calls: list[int] = []
    with advisory_lock(tmp_path / "x.lock", on_unsupported=lambda: calls.append(1)):
        pass
    assert calls == [1]
    assert capsys.readouterr().out == ""


def test_other_flock_errors_are_contention_not_degradation(tmp_path, monkeypatch):
    monkeypatch.setattr(fcntl, "flock", _fake_unsupported_flock(errno.EAGAIN))
    with pytest.raises(LockBusyError):
        with advisory_lock(tmp_path / "x.lock", timeout_s=0.1):
            pass
