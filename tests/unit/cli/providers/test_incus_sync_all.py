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
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=0
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--user", "u"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is False


def test_all_flag_threads_include_all_true(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=0
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--all"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is True


def test_exit_code_propagates_from_sync(runner, mocker):
    mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=1
    )
    result = runner.invoke(incus, ["sync", "--host", "h"])
    assert result.exit_code == 1


def test_yes_flag_threads_auto_confirm_true(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=0
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--yes"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["auto_confirm"] is True


def test_dry_run_flag_threads_dry_run_true(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=0
    )
    result = runner.invoke(incus, ["sync", "--host", "h", "--dry-run"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["dry_run"] is True


def test_default_sync_passes_auto_confirm_and_dry_run_false(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.incus.providers_incus.sync", return_value=0
    )
    result = runner.invoke(incus, ["sync", "--host", "h"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["auto_confirm"] is False
    assert spy.call_args.kwargs["dry_run"] is False
