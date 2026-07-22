"""Tests for the `--all` flag on `remo incus sync` (cli/providers/incus.py)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.incus import incus


@pytest.fixture
def runner():
    return CliRunner()


def test_default_sync_passes_include_all_false(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=None
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--user", "u"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is False


def test_all_flag_threads_include_all_true(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=None
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--all"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is True
