"""CLI-layer tests for `remo projects` (spec 030, contracts/cli-projects.md)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.projects import projects
from remo_cli.core.errors import PreconditionError
from remo_cli.models.host import KnownHost
from remo_cli.providers import projects as provider
from tests.conftest import seed_registry

HOST = KnownHost("ssh", "mbp", "mac.example", "paul", instance_id="22", access_mode="direct")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _registry(tmp_config_dir):
    seed_registry(tmp_config_dir, [HOST])


def test_group_lists_the_three_commands(runner):
    out = runner.invoke(projects, ["--help"]).output
    for command in ("list", "add", "remove"):
        assert command in out


def test_add_resolves_the_host_and_forwards(runner, mocker):
    add = mocker.patch.object(provider, "add_project")
    result = runner.invoke(projects, ["add", "mbp", "~/code/app", "--name", "app"])
    assert result.exit_code == 0, result.output
    host, path, name = add.call_args.args
    assert host.name == "mbp" and path == "~/code/app" and name == "app"


def test_add_defaults_name_to_none(runner, mocker):
    add = mocker.patch.object(provider, "add_project")
    runner.invoke(projects, ["add", "mbp", "/p"])
    assert add.call_args.args[2] is None


def test_remove_forwards(runner, mocker):
    remove = mocker.patch.object(provider, "remove_project")
    assert runner.invoke(projects, ["remove", "mbp", "app"]).exit_code == 0
    assert remove.call_args.args[0].name == "mbp" and remove.call_args.args[1] == "app"


def test_list_renders_the_table(runner, mocker):
    mocker.patch.object(provider, "list_projects", return_value=[])
    result = runner.invoke(projects, ["list", "mbp"])
    assert result.exit_code == 0
    assert "remo projects add mbp PATH" in result.output


def test_unknown_host_is_a_precondition_failure(runner, mocker):
    add = mocker.patch.object(provider, "add_project")
    result = runner.invoke(projects, ["add", "ghost", "/p"])
    assert result.exit_code == 1
    assert "No host named 'ghost'" in result.output
    add.assert_not_called()


def test_provider_errors_map_to_exit_codes(runner, mocker):
    mocker.patch.object(provider, "list_projects", side_effect=PreconditionError("too old"))
    result = runner.invoke(projects, ["list", "mbp"])
    assert result.exit_code == 1 and "too old" in result.output
