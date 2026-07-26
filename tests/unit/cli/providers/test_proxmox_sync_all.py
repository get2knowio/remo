"""Tests for the `remo proxmox sync` CLI flags (generated CLI, built from
``providers/proxmox_descriptor.py`` by ``cli/providers/factory.py``)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.factory import build_provider_group
from remo_cli.core.provider_registry import get_descriptor

proxmox = build_provider_group(get_descriptor("proxmox"))


@pytest.fixture
def runner():
    return CliRunner()


def test_default_sync_passes_include_all_false(runner, mocker):
    spy = mocker.patch("remo_cli.providers.proxmox.sync", return_value=0)
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--user", "root"])
    assert result.exit_code == 0, result.output
    assert spy.call_args.kwargs["include_all"] is False


def test_all_flag_threads_include_all_true(runner, mocker):
    spy = mocker.patch("remo_cli.providers.proxmox.sync", return_value=0)
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--all"])
    assert result.exit_code == 0, result.output
    assert spy.call_args.kwargs["include_all"] is True


def test_exit_code_propagates_from_sync(runner, mocker):
    mocker.patch("remo_cli.providers.proxmox.sync", return_value=1)
    result = runner.invoke(proxmox, ["sync", "--host", "node"])
    assert result.exit_code == 1


def test_yes_flag_threads_auto_confirm_true(runner, mocker):
    spy = mocker.patch("remo_cli.providers.proxmox.sync", return_value=0)
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--yes"])
    assert result.exit_code == 0, result.output
    assert spy.call_args.kwargs["auto_confirm"] is True


def test_dry_run_flag_threads_dry_run_true(runner, mocker):
    spy = mocker.patch("remo_cli.providers.proxmox.sync", return_value=0)
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert spy.call_args.kwargs["dry_run"] is True


def test_default_sync_passes_auto_confirm_and_dry_run_false(runner, mocker):
    spy = mocker.patch("remo_cli.providers.proxmox.sync", return_value=0)
    result = runner.invoke(proxmox, ["sync", "--host", "node"])
    assert result.exit_code == 0, result.output
    assert spy.call_args.kwargs["auto_confirm"] is False
    assert spy.call_args.kwargs["dry_run"] is False


def test_host_is_required(runner, mocker):
    # Proxmox differs from Incus here: --host has no default, so a bare
    # `sync` must fail as a Click usage error (exit 2), never construct a
    # SyncScope with an empty host.
    mocker.patch("remo_cli.providers.proxmox.sync")
    result = runner.invoke(proxmox, ["sync"])
    assert result.exit_code == 2
