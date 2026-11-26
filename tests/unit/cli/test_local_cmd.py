"""CLI-layer tests for `remo local` (spec 029, contracts/cli-local.md)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.local import local
from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.providers import local as provider


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_group_lists_the_five_commands(runner):
    result = runner.invoke(local, ["--help"])
    for command in ("setup", "check", "add", "remove", "list"):
        assert command in result.output


class TestSetupCommand:
    def test_forwards_every_option(self, runner, mocker):
        setup = mocker.patch.object(provider, "setup")
        result = runner.invoke(
            local, ["setup", "--name", "work", "--projects-dir", "/p", "--yes", "-v"]
        )
        assert result.exit_code == 0, result.output
        setup.assert_called_once_with(
            name="work", projects_dir="/p", assume_yes=True, verbose=True
        )

    def test_defaults(self, runner, mocker):
        setup = mocker.patch.object(provider, "setup")
        runner.invoke(local, ["setup"])
        setup.assert_called_once_with(
            name=None, projects_dir=None, assume_yes=False, verbose=False
        )

    def test_empty_projects_dir_clears(self, runner, mocker):
        setup = mocker.patch.object(provider, "setup")
        runner.invoke(local, ["setup", "--projects-dir", ""])
        assert setup.call_args.kwargs["projects_dir"] == ""

    def test_invalid_name_is_rejected_before_the_provider(self, runner, mocker):
        setup = mocker.patch.object(provider, "setup")
        result = runner.invoke(local, ["setup", "--name", "-bad"])
        assert result.exit_code != 0
        setup.assert_not_called()

    @pytest.mark.parametrize(
        "error,code",
        [(PreconditionError("tools missing"), 1), (OperationFailedError("rc=2"), 1)],
    )
    def test_typed_errors_become_exit_codes(self, runner, mocker, error, code):
        mocker.patch.object(provider, "setup", side_effect=error)
        result = runner.invoke(local, ["setup"])
        assert result.exit_code == code
        assert str(error) in result.output

    def test_missing_prerequisite_end_to_end_registers_nothing(
        self, runner, mocker, tmp_config_dir
    ):
        mocker.patch.object(
            provider, "check_prerequisites",
            return_value=[provider.Prerequisite("fzf", True, "missing", "no", "brew install fzf")],
        )
        play = mocker.patch.object(provider, "run_playbook")
        result = runner.invoke(local, ["setup"])
        assert result.exit_code == 1
        assert "brew install fzf" in result.output
        play.assert_not_called()
        assert provider.get_local_host() is None


class TestCheckCommand:
    def _results(self, *, missing: bool):
        rows = [provider.Prerequisite("git", True, "present", "/usr/bin/git", "")]
        if missing:
            rows.append(provider.Prerequisite("zellij", True, "missing", "no", "brew install zellij"))
        else:
            rows.append(
                provider.Prerequisite("container engine", True, "not_running", "stopped", "")
            )
        return rows

    def test_exit_0_when_every_required_tool_is_present_warnings_allowed(self, runner, mocker):
        mocker.patch.object(provider, "check_prerequisites", return_value=self._results(missing=False))
        result = runner.invoke(local, ["check"])
        assert result.exit_code == 0
        assert "not running" in result.output and "Local host:" in result.output

    def test_exit_1_when_a_required_tool_is_missing(self, runner, mocker):
        mocker.patch.object(provider, "check_prerequisites", return_value=self._results(missing=True))
        result = runner.invoke(local, ["check"])
        assert result.exit_code == 1
        assert "brew install zellij" in result.output

    def test_never_writes(self, runner, mocker, tmp_config_dir):
        mocker.patch.object(provider, "check_prerequisites", return_value=self._results(missing=False))
        play = mocker.patch.object(provider, "run_playbook")
        runner.invoke(local, ["check"])
        play.assert_not_called()
        assert not (tmp_config_dir / "registry.json").exists()


class TestProjectCommands:
    def test_add(self, runner, mocker):
        add = mocker.patch.object(provider, "add_project")
        result = runner.invoke(local, ["add", "/x/a", "--name", "alpha"])
        assert result.exit_code == 0, result.output
        add.assert_called_once_with("/x/a", "alpha")

    def test_remove(self, runner, mocker):
        rm = mocker.patch.object(provider, "remove_project")
        assert runner.invoke(local, ["remove", "a"]).exit_code == 0
        rm.assert_called_once_with("a")

    def test_list_renders_the_table(self, runner, mocker):
        mocker.patch.object(
            provider, "list_projects",
            return_value=[{"name": "a", "path": "/x/a", "source": "index", "status": "ok"}],
        )
        result = runner.invoke(local, ["list"])
        assert result.exit_code == 0
        assert "/x/a" in result.output and "added" in result.output

    def test_remo_host_refusal_is_one_actionable_line(self, runner, mocker):
        mocker.patch.object(
            provider, "add_project",
            side_effect=PreconditionError("no such directory: /nope"),
        )
        result = runner.invoke(local, ["add", "/nope"])
        assert result.exit_code == 1
        assert "no such directory: /nope" in result.output
        assert "Traceback" not in result.output

    def test_setup_not_completed(self, runner, tmp_config_dir):
        result = runner.invoke(local, ["list"])
        assert result.exit_code == 1
        assert "remo local setup" in result.output
