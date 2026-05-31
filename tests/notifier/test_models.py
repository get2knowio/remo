"""Unit tests for the notifier wire-protocol models (T007)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from remo_cli.notifier.models import (
    ApprovalRequest,
    Decision,
    Operation,
    OperationKind,
)


def _valid_request_dict() -> dict:
    return {
        "operation": {"kind": "command", "command": "rm", "args": ["-rf", "/tmp/x"]},
        "policy_rule_name": "demo",
        "policy_message": "approve rm?",
    }


def test_valid_request_round_trips() -> None:
    req = ApprovalRequest.model_validate(_valid_request_dict())
    assert req.operation.kind is OperationKind.command
    assert req.operation.args == ["-rf", "/tmp/x"]
    assert req.timeout_seconds is None
    assert req.approval_id is None


def test_unknown_field_rejected() -> None:
    bad = _valid_request_dict() | {"surprise": 1}
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(bad)


def test_unknown_operation_field_rejected() -> None:
    bad = _valid_request_dict()
    bad["operation"]["surprise"] = 1
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(bad)


def test_bad_uuid_rejected() -> None:
    bad = _valid_request_dict() | {"approval_id": "not-a-uuid"}
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(bad)


def test_valid_uuid_accepted() -> None:
    good = _valid_request_dict() | {"approval_id": str(uuid.uuid4())}
    req = ApprovalRequest.model_validate(good)
    assert req.approval_id is not None


def test_bad_enum_rejected() -> None:
    bad = _valid_request_dict()
    bad["operation"]["kind"] = "teleport"
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(bad)


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_port_bounds(port: int) -> None:
    with pytest.raises(ValidationError):
        Operation.model_validate({"kind": "network", "remote_port": port})


def test_negative_timeout_rejected() -> None:
    bad = _valid_request_dict() | {"timeout_seconds": 0}
    with pytest.raises(ValidationError):
        ApprovalRequest.model_validate(bad)


def test_decision_enum_values() -> None:
    assert Decision.allow.value == "allow"
    assert Decision.deny.value == "deny"
