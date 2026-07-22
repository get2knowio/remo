"""CLI-layer tests for `remo add` (feature 014, US1). Provider logic is mocked."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.added import add


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_success_calls_provider_and_exits_zero(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.add", return_value=0)
    result = runner.invoke(add, ["box", "dev@1.2.3.4:2222", "--identity", "/k/id"])
    assert result.exit_code == 0
    prov.assert_called_once()
    kwargs = prov.call_args.kwargs
    assert kwargs["name"] == "box"
    assert kwargs["target"] == "dev@1.2.3.4:2222"
    assert kwargs["identity"] == "/k/id"
    assert kwargs["verify"] is False


def test_verify_flag_forwarded(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.add", return_value=0)
    runner.invoke(add, ["box", "1.2.3.4", "--verify"])
    assert prov.call_args.kwargs["verify"] is True


def test_nonzero_provider_rc_propagates(runner, mocker) -> None:
    mocker.patch("remo_cli.providers.added.add", return_value=1)
    result = runner.invoke(add, ["box", "1.2.3.4"])
    assert result.exit_code == 1


def test_invalid_name_rejected_before_provider(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.add", return_value=0)
    result = runner.invoke(add, ["!bad", "1.2.3.4"])
    assert result.exit_code == 2
    prov.assert_not_called()


def test_invalid_port_rejected_before_provider(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.add", return_value=0)
    result = runner.invoke(add, ["box", "1.2.3.4", "--port", "70000"])
    assert result.exit_code == 2
    prov.assert_not_called()
