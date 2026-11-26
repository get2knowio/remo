"""The cleanup workflow's IAM step must never touch anything but a smoke run's
own SSM role/profile pair.

Run 45749586140 deleted the CI's own OIDC role, ``remo-ci-github-actions``: the
step selected roles by the bare ``remo-ci-`` prefix, which that role shares
with the per-run ``remo-ci-<run_id>-aws-ssm-role``. This test runs the
workflow's real step script against a fake ``aws`` that ignores ``--query`` and
returns every name -- the worst case, a query that filters nothing -- so the
script's own guards are what must hold.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "smoke-test-cleanup.yml"
STEP = "Clean up orphaned CI resources"
CI_ROLE = "remo-ci-github-actions"

FAKE_AWS = r"""#!/usr/bin/env bash
# Fake aws CLI: canned reads, every mutating call logged to $AWS_CALLS.
case "$1 $2" in
  "sts get-caller-identity") echo "arn:aws:sts::123456789012:assumed-role/$SELF/remo-cleanup-1" ;;
  "iam list-roles")
    printf '%s\t%s\t%s\t%s\n' remo-ci-github-actions remo-ci-123-aws-ssm-role \
      remo-ci-github-actions-ssm-role remo-alice-ssm-role ;;
  "iam list-instance-profiles")
    printf '%s\t%s\t%s\n' remo-ci-github-actions remo-ci-123-aws-ssm-profile remo-ci-x-ssm-profile ;;
  "iam get-instance-profile") printf '%s\t%s\n' remo-ci-123-aws-ssm-role remo-ci-github-actions ;;
  "iam list-instance-profiles-for-role"|"iam list-attached-role-policies"|"iam list-role-policies") ;;
  "ec2 "*) ;;
  *) echo "$*" >>"$AWS_CALLS" ;;
esac
"""


def _step_script() -> str:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    (step,) = [s for s in jobs["cleanup-aws"]["steps"] if s.get("name") == STEP]
    return step["run"]


@pytest.fixture
def calls(tmp_path: Path) -> list[str]:
    fake = tmp_path / "bin" / "aws"
    fake.parent.mkdir()
    fake.write_text(FAKE_AWS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.touch()
    env = {
        **os.environ,
        "PATH": f"{fake.parent}{os.pathsep}{os.environ['PATH']}",
        "AWS_CALLS": str(log),
        "SELF": CI_ROLE,
    }
    result = subprocess.run(
        ["bash", "-e", "-c", _step_script()], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return log.read_text().splitlines()


def test_the_ci_role_is_never_modified(calls: list[str]) -> None:
    touching = [c for c in calls if CI_ROLE in c.split() or f"{CI_ROLE}-ssm-role" in c.split()]
    assert not touching, f"cleanup touched the CI role: {touching}"


def test_only_smoke_run_shaped_names_are_deleted(calls: list[str]) -> None:
    deleted = sorted(c.split()[-1] for c in calls if c.startswith(("iam delete-role ", "iam delete-instance-profile ")))
    assert deleted == ["remo-ci-123-aws-ssm-profile", "remo-ci-123-aws-ssm-role"], calls


def test_a_users_own_ssm_role_is_never_touched(calls: list[str]) -> None:
    assert not [c for c in calls if "remo-alice-ssm-role" in c]


def test_queries_require_the_ssm_suffix() -> None:
    script = _step_script()
    assert "ends_with(RoleName, '-ssm-role')" in script
    assert "ends_with(InstanceProfileName, '-ssm-profile')" in script
