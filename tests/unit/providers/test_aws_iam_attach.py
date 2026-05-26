"""US1 T042: assert the AWS broker IAM instance profile is scoped to per-dev secret ARNs."""

import json

import pytest

from remo_cli.providers import aws


class _FakeIAM:
    """Minimal stub of the boto3 IAM client surface used by _ensure_broker_instance_role."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._roles: dict[str, dict] = {}
        self._profiles: dict[str, dict] = {}
        self._role_policies: dict[tuple[str, str], str] = {}

        class _Exceptions:
            class NoSuchEntityException(Exception):
                pass

        self.exceptions = _Exceptions

    def _record(self, name: str, **kwargs) -> None:
        self.calls.append((name, kwargs))

    def get_role(self, RoleName: str):  # noqa: N803
        self._record("get_role", RoleName=RoleName)
        if RoleName not in self._roles:
            raise self.exceptions.NoSuchEntityException(RoleName)
        return {"Role": {"RoleName": RoleName}}

    def create_role(self, **kwargs):
        self._record("create_role", **kwargs)
        self._roles[kwargs["RoleName"]] = kwargs

    def attach_role_policy(self, **kwargs):
        self._record("attach_role_policy", **kwargs)

    def put_role_policy(self, **kwargs):
        self._record("put_role_policy", **kwargs)
        self._role_policies[(kwargs["RoleName"], kwargs["PolicyName"])] = kwargs["PolicyDocument"]

    def get_instance_profile(self, InstanceProfileName: str):  # noqa: N803
        self._record("get_instance_profile", InstanceProfileName=InstanceProfileName)
        if InstanceProfileName not in self._profiles:
            raise self.exceptions.NoSuchEntityException(InstanceProfileName)
        # Once we know about it, the role is attached.
        return {
            "InstanceProfile": {
                "InstanceProfileName": InstanceProfileName,
                "Roles": [{"RoleName": InstanceProfileName}],
            }
        }

    def create_instance_profile(self, **kwargs):
        self._record("create_instance_profile", **kwargs)
        self._profiles[kwargs["InstanceProfileName"]] = kwargs

    def add_role_to_instance_profile(self, **kwargs):
        self._record("add_role_to_instance_profile", **kwargs)


def test_ensure_creates_role_and_profile(mocker):
    mocker.patch("remo_cli.providers.aws.time.sleep", return_value=None)
    iam = _FakeIAM()

    role_name, profile_name = aws._ensure_broker_instance_role(  # noqa: SLF001
        iam, dev_id="alice", region="us-west-2", instance_id="web-1"
    )

    assert role_name == "remo-broker-instance-alice-web-1"
    assert profile_name == "remo-broker-instance-alice-web-1"
    assert "remo-broker-instance-alice-web-1" in iam._roles  # noqa: SLF001
    assert "remo-broker-instance-alice-web-1" in iam._profiles  # noqa: SLF001


def test_inline_policy_scopes_to_dev_arn(mocker):
    mocker.patch("remo_cli.providers.aws.time.sleep", return_value=None)
    iam = _FakeIAM()
    aws._ensure_broker_instance_role(  # noqa: SLF001
        iam, dev_id="alice", region="us-west-2", instance_id="web-1"
    )

    inline = iam._role_policies[  # noqa: SLF001
        ("remo-broker-instance-alice-web-1", "remo-broker-secretsmanager-scoped")
    ]
    parsed = json.loads(inline)
    statements = parsed["Statement"]
    assert any(
        s.get("Resource") == "arn:aws:secretsmanager:*:*:secret:remo/alice/*"
        for s in statements
    )


def test_ensure_idempotent_when_role_exists(mocker):
    mocker.patch("remo_cli.providers.aws.time.sleep", return_value=None)
    iam = _FakeIAM()
    iam._roles["remo-broker-instance-bob-web-1"] = {}  # noqa: SLF001
    iam._profiles["remo-broker-instance-bob-web-1"] = {}  # noqa: SLF001

    aws._ensure_broker_instance_role(  # noqa: SLF001
        iam, dev_id="bob", region="us-west-2", instance_id="web-1"
    )

    create_calls = [n for n, _ in iam.calls if n in {"create_role", "create_instance_profile"}]
    assert create_calls == []  # Already exists → no creates.


def test_deny_all_revocation(mocker):
    iam = _FakeIAM()
    aws._attach_broker_deny_all_policy(iam, "remo-broker-instance-eve")  # noqa: SLF001
    doc = json.loads(
        iam._role_policies[("remo-broker-instance-eve", "remo-broker-secretsmanager-scoped")]  # noqa: SLF001
    )
    stmts = doc["Statement"]
    assert any(s.get("Effect") == "Deny" and s.get("Action") == "*" for s in stmts)
