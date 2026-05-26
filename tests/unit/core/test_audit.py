"""US4 T069: audit log parsing, table rendering, --since filter, duration parsing."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from remo_cli.core import audit


def _lines(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def test_parse_lines_skips_blank_and_malformed():
    text = "\n".join(
        [
            "",
            json.dumps({"ts": "2026-05-25T10:00:00Z", "decision": "allow"}),
            "{not json}",
            json.dumps({"ts": "2026-05-25T10:01:00Z", "decision": "deny", "reason": "not-in-manifest"}),
        ]
    )
    parsed = audit._parse_lines(text)  # noqa: SLF001
    assert len(parsed) == 2
    assert parsed[0].decision == "allow"
    assert parsed[1].reason == "not-in-manifest"


def test_parse_duration_units():
    assert audit.parse_duration("30s") == timedelta(seconds=30)
    assert audit.parse_duration("5m") == timedelta(minutes=5)
    assert audit.parse_duration("2h") == timedelta(hours=2)
    assert audit.parse_duration("7d") == timedelta(days=7)
    with pytest.raises(ValueError):
        audit.parse_duration("eternity")


def test_render_table_includes_columns():
    text = _lines(
        {
            "ts": "2026-05-25T10:00:00Z",
            "project": "foo",
            "secret": "github_token",
            "decision": "allow",
            "reason": "in-manifest",
            "cache": "miss",
        }
    )
    rendered = audit.render_table(audit._parse_lines(text))  # noqa: SLF001
    assert "github_token" in rendered
    assert "allow" in rendered
    assert "foo" in rendered


def test_render_empty_says_no_records():
    assert "no audit records" in audit.render_table([]).lower()


def test_fetch_since_filters_old_lines(mocker):
    now = datetime.now(timezone.utc)
    fresh_ts = (now - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    old_ts = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = _lines(
        {"ts": fresh_ts, "decision": "allow"},
        {"ts": old_ts, "decision": "deny"},
    )
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=text, stderr="")
    mocker.patch("subprocess.run", return_value=completed)

    lines = audit.fetch("host", "user", tail=None, since=timedelta(minutes=10))
    assert len(lines) == 1
    assert lines[0].decision == "allow"


def test_fetch_missing_audit_log_raises(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="cat: /var/log/remo-broker/audit.log: No such file or directory"
    )
    mocker.patch("subprocess.run", return_value=completed)
    with pytest.raises(audit.AuditError, match="audit log not found"):
        audit.fetch("h", "u", tail=None, since=None)
