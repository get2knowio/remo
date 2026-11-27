"""providers/projects.py — generic project verbs over any host (spec 030, contracts/cli-projects.md)."""

from __future__ import annotations

import os

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.core.remo_host_client import RemoHostCommandError, SshTransportError
from remo_cli.models.host import KnownHost
from remo_cli.providers import projects
from tests.conftest import seed_registry

SSH_HOST = KnownHost("ssh", "mbp", "mac.example", "paul", instance_id="2222", access_mode="direct")
LOCAL = KnownHost("local", "local", "localhost", "paul", access_mode="direct")


@pytest.fixture
def run(mocker):
    mocker.patch.object(
        projects.get_host_access(SSH_HOST).__class__, "command_prefix",
        return_value=["ssh", "paul@mac.example"],
    )
    return mocker.patch.object(
        projects, "run_remo_host_json",
        return_value={"name": "a", "path": "/x/a", "status": "ok"},
    )


class TestRemoteHost:
    def test_add_sends_the_path_verbatim_including_tilde(self, run):
        projects.add_project(SSH_HOST, "~/code/My App", "app")
        prefix, verb = run.call_args.args
        assert prefix == ["ssh", "paul@mac.example"]
        assert verb == "projects add"
        assert run.call_args.kwargs == {"path": "~/code/My App", "name": "app"}

    def test_relative_path_is_not_made_absolute_client_side(self, run):
        projects.add_project(SSH_HOST, "rel/a")
        assert run.call_args.kwargs["path"] == "rel/a"

    def test_remove_and_list(self, run):
        run.side_effect = [{"name": "a", "removed": True}, {"projects": [{"name": "a"}]}]
        projects.remove_project(SSH_HOST, "a")
        assert run.call_args.args[1] == "projects remove"
        assert run.call_args.kwargs == {"project": "a"}
        assert projects.list_projects(SSH_HOST) == [{"name": "a"}]

    def test_non_list_payload_is_empty(self, run):
        run.return_value = {"projects": "oops"}
        assert projects.list_projects(SSH_HOST) == []

    def test_exit_3_is_a_precondition_error_with_the_hosts_text(self, run):
        run.side_effect = RemoHostCommandError(3, "no such directory: /x\n", verb="projects add")
        with pytest.raises(PreconditionError, match="no such directory: /x"):
            projects.add_project(SSH_HOST, "/x")

    @pytest.mark.parametrize("code", [4, 127])
    def test_old_host_tools_name_the_upgrade_command(self, run, code):
        run.side_effect = RemoHostCommandError(code, "", verb="projects list")
        with pytest.raises(PreconditionError) as exc:
            projects.list_projects(SSH_HOST)
        assert "too old for project management" in str(exc.value)
        assert "remo configure mbp" in str(exc.value)

    def test_host_tools_without_a_projects_verb_name_the_upgrade_command(self, run):
        run.side_effect = RemoHostCommandError(
            2, "remo-host: unrecognized subcommand: projects\n", verb="projects list"
        )
        with pytest.raises(PreconditionError) as exc:
            projects.list_projects(SSH_HOST)
        assert "too old for project management" in str(exc.value)
        assert "remo configure mbp" in str(exc.value)

    def test_a_plain_usage_error_is_not_mistaken_for_old_tools(self, run):
        run.side_effect = RemoHostCommandError(
            2, "remo-host: projects add: --json is required\n", verb="projects add"
        )
        with pytest.raises(OperationFailedError):
            projects.add_project(SSH_HOST, "/x")

    def test_other_failures_are_operation_failures(self, run):
        run.side_effect = RemoHostCommandError(1, "bad", verb="projects list")
        with pytest.raises(OperationFailedError):
            projects.list_projects(SSH_HOST)
        run.side_effect = SshTransportError("boom")
        with pytest.raises(OperationFailedError):
            projects.list_projects(SSH_HOST)

    def test_hints_name_remo_projects_add_host(self):
        assert "remo projects add mbp PATH" in projects.render_projects([], SSH_HOST)
        table = projects.render_projects(
            [{"name": "a", "path": "/x/a", "source": "index", "status": "missing"}], SSH_HOST
        )
        assert "remo projects add mbp <new-path> --name a" in table

    def test_local_hints_keep_remo_local_add(self):
        assert "remo local add PATH" in projects.render_projects([], LOCAL)
        assert "remo local add PATH" in projects.render_projects([])


def test_local_host_path_is_made_absolute_client_side(mocker):
    mocker.patch.object(
        projects.get_host_access(LOCAL).__class__, "command_prefix", return_value=["bash"]
    )
    run = mocker.patch.object(projects, "run_remo_host_json", return_value={"name": "a"})
    projects.add_project(LOCAL, "rel/a")
    assert run.call_args.kwargs["path"] == os.path.abspath("rel/a")


class TestResolveHost:
    def test_known_and_unknown(self, tmp_config_dir):
        seed_registry(tmp_config_dir, [SSH_HOST])
        assert projects.resolve_host("mbp").name == "mbp"
        with pytest.raises(PreconditionError, match="No host named 'nope'"):
            projects.resolve_host("nope")
