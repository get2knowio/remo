"""T036: the optional-SDK-absent experience (contracts/errors.md).

AWS lazy-imports `boto3` only inside functions (never at module level) so
CLI startup stays fast (SC-008); when it's missing, every AWS command must
raise `MissingDependencyError` naming the extra to install, translate to
exit code 1 at the CLI boundary, and never leak a raw traceback.

Hetzner has no optional-SDK import in practice today — it talks to the
Hetzner Cloud HTTP API directly via `urllib.request`, not the `hcloud`
package (the descriptor's `sdk_extra="hetzner"` is declared for
future-proofing only) — so there is no missing-SDK path to test for it.
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
    from remo_cli.providers.aws import _require_boto3

    with pytest.raises(MissingDependencyError) as exc_info:
        _require_boto3()
    message = str(exc_info.value)
    assert "boto3" in message
    # The remediation must not point at `uv sync --extra aws`: pyproject.toml
    # declares no `aws` extra, so that command fails with an unknown-extra
    # error. Until issue #94 introduces real optional extras, a reinstall is
    # the only advice that actually works.
    assert "--extra aws" not in message
    assert "uv sync" in message


@pytest.mark.parametrize("verb, argv", [("sync", ["sync", "--region", "us-west-2"])])
def test_cli_command_exits_1_with_clear_message_no_traceback(boto3_absent, verb, argv) -> None:
    group = build_provider_group(get_descriptor("aws"))
    result = CliRunner().invoke(group, argv)

    assert result.exit_code == 1
    assert "boto3" in result.output
    # No raw traceback leaked to the user (the factory's provider_command
    # wrapper catches ProviderError and prints a clean message + exits) —
    # the MissingDependencyError itself must not escape CliRunner uncaught.
    assert not isinstance(result.exception, MissingDependencyError)
    assert "Traceback (most recent call last)" not in result.output
