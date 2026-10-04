"""Workstation tab-record store (spec 028 R5)."""

from __future__ import annotations

import json
import multiprocessing
import os
import stat
from datetime import UTC, datetime, timedelta

import pytest

from remo_cli.core import tab_records
from remo_cli.core.tab_records import TabRecordError

K1 = "a" * 32
K2 = "b" * 32


@pytest.fixture(autouse=True)
def remo_home(tmp_path, monkeypatch):
    home = tmp_path / "remo"
    monkeypatch.setenv("REMO_HOME", str(home))
    return home


def _raw(remo_home):
    return json.loads((remo_home / "tab-records.json").read_text())


def test_secret_created_once_0600_and_reused(remo_home):
    first = tab_records.get_secret()
    path = remo_home / "tab-secret"
    assert len(path.read_text().strip()) == 64
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert tab_records.get_secret() == first


def test_record_load_round_trip_and_overwrite():
    assert tab_records.load(K1) is None
    tab_records.record(K1, "host-a", "proj")
    rec = tab_records.load(K1)
    assert rec is not None
    assert (rec.host, rec.project) == ("host-a", "proj")
    assert rec.recorded_at.tzinfo is not None
    assert datetime.now(UTC) - rec.recorded_at < timedelta(minutes=1)
    tab_records.record(K1, "host-b", None)
    rec = tab_records.load(K1)
    assert rec is not None
    assert (rec.host, rec.project) == ("host-b", None)


def test_forget_removes_one_and_reports_existence():
    tab_records.record(K1, "h", None)
    tab_records.record(K2, "h", None)
    assert tab_records.forget(K1) is True
    assert tab_records.forget(K1) is False
    assert tab_records.load(K1) is None
    assert tab_records.load(K2) is not None


def test_forget_all_empties_and_rotates_secret(remo_home):
    before = tab_records.get_secret()
    tab_records.record(K1, "h", None)
    tab_records.forget_all()
    assert _raw(remo_home)["records"] == {}
    assert tab_records.get_secret() != before


def test_prune_drops_old_entries(remo_home):
    tab_records.record(K1, "h", None)
    data = _raw(remo_home)
    old = (datetime.now(UTC) - timedelta(days=tab_records.RETENTION_DAYS + 1)).isoformat()
    data["records"]["c" * 32] = {"host": "h", "project": None, "recorded_at": old}
    (remo_home / "tab-records.json").write_text(json.dumps(data))
    tab_records.record(K2, "h", None)
    assert set(_raw(remo_home)["records"]) == {K1, K2}


def test_prune_keeps_newest_max_records(remo_home):
    now = datetime.now(UTC)
    records = {
        f"{i:032x}": {
            "host": "h",
            "project": None,
            "recorded_at": (now - timedelta(minutes=i + 1)).isoformat(),
        }
        for i in range(tab_records.MAX_RECORDS + 2)
    }
    remo_home.mkdir(parents=True)
    (remo_home / "tab-records.json").write_text(json.dumps({"version": 1, "records": records}))
    tab_records.record(K1, "h", None)
    kept = _raw(remo_home)["records"]
    assert len(kept) == tab_records.MAX_RECORDS
    assert K1 in kept
    assert f"{tab_records.MAX_RECORDS + 1:032x}" not in kept


@pytest.mark.parametrize(
    "content",
    [
        "not json at all",
        json.dumps({"version": 99, "records": {K1: {"host": "h", "project": None, "recorded_at": "x"}}}),
        json.dumps(["a", "list"]),
        json.dumps({"version": 1, "records": ["bad"]}),
        json.dumps({"version": 1, "records": {K1: "bad-shape"}}),
        json.dumps({"version": 1, "records": {K1: {"host": "h", "project": 3, "recorded_at": "x"}}}),
    ],
)
def test_malformed_file_reads_empty_and_is_rewritten(remo_home, content):
    remo_home.mkdir(parents=True)
    (remo_home / "tab-records.json").write_text(content)
    assert tab_records.load(K1) is None
    tab_records.record(K2, "h", "p")
    data = _raw(remo_home)
    assert data["version"] == 1
    assert set(data["records"]) == {K2}


def test_missing_file_reads_empty():
    assert tab_records.load(K1) is None


def test_unwritable_home_raises_tab_record_error(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    monkeypatch.setenv("REMO_HOME", str(blocker / "remo"))
    with pytest.raises(TabRecordError):
        tab_records.record(K1, "h", None)
    with pytest.raises(TabRecordError):
        tab_records.forget(K1)
    with pytest.raises(TabRecordError):
        tab_records.forget_all()
    with pytest.raises(TabRecordError):
        tab_records.get_secret()


def test_lock_timeout_raises_tab_record_error(monkeypatch):
    import fcntl

    monkeypatch.setattr(tab_records, "_LOCK_TIMEOUT_S", 0.1)
    tab_records.record(K1, "h", None)  # creates the lock sidecar
    holder = os.open(str(tab_records._lock_path()), os.O_RDWR)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        with pytest.raises(TabRecordError, match="busy"):
            tab_records.record(K2, "h", None)
    finally:
        os.close(holder)


def _writer(args):
    home, worker = args
    os.environ["REMO_HOME"] = home
    for i in range(20):
        tab_records.record(f"{worker:016x}{i:016x}", f"host-{worker}", str(i))


def test_concurrent_writers_never_corrupt(remo_home):
    ctx = multiprocessing.get_context("fork")
    with ctx.Pool(8) as pool:
        pool.map(_writer, [(str(remo_home), n) for n in range(8)])
    data = _raw(remo_home)
    assert len(data["records"]) == 8 * 20
    for worker in range(8):
        for i in range(20):
            entry = data["records"][f"{worker:016x}{i:016x}"]
            assert entry["host"] == f"host-{worker}"
            assert entry["project"] == str(i)
