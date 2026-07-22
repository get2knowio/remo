"""Tests for the `--all` flag on `remo proxmox sync` (cli/providers/proxmox.py)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.providers.proxmox import proxmox


@pytest.fixture
def runner():
    return CliRunner()


def test_default_sync_passes_include_all_false(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.proxmox.providers_proxmox.sync", return_value=None
    )
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--user", "root"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is False


def test_all_flag_threads_include_all_true(runner, mocker):
    spy = mocker.patch(
        "remo_cli.cli.providers.proxmox.providers_proxmox.sync", return_value=None
    )
    result = runner.invoke(proxmox, ["sync", "--host", "node", "--all"])
    assert result.exit_code == 0
    assert spy.call_args.kwargs["include_all"] is True
