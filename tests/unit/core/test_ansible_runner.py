"""Unit tests for remo.core.ansible_runner module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ansible_dir(tmp_path, mocker):
    """Create a temporary ansible directory and mock get_ansible_dir to return it."""
    ansible_dir = tmp_path / "ansible"
    ansible_dir.mkdir()
    mocker.patch("remo_cli.core.ansible_runner.get_ansible_dir", return_value=ansible_dir)
    return ansible_dir


@pytest.fixture
def mock_project_root(tmp_path, mocker):
    """Mock project root. The ansible dir is a child of the project root."""
    # Project root is the parent of the ansible dir
    return tmp_path


@pytest.fixture
def mock_verbose_off(mocker):
    """Ensure verbose mode is disabled."""
    mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=False)


# ---------------------------------------------------------------------------
# _find_co_installed
# ---------------------------------------------------------------------------


class TestFindCoInstalled:
    """Tests for _find_co_installed()."""

    def test_prefers_co_installed_binary(self, tmp_path, mocker):
        """When binary exists alongside sys.executable and is executable, use it."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.touch()
        mocker.patch("remo_cli.core.ansible_runner.sys.executable", str(fake_python))

        co_installed = bin_dir / "ansible-playbook"
        co_installed.touch()
        co_installed.chmod(0o755)

        from remo_cli.core.ansible_runner import _find_co_installed

        result = _find_co_installed("ansible-playbook")

        assert result == str(co_installed)

    def test_falls_back_to_name_when_not_found(self, tmp_path, mocker):
        """When binary does not exist alongside sys.executable, falls back to name."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.touch()
        mocker.patch("remo_cli.core.ansible_runner.sys.executable", str(fake_python))

        from remo_cli.core.ansible_runner import _find_co_installed

        result = _find_co_installed("ansible-playbook")

        assert result == "ansible-playbook"

    def test_falls_back_when_not_executable(self, tmp_path, mocker):
        """When binary exists but is NOT executable, falls back to name."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_python = bin_dir / "python"
        fake_python.touch()
        mocker.patch("remo_cli.core.ansible_runner.sys.executable", str(fake_python))

        co_installed = bin_dir / "ansible-playbook"
        co_installed.touch()
        co_installed.chmod(0o644)  # Not executable

        from remo_cli.core.ansible_runner import _find_co_installed

        result = _find_co_installed("ansible-playbook")

        assert result == "ansible-playbook"


# ---------------------------------------------------------------------------
# run_playbook - verbose mode
# ---------------------------------------------------------------------------


class TestRunPlaybookVerbose:
    """Tests for run_playbook() in verbose (pass-through) mode."""

    def test_basic_playbook_command(self, mock_ansible_dir, mocker):
        """Builds correct command with ansible_dir and playbook name."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="/usr/bin/ansible-playbook",
        )
        mock_run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        from remo_cli.core.ansible_runner import run_playbook

        rc = run_playbook("incus_bootstrap.yml", verbose=True)

        assert rc == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "/usr/bin/ansible-playbook"
        assert cmd[1] == "incus_bootstrap.yml"
        assert call_args[1]["cwd"] == str(mock_ansible_dir)

    def test_extra_vars_passed_to_command(self, mock_ansible_dir, mocker):
        """Extra args are appended to the ansible-playbook command."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )
        mock_run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        from remo_cli.core.ansible_runner import run_playbook

        run_playbook(
            "site.yml",
            extra_vars=["-e", "key=value", "-e", "other=val"],
            verbose=True,
        )

        cmd = mock_run.call_args[0][0]
        assert "-e" in cmd
        assert "key=value" in cmd
        assert "other=val" in cmd

    def test_inventory_passed_to_command(self, mock_ansible_dir, mocker):
        """When inventory is provided, -i <inventory> is appended."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )
        mock_run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        from remo_cli.core.ansible_runner import run_playbook

        run_playbook("site.yml", inventory="myhost,", verbose=True)

        cmd = mock_run.call_args[0][0]
        i_index = cmd.index("-i")
        assert cmd[i_index + 1] == "myhost,"

    def test_returns_nonzero_exit_code(self, mock_ansible_dir, mocker):
        """Returns the actual exit code from ansible-playbook."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )
        mock_run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        mock_run.return_value = MagicMock(returncode=4)

        from remo_cli.core.ansible_runner import run_playbook

        rc = run_playbook("failing.yml", verbose=True)

        assert rc == 4

    def test_verbose_flag_or_env_var(self, mock_ansible_dir, mocker):
        """When REMO_VERBOSE=1, verbose mode is used even without verbose=True."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )
        mock_run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        mock_run.return_value = MagicMock(returncode=0)

        from remo_cli.core.ansible_runner import run_playbook

        # verbose=False but is_verbose() returns True
        rc = run_playbook("site.yml", verbose=False)

        assert rc == 0
        # In verbose mode, subprocess.run is called directly (not Popen)
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# run_playbook - filtered mode
# ---------------------------------------------------------------------------


class TestRunPlaybookFiltered:
    """Tests for run_playbook() in filtered (non-verbose) mode."""

    def test_filtered_mode_uses_popen(self, mock_ansible_dir, mocker):
        """In filtered mode, subprocess.Popen is used instead of subprocess.run."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=False)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )

        # Create a mock Popen that simulates a completed process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Process already finished
        mock_proc.wait.return_value = 0
        mock_popen = mocker.patch("remo_cli.core.ansible_runner.subprocess.Popen", return_value=mock_proc)

        # Mock open to return an empty log file for the reading loop
        mocker.patch("remo_cli.core.ansible_runner.time.sleep")
        mocker.patch("remo_cli.core.ansible_runner.signal.signal")

        from remo_cli.core.ansible_runner import run_playbook

        rc = run_playbook("site.yml", verbose=False)

        assert rc == 0
        mock_popen.assert_called_once()

    def test_filtered_mode_sets_ansible_nocolor(self, mock_ansible_dir, mocker):
        """Filtered mode sets ANSIBLE_NOCOLOR=1 in the environment."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=False)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_popen = mocker.patch("remo_cli.core.ansible_runner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("remo_cli.core.ansible_runner.time.sleep")
        mocker.patch("remo_cli.core.ansible_runner.signal.signal")

        from remo_cli.core.ansible_runner import run_playbook

        run_playbook("site.yml", verbose=False)

        # Check that ANSIBLE_NOCOLOR was set in the env passed to Popen
        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["env"]["ANSIBLE_NOCOLOR"] == "1"

    def test_filtered_mode_passes_cwd(self, mock_ansible_dir, mocker):
        """Filtered mode sets cwd to ansible_dir."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=False)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_popen = mocker.patch("remo_cli.core.ansible_runner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("remo_cli.core.ansible_runner.time.sleep")
        mocker.patch("remo_cli.core.ansible_runner.signal.signal")

        from remo_cli.core.ansible_runner import run_playbook

        run_playbook("site.yml", verbose=False)

        call_kwargs = mock_popen.call_args[1]
        assert call_kwargs["cwd"] == str(mock_ansible_dir)

    def test_filtered_mode_returns_nonzero_on_failure(self, mock_ansible_dir, mocker):
        """Filtered mode returns non-zero exit code on failure."""
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=False)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            return_value="ansible-playbook",
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 2
        mock_proc.wait.return_value = 2
        mock_popen = mocker.patch("remo_cli.core.ansible_runner.subprocess.Popen", return_value=mock_proc)
        mocker.patch("remo_cli.core.ansible_runner.time.sleep")
        mocker.patch("remo_cli.core.ansible_runner.signal.signal")
        mocker.patch("remo_cli.core.ansible_runner.print_error")

        from remo_cli.core.ansible_runner import run_playbook

        rc = run_playbook("failing.yml", verbose=False)

        assert rc == 2


# ---------------------------------------------------------------------------
# _filter_line
# ---------------------------------------------------------------------------


class TestFilterLine:
    """Tests for the _filter_line() internal helper."""

    def test_play_line_is_formatted(self):
        """PLAY [...] **** lines are cleaned and returned."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = [""]
        result = _filter_line("PLAY [Setup server] **********", pending)

        assert result is not None
        assert "Setup server" in result

    def test_task_line_is_buffered(self):
        """TASK [...] **** lines are buffered in pending, not printed."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = [""]
        result = _filter_line("TASK [Install packages] **********", pending)

        assert result is None
        assert pending[0] == "Install packages"

    def test_ok_line_flushes_pending(self):
        """An ok: line flushes the buffered task name."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = ["Install packages"]
        result = _filter_line("ok: [myhost]", pending)

        assert result is not None
        assert "Install packages" in result
        assert pending[0] == ""

    def test_skipping_clears_pending(self):
        """A skipping: line clears the pending task without output."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = ["Check something"]
        result = _filter_line("skipping: [myhost]", pending)

        assert result is None
        assert pending[0] == ""

    def test_unrecognized_line_returns_none(self):
        """Lines that don't match any pattern are suppressed."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = [""]
        result = _filter_line("some random output line", pending)

        assert result is None

    def test_role_prefix_stripped_from_task(self):
        """Role prefix (e.g. 'rolename : ') is stripped from task names."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = [""]
        _filter_line("TASK [my_role : Install docker] **********", pending)

        assert pending[0] == "Install docker"

    def test_display_task_clears_pending(self):
        """Tasks starting with 'Display ' set pending to empty."""
        from remo_cli.core.ansible_runner import _filter_line

        pending = [""]
        _filter_line("TASK [Display status message] **********", pending)

        assert pending[0] == ""


# ---------------------------------------------------------------------------
# run_playbook(playbook_dir=...) — a provider plugin's own playbooks
# ---------------------------------------------------------------------------


class TestRunPlaybookPluginDir:
    """A plugin's playbook runs by absolute path from remo's ansible/ (so
    remo's ansible.cfg and shared roles apply) and is told where remo's tree
    is; its requirements.yml is installed under its own marker."""

    @pytest.fixture
    def plugin_dir(self, tmp_path):
        d = tmp_path / "plugin" / "ansible"
        d.mkdir(parents=True)
        return d

    @pytest.fixture
    def mock_run(self, mocker):
        mocker.patch("remo_cli.core.ansible_runner.is_verbose", return_value=True)
        mocker.patch(
            "remo_cli.core.ansible_runner._find_co_installed",
            side_effect=lambda name: f"/bin/{name}",
        )
        run = mocker.patch("remo_cli.core.ansible_runner.subprocess.run")
        run.return_value = MagicMock(returncode=0, stderr="")
        return run

    def _playbook_calls(self, mock_run):
        return [c for c in mock_run.call_args_list if c[0][0][0] == "/bin/ansible-playbook"]

    def _galaxy_calls(self, mock_run):
        return [c for c in mock_run.call_args_list if c[0][0][0] == "/bin/ansible-galaxy"]

    def test_runs_plugin_playbook_by_absolute_path_from_core_dir(
        self, mock_ansible_dir, plugin_dir, mock_run, tmp_config_dir
    ):
        import json

        from remo_cli.core.ansible_runner import run_playbook

        rc = run_playbook(
            "acme_site.yml", ["-e", "k=v"], playbook_dir=plugin_dir, verbose=True
        )

        assert rc == 0
        [call] = self._playbook_calls(mock_run)
        cmd = call[0][0]
        assert cmd[1] == str(plugin_dir / "acme_site.yml")
        assert cmd[2] == "-e"
        assert json.loads(cmd[3]) == {"remo_core_ansible_dir": str(mock_ansible_dir)}
        assert cmd[4:] == ["-e", "k=v"]
        assert call[1]["cwd"] == str(mock_ansible_dir)

    def test_core_playbook_gets_no_core_dir_var(self, mock_ansible_dir, mock_run, tmp_config_dir):
        from remo_cli.core.ansible_runner import run_playbook

        run_playbook("incus_site.yml", verbose=True)

        [call] = self._playbook_calls(mock_run)
        assert call[0][0] == ["/bin/ansible-playbook", "incus_site.yml"]

    def test_plugin_requirements_installed_once_under_own_marker(
        self, mock_ansible_dir, plugin_dir, mock_run, tmp_config_dir
    ):
        from remo_cli.core.ansible_runner import _plugin_marker_name, run_playbook

        (mock_ansible_dir / "requirements.yml").write_text("collections: []\n")
        (plugin_dir / "requirements.yml").write_text("collections: [{name: acme.cloud}]\n")

        run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)
        run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)

        installed = [c[0][0][-1] for c in self._galaxy_calls(mock_run)]
        # Each requirements file exactly once across two runs (idempotent).
        assert installed == [
            str(mock_ansible_dir / "requirements.yml"),
            str(plugin_dir / "requirements.yml"),
        ]
        assert (tmp_config_dir / "collections.lock").is_file()
        assert (tmp_config_dir / _plugin_marker_name(plugin_dir)).is_file()

    def test_changed_plugin_requirements_reinstall_without_touching_core(
        self, mock_ansible_dir, plugin_dir, mock_run, tmp_config_dir
    ):
        from remo_cli.core.ansible_runner import run_playbook

        (mock_ansible_dir / "requirements.yml").write_text("collections: []\n")
        reqs = plugin_dir / "requirements.yml"
        reqs.write_text("collections: [{name: acme.cloud}]\n")
        run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)

        reqs.write_text("collections: [{name: acme.cloud, version: '>=2'}]\n")
        run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)

        installed = [c[0][0][-1] for c in self._galaxy_calls(mock_run)]
        assert installed == [
            str(mock_ansible_dir / "requirements.yml"),
            str(reqs),
            str(reqs),
        ]

    def test_plugin_without_requirements_installs_nothing_extra(
        self, mock_ansible_dir, plugin_dir, mock_run, tmp_config_dir
    ):
        from remo_cli.core.ansible_runner import run_playbook

        run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)

        assert self._galaxy_calls(mock_run) == []
        assert len(self._playbook_calls(mock_run)) == 1

    def test_plugin_requirements_failure_exits_before_playbook(
        self, mock_ansible_dir, plugin_dir, mock_run, tmp_config_dir
    ):
        from remo_cli.core.ansible_runner import run_playbook

        (plugin_dir / "requirements.yml").write_text("collections: [{name: acme.cloud}]\n")
        mock_run.return_value = MagicMock(returncode=1, stderr="galaxy down")

        with pytest.raises(SystemExit):
            run_playbook("acme_site.yml", playbook_dir=plugin_dir, verbose=True)

        assert self._playbook_calls(mock_run) == []

    def test_marker_names_differ_per_plugin_dir(self, tmp_path):
        from remo_cli.core.ansible_runner import _plugin_marker_name

        a, b = tmp_path / "a", tmp_path / "b"
        assert _plugin_marker_name(a) != _plugin_marker_name(b)
        assert _plugin_marker_name(a) != "collections.lock"
