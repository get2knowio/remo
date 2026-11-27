"""`remo shell` against the local host (spec 029): no SSH anywhere."""

from __future__ import annotations

import re
import termios

import pytest
from click.testing import CliRunner

from remo_cli.cli.shell import shell
from remo_cli.core.errors import PreconditionError
from remo_cli.core.ssh import PROBE_COMMAND
from remo_cli.models.host import KnownHost
from tests.conftest import seed_registry

BASH = "/opt/homebrew/bin/bash"
LOCAL = KnownHost("local", "local", "localhost", "paul", access_mode="direct")


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch, tmp_config_dir):
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".local" / "state" / "remo"
    state.mkdir(parents=True)
    (state / "local.env").write_text(f"REMO_BASH={BASH}\n")
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "project-menu").write_text("#!/bin/bash\n")
    (tmp_path / ".remo-version").write_text("0.0.1\n")
    seed_registry(tmp_config_dir, [LOCAL])
    return tmp_path


@pytest.fixture
def run(mocker, home):
    mocker.patch("remo_cli.core.ssh.termios.tcgetattr", side_effect=termios.error)
    mocker.patch("remo_cli.core.ssh.reset_terminal")
    mocker.patch("remo_cli.core.ssh.sys")
    mocker.patch("remo_cli.core.ssh.shutil.which", return_value=None)
    return mocker.patch(
        "remo_cli.core.ssh.subprocess.run", return_value=mocker.MagicMock(returncode=0)
    )


def _invoke(runner, *args):
    return runner.invoke(shell, ["local", "--no-update-check", *args])


class TestMenuAndShortcuts:
    def test_menu_runs_project_menu_locally(self, runner, run, home):
        result = _invoke(runner)
        assert result.exit_code == 0, result.output
        argv = run.call_args.args[0]
        assert argv == [BASH, "-c", 'exec "$HOME/.local/bin/project-menu"']
        assert "ssh" not in " ".join(argv).split()
        env = run.call_args.kwargs["env"]
        assert env["PATH"].startswith(f"{home}/.local/bin")

    def test_terminal_is_reset_afterwards(self, runner, run, mocker):
        reset = mocker.patch("remo_cli.core.ssh.reset_terminal")
        _invoke(runner)
        reset.assert_called_once()

    def test_project_runs_project_launch(self, runner, run):
        _invoke(runner, "-p", "a")
        assert run.call_args.args[0] == [
            BASH, "-c", "~/.local/bin/project-launch --project a",
        ]

    def test_exec(self, runner, run):
        _invoke(runner, "-p", "a", "--exec", "git status")
        assert run.call_args.args[0] == [
            BASH, "-c", "~/.local/bin/project-launch --project a --exec 'git status'",
        ]

    def test_detach(self, runner, run):
        _invoke(runner, "-p", "a", "--detach", "--exec", "make")
        assert run.call_args.args[0] == [
            BASH, "-c", "~/.local/bin/project-launch --project a --detach --exec make",
        ]

    def test_exec_exit_status_propagates(self, runner, run, mocker):
        run.return_value = mocker.MagicMock(returncode=7)
        result = _invoke(runner, "-p", "a", "--exec", "false")
        assert result.exit_code == 7

    @pytest.mark.parametrize("args", [(), ("-p", "a")])
    def test_leaving_the_menu_or_a_session_is_not_a_failure(self, runner, run, mocker, args):
        # project-menu exits 1 when the user presses Esc; that is a normal exit.
        run.return_value = mocker.MagicMock(returncode=1)
        result = _invoke(runner, *args)
        assert result.exit_code == 0, result.output

    def test_tab_key_reaches_the_scripts_by_env_not_sendenv(self, runner, run, monkeypatch):
        monkeypatch.setenv("KITTY_WINDOW_ID", "7")
        _invoke(runner, "-p", "a")
        argv = run.call_args.args[0]
        assert not any("SendEnv" in a for a in argv)
        assert re.fullmatch(r"[0-9a-f]{32}", run.call_args.kwargs["env"]["REMO_TAB_KEY"])

    def test_no_ssh_argv_anywhere(self, runner, run):
        _invoke(runner)
        _invoke(runner, "-p", "a")
        for call in run.call_args_list:
            assert call.args[0][0] == BASH


class TestRefusals:
    def test_tunnels_are_refused_without_running_anything(self, runner, run):
        result = _invoke(runner, "-L", "8080:8080")
        assert result.exit_code == 1
        assert "port tunnels do not apply to the local host" in result.output
        run.assert_not_called()

    def test_setup_not_completed(self, runner, run, home):
        (home / ".local/state/remo/local.env").unlink()
        result = _invoke(runner)
        assert result.exit_code == 1
        assert "remo local setup" in result.output
        run.assert_not_called()


class TestVersionCheck:
    @pytest.fixture
    def checked(self, mocker, run):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        return run

    def test_marker_is_read_through_local_access(self, runner, checked, home, mocker):
        (home / ".remo-version").write_text("0.9.0\n")
        checked.return_value = mocker.MagicMock(returncode=0, stdout="0.9.0\n", stderr="")
        result = runner.invoke(shell, ["local"])
        assert result.exit_code == 0, result.output
        probe = checked.call_args_list[0]
        assert probe.args[0] == [BASH, "-c", PROBE_COMMAND]

    def test_older_marker_offers_local_setup_and_runs_it(self, runner, checked, mocker):
        checked.side_effect = [
            mocker.MagicMock(returncode=0, stdout="0.8.0\n", stderr=""),
            mocker.MagicMock(returncode=0),
        ]
        confirm = mocker.patch("remo_cli.core.output.confirm", return_value=True)
        setup = mocker.patch("remo_cli.providers.local.setup")
        result = runner.invoke(shell, ["local"])
        assert result.exit_code == 0, result.output
        assert "remo local setup" in confirm.call_args.args[0]
        setup.assert_called_once_with(assume_yes=True)

    def test_declining_the_offer_still_connects(self, runner, checked, mocker):
        checked.side_effect = [
            mocker.MagicMock(returncode=0, stdout="0.8.0\n", stderr=""),
            mocker.MagicMock(returncode=0),
        ]
        mocker.patch("remo_cli.core.output.confirm", return_value=False)
        setup = mocker.patch("remo_cli.providers.local.setup")
        runner.invoke(shell, ["local"])
        setup.assert_not_called()

    def test_missing_marker_means_setup_never_completed(self, runner, checked, mocker):
        checked.return_value = mocker.MagicMock(returncode=1, stdout="", stderr="")
        result = runner.invoke(shell, ["local"])
        assert result.exit_code == 1
        assert "remo local setup has not completed" in result.output
        assert checked.call_count == 1  # never reached the menu

    def test_failed_setup_upgrade_can_still_connect_anyway(self, runner, checked, mocker):
        checked.side_effect = [
            mocker.MagicMock(returncode=0, stdout="0.8.0\n", stderr=""),
            mocker.MagicMock(returncode=0),
        ]
        mocker.patch("remo_cli.core.output.confirm", side_effect=[True, True])
        mocker.patch(
            "remo_cli.providers.local.setup", side_effect=PreconditionError("tools missing")
        )
        result = runner.invoke(shell, ["local"])
        assert result.exit_code == 0
        assert "Tools upgrade for 'local' failed" in result.output


def test_a_vanished_recorded_bash_names_the_fix_instead_of_a_traceback(runner, run):
    run.side_effect = FileNotFoundError(2, "No such file or directory", BASH)
    result = _invoke(runner)
    assert result.exit_code == 1
    assert "re-run: remo local setup" in result.output
    assert not isinstance(result.exception, FileNotFoundError)
