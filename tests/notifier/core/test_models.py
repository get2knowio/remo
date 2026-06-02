"""Unit tests for the notifier models (spec 008 — agentsh ``Request``)."""

from __future__ import annotations

from datetime import datetime, timezone

from remo_cli.notifier.models import AgentshRequest, ApprovalDecision, Decision


def _valid() -> dict:
    return {
        "id": "appr-123",
        "session_id": "sess-1",
        "kind": "file_delete",
        "target": "/workspace/scratch.txt",
        "rule": "fs.delete",
        "message": "delete scratch file?",
    }


def test_valid_request_round_trips() -> None:
    req = AgentshRequest.model_validate(_valid())
    assert req.id == "appr-123"
    assert req.kind == "file_delete"
    assert req.target == "/workspace/scratch.txt"
    assert req.fields == {}


def test_unknown_field_tolerated() -> None:
    # agentsh owns the schema; a future field must not block delivery.
    req = AgentshRequest.model_validate(_valid() | {"surprise": 1})
    assert req.id == "appr-123"


def test_timestamps_parsed() -> None:
    req = AgentshRequest.model_validate(
        _valid() | {"expires_at": "2026-06-01T12:00:00Z"}
    )
    assert isinstance(req.expires_at, datetime)


def test_fields_map_preserved() -> None:
    req = AgentshRequest.model_validate(_valid() | {"fields": {"size": 42, "owner": "remo"}})
    assert req.fields["size"] == 42
    assert req.fields["owner"] == "remo"


def test_defaults_for_optional_fields() -> None:
    req = AgentshRequest.model_validate({"id": "x"})
    assert req.kind == ""
    assert req.target == ""
    assert req.session_id == ""


def test_decision_enum_values() -> None:
    assert Decision.allow.value == "allow"
    assert Decision.deny.value == "deny"


def test_approval_decision_defaults() -> None:
    d = ApprovalDecision(decision=Decision.allow, responder="telegram:p")
    assert d.reason == ""
    assert d.grant_id is None
    assert d.decided_at.tzinfo is not None
    assert d.decided_at <= datetime.now(timezone.utc)
