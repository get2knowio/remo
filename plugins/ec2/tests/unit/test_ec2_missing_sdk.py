"""T036: the SDK-absent experience (contracts/errors.md), moved from remo's
tests/unit/providers/test_missing_sdk.py with the provider (#258).

EC2 lazy-imports `boto3` only inside functions (never at module level) so
CLI startup stays fast (SC-008); when it's missing, every EC2 command must
raise `MissingDependencyError` naming the extra to install, translate to
exit code 1 at the CLI boundary, and never leak a raw traceback.
"""

from __future__ import annotations

import sys

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.errors import MissingDependencyError
from remo_cli.core.provider_registry import get_descriptor


@pytest.fixture
def boto3_absent(monkeypatch: pytest.MonkeyPatch):
    """Simulate `import boto3` raising ImportError, regardless of caching."""
    monkeypatch.setitem(sys.modules, "boto3", None)


def test_require_boto3_raises_missing_dependency_error(boto3_absent) -> None:
    from remo_provider_ec2.provider import _require_boto3

    with pytest.raises(MissingDependencyError) as exc_info:
        _require_boto3()
    message = str(exc_info.value)
    assert "boto3" in message
    # The remediation names the extra that really exists since #258
    # (`remo-cli[ec2]`), never the old built-in's nonexistent `aws` extra.
    assert "--extra aws" not in message
    assert "uv sync --extra ec2" in message
    assert "remo-cli[ec2]" in message


@pytest.mark.parametrize("verb, argv", [("sync", ["sync", "--region", "us-west-2"])])
def test_cli_command_exits_1_with_clear_message_no_traceback(boto3_absent, verb, argv) -> None:
    group = build_provider_group(get_descriptor("ec2"))
    result = CliRunner().invoke(group, argv)

    assert result.exit_code == 1
    assert "boto3" in result.output
    # No raw traceback leaked to the user (the factory's provider_command
    # wrapper catches ProviderError and prints a clean message + exits) —
    # the MissingDependencyError itself must not escape CliRunner uncaught.
    assert not isinstance(result.exception, MissingDependencyError)
    assert "Traceback (most recent call last)" not in result.output
