"""CLI-layer tests for `remo remove` (feature 014, US2). Provider logic mocked."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.added import remove


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_success_calls_provider_and_exits_zero(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.remove", return_value=0)
    result = runner.invoke(remove, ["box", "--yes"])
    assert result.exit_code == 0
    prov.assert_called_once_with(name="box", assume_yes=True)


def test_confirm_flag_default_false(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.remove", return_value=0)
    runner.invoke(remove, ["box"])
    assert prov.call_args.kwargs["assume_yes"] is False


def test_nonzero_rc_propagates(runner, mocker) -> None:
    mocker.patch("remo_cli.providers.added.remove", return_value=1)
    result = runner.invoke(remove, ["box", "--yes"])
    assert result.exit_code == 1
