"""Unit tests for remo.core.rsync module."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, call

import pytest

from remo_cli.core.rsync import transfer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_ssh_opts():
    """Minimal SSH options list."""
    return ["-o", "StrictHostKeyChecking=no"]


@pytest.fixture
def ssh_target():
    return "remo@5.6.7.8"


# ---------------------------------------------------------------------------
# transfer() - command construction
# ---------------------------------------------------------------------------


class TestTransferCommandBuilding:
    """Tests for how transfer() constructs the rsync command."""

    def test_basic_transfer_builds_correct_command(
        self, mocker, basic_ssh_opts, ssh_target, tmp_path
    ):
        """A basic transfer builds rsync -az with correct -e ssh options."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = [f"{ssh_target}:/home/remo/file.txt"]
        dest = str(tmp_path / "file.txt")

        rc = transfer(basic_ssh_opts, ssh_target, sources, dest)

        assert rc == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        # First two args are rsync -az
        assert cmd[0] == "rsync"
        assert "-az" in cmd

        # -e option contains quoted SSH opts
        e_index = cmd.index("-e")
        e_value = cmd[e_index + 1]
        assert e_value.startswith("ssh")
        assert '"-o"' in e_value
        assert '"StrictHostKeyChecking=no"' in e_value

        # Sources and dest are at the end
        assert cmd[-2] == sources[0]
        assert cmd[-1] == dest

    def test_recursive_flag_adds_dash_r(self, mocker, basic_ssh_opts, ssh_target, tmp_path):
        """When recursive=True, -r is added to rsync command."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = ["/local/dir"]
        dest = f"{ssh_target}:/remote/dir"

        transfer(basic_ssh_opts, ssh_target, sources, dest, recursive=True)

        cmd = mock_run.call_args[0][0]
        assert "-r" in cmd

    def test_no_recursive_flag_when_false(self, mocker, basic_ssh_opts, ssh_target, tmp_path):
        """When recursive=False, no -r flag appears."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = ["/local/file.txt"]
        dest = f"{ssh_target}:/remote/file.txt"

        transfer(basic_ssh_opts, ssh_target, sources, dest, recursive=False)

        cmd = mock_run.call_args[0][0]
        assert "-r" not in cmd

    def test_progress_flag_adds_progress(self, mocker, basic_ssh_opts, ssh_target, tmp_path):
        """When progress=True, --progress is added to rsync command."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = ["/local/file.txt"]
        dest = f"{ssh_target}:/remote/file.txt"

        transfer(basic_ssh_opts, ssh_target, sources, dest, progress=True)

        cmd = mock_run.call_args[0][0]
        assert "--progress" in cmd

    def test_no_progress_flag_when_false(self, mocker, basic_ssh_opts, ssh_target, tmp_path):
        """When progress=False, no --progress flag appears."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = ["/local/file.txt"]
        dest = f"{ssh_target}:/remote/file.txt"

        transfer(basic_ssh_opts, ssh_target, sources, dest, progress=False)

        cmd = mock_run.call_args[0][0]
        assert "--progress" not in cmd


# ---------------------------------------------------------------------------
# transfer() - return codes
# ---------------------------------------------------------------------------


class TestTransferReturnCodes:
    """Tests for transfer() exit code handling."""

    def test_returns_zero_on_success(self, mocker, basic_ssh_opts, ssh_target):
        """Returns 0 when rsync succeeds."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        rc = transfer(basic_ssh_opts, ssh_target, ["/src"], "/dst")

        assert rc == 0

    def test_returns_nonzero_on_failure(self, mocker, basic_ssh_opts, ssh_target):
        """Returns non-zero exit code when rsync fails."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=23)

        rc = transfer(basic_ssh_opts, ssh_target, ["/src"], "/dst")

        assert rc == 23

    def test_handles_file_not_found_error(self, mocker, basic_ssh_opts, ssh_target):
        """When rsync is not installed (FileNotFoundError), returns 1."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.side_effect = FileNotFoundError("No such file or directory: 'rsync'")
        mocker.patch("remo_cli.core.rsync.print_error")

        rc = transfer(basic_ssh_opts, ssh_target, ["/src"], "/dst")

        assert rc == 1


# ---------------------------------------------------------------------------
# transfer() - progress mode stdout handling
# ---------------------------------------------------------------------------


class TestTransferProgressMode:
    """Tests for stdout pass-through behaviour in progress mode."""

    def test_progress_mode_does_not_suppress_stdout(
        self, mocker, basic_ssh_opts, ssh_target
    ):
        """With progress=True, subprocess.run is called without stdout=DEVNULL."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        transfer(basic_ssh_opts, ssh_target, ["/src"], "/dst", progress=True)

        kwargs = mock_run.call_args[1]
        # In progress mode, stdout should NOT be DEVNULL
        assert kwargs.get("stdout") != subprocess.DEVNULL

    def test_non_progress_mode_suppresses_stdout(
        self, mocker, basic_ssh_opts, ssh_target
    ):
        """With progress=False, subprocess.run is called with stdout=DEVNULL."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        transfer(basic_ssh_opts, ssh_target, ["/src"], "/dst", progress=False)

        kwargs = mock_run.call_args[1]
        assert kwargs.get("stdout") == subprocess.DEVNULL


# ---------------------------------------------------------------------------
# transfer() - multiple sources
# ---------------------------------------------------------------------------


class TestTransferMultipleSources:
    """Tests for multi-source transfers."""

    def test_multiple_sources_all_included(self, mocker, basic_ssh_opts, ssh_target):
        """All source paths are included in the rsync command."""
        mock_run = mocker.patch("remo_cli.core.rsync.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        sources = ["/local/a.txt", "/local/b.txt", "/local/c.txt"]
        dest = f"{ssh_target}:/remote/"

        transfer(basic_ssh_opts, ssh_target, sources, dest)

        cmd = mock_run.call_args[0][0]
        # All sources should appear in the command before the destination
        for src in sources:
            assert src in cmd
        # Destination is last
        assert cmd[-1] == dest
