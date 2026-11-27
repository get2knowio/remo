"""providers/local.py — prerequisites, setup, and the project verbs (spec 029)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.core.known_hosts import get_known_hosts, save_known_host
from remo_cli.core.remo_host_client import RemoHostCommandError, SshTransportError
from remo_cli.models.host import KnownHost
from remo_cli.providers import local
from remo_cli.providers import projects as projects_mod
from tests.conftest import seed_registry

LOCAL = KnownHost("local", "local", "localhost", "paul", access_mode="direct")


def _shim(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def toolbox(tmp_path, monkeypatch):
    """A PATH holding only shims, plus a controllable platform."""
    bin_dir = tmp_path / "shims"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("HOMEBREW_PREFIX", raising=False)
    monkeypatch.setattr(local.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(local, "OS_RELEASE_PATH", tmp_path / "no-os-release")
    # The two absolute Homebrew candidates must not leak the host's own bash.
    monkeypatch.setattr(
        local, "_bash_candidates",
        lambda: [str(bin_dir / "bash")],
    )
    return bin_dir


def _full(toolbox: Path, *, docker_ok: bool = True) -> None:
    _shim(toolbox, "bash", 'echo "5 5.2.37"')
    # `bash -c '<printf ...>'` — answer the probe the way real bash would.
    (toolbox / "bash").write_text('#!/bin/sh\nprintf "5 5.2.37"\n')
    for tool in ("git", "zellij", "fzf", "deacon", "gh"):
        _shim(toolbox, tool, "exit 0")
    _shim(toolbox, "docker", "exit 0" if docker_ok else "exit 1")


def _by_name(results):
    return {r.name: r for r in results}


class TestCheckPrerequisites:
    def test_everything_present(self, toolbox):
        _full(toolbox)
        results = _by_name(local.check_prerequisites())
        assert all(r.status == "present" for r in results.values())
        assert results["bash >= 4"].detail.endswith("(5.2.37)")
        assert results["devcontainer runtime"].detail.startswith("deacon")
        assert local.missing_required(list(results.values())) == []

    @pytest.mark.parametrize(
        "tool,hint",
        [
            ("git", "brew install git"),
            ("zellij", "brew install zellij"),
            ("fzf", "brew install fzf"),
        ],
    )
    def test_each_missing_required_tool_is_reported_with_a_brew_hint(self, toolbox, tool, hint):
        _full(toolbox)
        (toolbox / tool).unlink()
        results = _by_name(local.check_prerequisites())
        assert results[tool].status == "missing" and results[tool].required
        assert results[tool].install_hint == hint
        assert [r.name for r in local.missing_required(list(results.values()))] == [tool]

    def test_bash_3_2_only_is_missing(self, toolbox):
        _full(toolbox)
        (toolbox / "bash").write_text('#!/bin/sh\nprintf "3 3.2.57"\n')
        r = _by_name(local.check_prerequisites())["bash >= 4"]
        assert r.status == "missing" and r.install_hint == "brew install bash"

    def test_no_devcontainer_runtime(self, toolbox):
        _full(toolbox)
        (toolbox / "deacon").unlink()
        r = _by_name(local.check_prerequisites())["devcontainer runtime"]
        assert r.status == "missing" and "deacon" in r.install_hint

    def test_deacon_wins_over_the_reference_cli(self, toolbox):
        _full(toolbox)
        _shim(toolbox, "devcontainer", "exit 0")
        assert local.detect_devcontainer_runtime()[0] == "deacon"
        (toolbox / "deacon").unlink()
        assert local.detect_devcontainer_runtime()[0] == "devcontainer"

    def test_no_docker_is_missing(self, toolbox):
        _full(toolbox)
        (toolbox / "docker").unlink()
        r = _by_name(local.check_prerequisites())["container engine"]
        assert r.status == "missing" and r.required

    def test_docker_present_but_not_running(self, toolbox):
        _full(toolbox, docker_ok=False)
        r = _by_name(local.check_prerequisites())["container engine"]
        assert r.status == "not_running"
        assert "Docker Desktop" in r.detail
        assert local.missing_required(local.check_prerequisites()) == []

    def test_gh_is_optional(self, toolbox):
        _full(toolbox)
        (toolbox / "gh").unlink()
        results = local.check_prerequisites()
        gh = _by_name(results)["gh"]
        assert gh.status == "missing" and not gh.required
        assert local.missing_required(results) == []

    @pytest.mark.parametrize(
        "os_release,expected",
        [
            ('ID=ubuntu\nID_LIKE=debian\n', "sudo apt install git"),
            ('ID=fedora\n', "sudo dnf install git"),
            ('ID=endeavouros\nID_LIKE=arch\n', "sudo pacman -S git"),
            ('ID=nixos\n', "https://git-scm.com/downloads"),
        ],
    )
    def test_linux_hints_follow_os_release(self, toolbox, monkeypatch, tmp_path, os_release, expected):
        _full(toolbox)
        (toolbox / "git").unlink()
        release = tmp_path / "os-release"
        release.write_text(os_release)
        monkeypatch.setattr(local.platform, "system", lambda: "Linux")
        monkeypatch.setattr(local, "OS_RELEASE_PATH", release)
        assert _by_name(local.check_prerequisites())["git"].install_hint == expected

    def test_zellij_has_no_apt_package_so_it_points_at_the_docs(self, toolbox, monkeypatch, tmp_path):
        _full(toolbox)
        (toolbox / "zellij").unlink()
        release = tmp_path / "os-release"
        release.write_text("ID=debian\n")
        monkeypatch.setattr(local.platform, "system", lambda: "Linux")
        monkeypatch.setattr(local, "OS_RELEASE_PATH", release)
        assert _by_name(local.check_prerequisites())["zellij"].install_hint == local.ZELLIJ_URL

    def test_table_renders_every_row(self, toolbox):
        _full(toolbox, docker_ok=False)
        (toolbox / "zellij").unlink()
        table = local.render_prerequisites(local.check_prerequisites())
        assert "TOOL" in table and "brew install zellij" in table
        assert "not running" in table and "gh (optional)" in table


@pytest.fixture
def home(tmp_path, monkeypatch, tmp_config_dir):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


@pytest.fixture
def ready(mocker, home):
    """Prerequisites satisfied; the playbook is a recorded no-op."""
    ok = [
        local.Prerequisite("bash >= 4", True, "present", "/b/bash (5.2)", ""),
    ]
    mocker.patch.object(local, "check_prerequisites", return_value=ok)
    mocker.patch.object(local, "find_bash", return_value=("/b/bash", "5.2"))
    mocker.patch.object(local, "detect_devcontainer_runtime", return_value=("deacon", "/b/deacon"))
    return mocker.patch.object(local, "run_playbook", return_value=0)


def _play_vars(extra: list[str]) -> dict:
    """The one JSON ``-e`` document local setup passes to the play."""
    assert extra[0] == "-e" and len(extra) == 2
    return json.loads(extra[1])


class TestSetup:
    def test_registers_the_single_local_entry_and_passes_the_vars(self, ready):
        result = local.setup()
        assert result.changed and result.name == "local"
        entries = [h for h in get_known_hosts() if h.type == "local"]
        assert len(entries) == 1 and entries[0].host == "localhost"
        assert entries[0].access_mode == "direct"
        args = ready.call_args.args
        assert args[0] == "local_setup.yml"
        extra = _play_vars(args[1])
        assert extra["ansible_python_interpreter"] == os.sys.executable
        assert extra["remo_bash_path"] == "/b/bash"
        assert extra["devcontainer_runtime"] == "deacon"

    def test_a_projects_dir_with_spaces_reaches_the_play_whole(self, ready, tmp_path):
        # key=value extra vars split at the first space; the JSON form does not.
        folder = tmp_path / "My Projects"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        assert _play_vars(ready.call_args.args[1])["remo_local_projects_dir"] == str(folder)

    def test_runs_without_privilege_escalation(self, ready):
        seen = {}
        ready.side_effect = lambda *a, **k: seen.setdefault("become", os.environ.get("ANSIBLE_BECOME")) and 0
        local.setup()
        assert seen["become"] == "False"
        assert os.environ.get("ANSIBLE_BECOME") != "False"  # restored afterwards

    def test_second_run_is_a_noop(self, ready, capsys):
        local.setup()
        capsys.readouterr()
        result = local.setup()
        assert result.changed is False
        assert "Already up to date" in capsys.readouterr().out
        assert len([h for h in get_known_hosts() if h.type == "local"]) == 1

    def test_name_renames_instead_of_adding_a_second(self, ready):
        local.setup()
        local.setup(name="work")
        entries = [h for h in get_known_hosts() if h.type == "local"]
        assert [h.name for h in entries] == ["work"]

    def test_projects_dir_set_keep_and_clear(self, ready, tmp_path):
        folder = tmp_path / "proj"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        assert local.get_local_host().projects_dir == str(folder)
        local.setup()  # None keeps it
        assert local.get_local_host().projects_dir == str(folder)
        assert _play_vars(ready.call_args.args[1])["remo_local_projects_dir"] == str(folder)
        local.setup(projects_dir="")  # "" clears it
        assert local.get_local_host().projects_dir == ""

    def test_projects_dir_must_exist(self, ready, tmp_path):
        with pytest.raises(PreconditionError, match="not a directory"):
            local.setup(projects_dir=str(tmp_path / "nope"))
        ready.assert_not_called()

    def test_name_clash_with_a_non_local_entry(self, ready):
        save_known_host(KnownHost("ssh", "local", "h", "u", instance_id="22", access_mode="direct"))
        with pytest.raises(PreconditionError, match="already registered"):
            local.setup()
        ready.assert_not_called()

    def test_playbook_failure_registers_nothing(self, ready):
        ready.return_value = 2
        with pytest.raises(OperationFailedError, match="rc=2"):
            local.setup()
        assert local.get_local_host() is None

    def test_missing_ansible(self, ready):
        from remo_cli.core.errors import MissingDependencyError

        ready.side_effect = FileNotFoundError("ansible-playbook")
        with pytest.raises(MissingDependencyError):
            local.setup()

    def test_missing_required_tool_fails_before_the_playbook(self, mocker, home, capsys):
        missing = [local.Prerequisite("zellij", True, "missing", "not found", "brew install zellij")]
        mocker.patch.object(local, "check_prerequisites", return_value=missing)
        play = mocker.patch.object(local, "run_playbook")
        with pytest.raises(PreconditionError, match="zellij"):
            local.setup()
        play.assert_not_called()
        assert local.get_local_host() is None
        assert "brew install zellij" in capsys.readouterr().out

    def test_warnings_for_engine_and_optional_tools(self, ready, mocker, capsys):
        mocker.patch.object(
            local, "check_prerequisites",
            return_value=[
                local.Prerequisite("container engine", True, "not_running", "stopped", ""),
                local.Prerequisite("gh", False, "missing", "no", "brew install gh"),
            ],
        )
        local.setup()
        out = capsys.readouterr().out
        assert "container engine: stopped" in out and "gh not found" in out


class TestProjectVerbs:
    @pytest.fixture
    def host_ready(self, tmp_path, home, tmp_config_dir):
        state = home / ".local" / "state" / "remo"
        state.mkdir(parents=True)
        (state / "local.env").write_text("REMO_BASH=/b/bash\n")
        (home / ".local" / "bin").mkdir(parents=True)
        (home / ".local" / "bin" / "project-menu").write_text("")
        seed_registry(tmp_config_dir, [LOCAL])

    def test_add_sends_the_local_prefix_and_an_absolute_path(self, host_ready, mocker, tmp_path):
        run = mocker.patch.object(
            projects_mod, "run_remo_host_json",
            return_value={"name": "a", "path": "/x/a", "status": "ok"},
        )
        local.add_project("rel/a", "a")
        prefix, verb = run.call_args.args
        assert prefix == ["/b/bash", "-c", 'eval "$*"', "remo-local"]
        assert verb == "projects add"
        assert run.call_args.kwargs["path"] == os.path.abspath("rel/a")
        assert run.call_args.kwargs["name"] == "a"

    def test_remove_and_list(self, host_ready, mocker):
        run = mocker.patch.object(
            projects_mod, "run_remo_host_json",
            side_effect=[{"name": "a", "removed": True}, {"projects": [{"name": "a"}]}],
        )
        local.remove_project("a")
        assert run.call_args.args[1] == "projects remove"
        assert run.call_args.kwargs["project"] == "a"
        assert local.list_projects() == [{"name": "a"}]
        assert run.call_args.args[1] == "projects list"

    def test_exit_3_becomes_a_precondition_error_with_the_hosts_message(self, host_ready, mocker):
        mocker.patch.object(
            projects_mod, "run_remo_host_json",
            side_effect=RemoHostCommandError(
                3, "project 'a' already exists at /x/a — use --name to pick another\n",
                verb="projects add",
            ),
        )
        with pytest.raises(PreconditionError, match="use --name to pick another"):
            local.add_project("/x/a")

    def test_other_failures_are_operation_failures(self, host_ready, mocker):
        mocker.patch.object(projects_mod, "run_remo_host_json", side_effect=SshTransportError("boom"))
        with pytest.raises(OperationFailedError):
            local.list_projects()
        mocker.patch.object(
            projects_mod, "run_remo_host_json",
            side_effect=RemoHostCommandError(1, "bad", verb="projects list"),
        )
        with pytest.raises(OperationFailedError):
            local.list_projects()

    def test_setup_not_completed_is_refused(self, home, tmp_config_dir, mocker):
        run = mocker.patch.object(projects_mod, "run_remo_host_json")
        with pytest.raises(PreconditionError, match="remo local setup"):
            local.list_projects()
        run.assert_not_called()

    def test_scripts_missing_is_refused(self, home, tmp_config_dir, mocker):
        seed_registry(tmp_config_dir, [LOCAL])  # registered, scripts absent
        run = mocker.patch.object(projects_mod, "run_remo_host_json")
        with pytest.raises(PreconditionError, match="remo local setup"):
            local.add_project("/x/a")
        run.assert_not_called()


class TestRendering:
    def test_projects_table_marks_missing_with_the_fix(self):
        table = local.render_projects([
            {"name": "a", "path": "/x/a", "source": "index", "status": "ok"},
            {"name": "b", "path": "/y/b", "source": "index", "status": "missing"},
            {"name": "c", "path": "/p/c", "source": "dir", "status": "ok"},
        ])
        assert "added" in table and "folder" in table
        assert "missing — re-add: remo local add <new-path> --name b" in table

    def test_empty(self):
        assert "remo local add PATH" in local.render_projects([])

    def test_setup_state_line(self, home, tmp_config_dir):
        assert "not set up" in local.local_setup_state()
        seed_registry(tmp_config_dir, [LOCAL])
        (home / ".remo-version").write_text("5.0.0\n")
        (home / ".local/state/remo").mkdir(parents=True)
        (home / ".local/state/remo/local.env").write_text("REMO_BASH=/b\n")
        assert local.local_setup_state() == "Local host: local (setup complete, scripts 5.0.0)"


class TestIndexVerbQuoting:
    """The local prefix re-parses the words with `eval "$*"` (as ssh does): a
    typed name must reach remo-host as one word, never split or expanded."""

    @pytest.mark.parametrize(
        ("verb", "kwargs", "flag", "value"),
        [
            ("projects add", {"path": "/x/my app", "name": "my app"}, "--name", "my app"),
            ("projects add", {"path": "/x/a", "name": "a;touch pwned"}, "--name", "a;touch pwned"),
            ("projects remove", {"project": "$(touch pwned)"}, "--project", "$(touch pwned)"),
        ],
    )
    def test_values_survive_the_shell_rejoin(self, mocker, tmp_path, verb, kwargs, flag, value):
        import subprocess

        from remo_cli.core import remo_host_client

        seen = {}

        def fake_invoke(argv, *, timeout):
            # Re-join exactly like the local prefix does and let bash parse it.
            words = subprocess.run(
                ["bash", "-c", 'eval "printf \'%s\\n\' $*"', "x", *argv[1:]],
                capture_output=True, text=True, cwd=tmp_path,
            ).stdout.splitlines()
            seen["words"] = words
            return mocker.MagicMock(returncode=0, stdout=b'{"protocol_version":1}', stderr=b"")

        mocker.patch.object(remo_host_client, "_invoke", side_effect=fake_invoke)
        remo_host_client.run_remo_host_json(["prefix"], verb, **kwargs)
        words = seen["words"]
        assert words[words.index(flag) + 1] == value
        if "path" in kwargs:
            assert words[words.index("--path") + 1] == kwargs["path"]
        assert not (tmp_path / "pwned").exists()


class TestProjectsDirSurvivesRemoveLocal:
    """#281: `remo remove local` keeps the scripts and local.env; a later setup
    without --projects-dir must restore the folder, not silently drop it."""

    @pytest.fixture
    def play(self, ready):
        # Stand-in for ansible/local_setup.yml's local.env task (its real output
        # is pinned by tests/unit/test_local_setup_playbook.py).
        def _write_env(playbook, extra, **kwargs):
            play_vars = _play_vars(extra)
            env = Path(os.environ["HOME"]) / ".local" / "state" / "remo" / "local.env"
            env.parent.mkdir(parents=True, exist_ok=True)
            env.write_text(
                f"REMO_BASH={play_vars['remo_bash_path']}\n"
                f"REMO_PROJECTS_DIR={play_vars['remo_local_projects_dir']}\n"
            )
            return 0

        ready.side_effect = _write_env
        return ready

    def _remove_local(self):
        from remo_cli.core.known_hosts import remove_known_host

        remove_known_host("local", "local")
        assert local.get_local_host() is None

    def test_setup_after_remove_restores_the_folder(self, play, tmp_path, capsys):
        folder = tmp_path / "proj"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        self._remove_local()
        capsys.readouterr()
        result = local.setup()
        assert local.get_local_host().projects_dir == str(folder)
        assert _play_vars(play.call_args.args[1])["remo_local_projects_dir"] == str(folder)
        assert result.changed  # the entry was re-created
        assert "Restoring the projects folder" in capsys.readouterr().out
        # ...and a further run is a no-op.
        assert local.setup().changed is False

    def test_a_path_with_spaces_round_trips(self, play, tmp_path):
        folder = tmp_path / "My Projects  dir"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        assert local.persisted_projects_dir() == str(folder)
        self._remove_local()
        local.setup()
        assert local.get_local_host().projects_dir == str(folder)
        assert _play_vars(play.call_args.args[1])["remo_local_projects_dir"] == str(folder)

    def test_explicit_empty_clears_it_and_the_persisted_value(self, play, tmp_path):
        folder = tmp_path / "proj"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        self._remove_local()
        local.setup(projects_dir="")
        assert local.get_local_host().projects_dir == ""
        assert _play_vars(play.call_args.args[1])["remo_local_projects_dir"] == ""
        assert local.persisted_projects_dir() == ""
        # Cleared stays cleared across another remove + setup.
        self._remove_local()
        local.setup()
        assert local.get_local_host().projects_dir == ""

    def test_registered_value_wins_over_the_persisted_one(self, play, tmp_path):
        folder = tmp_path / "proj"
        folder.mkdir()
        local.setup()  # registered with no folder
        local.local_env_path().write_text(f"REMO_BASH=/b/bash\nREMO_PROJECTS_DIR={folder}\n")
        local.setup()
        assert local.get_local_host().projects_dir == ""

    def test_a_vanished_persisted_folder_is_dropped_with_a_warning(self, play, tmp_path, capsys):
        folder = tmp_path / "proj"
        folder.mkdir()
        local.setup(projects_dir=str(folder))
        self._remove_local()
        folder.rmdir()
        capsys.readouterr()
        local.setup()
        assert local.get_local_host().projects_dir == ""
        captured = capsys.readouterr()
        assert "no longer exists" in captured.out + captured.err

    def test_no_env_file_or_no_key_means_no_folder(self, ready, home):
        assert local.persisted_projects_dir() == ""
        env = local.local_env_path()
        env.parent.mkdir(parents=True)
        env.write_text("REMO_BASH=/b/bash\n")  # written by a setup that predates the key
        assert local.persisted_projects_dir() == ""
        local.setup()
        assert local.get_local_host().projects_dir == ""


def test_setup_preserves_a_hand_edited_second_local_entry(ready, tmp_config_dir):
    """The first local entry is the local host; any extra one is kept as-is."""
    first = KnownHost("local", "local", "localhost", "paul", access_mode="direct")
    extra = KnownHost("local", "zz-extra", "localhost", "paul", access_mode="direct")
    seed_registry(tmp_config_dir, [first, extra])
    local.setup(name="work")
    names = sorted(h.name for h in get_known_hosts() if h.type == "local")
    assert names == ["work", "zz-extra"]


# --- spec 030: tool PATH, old-session note, host-info signature -----------------------


class TestToolPath:
    def test_dirs_of_detected_tools_deduplicated_in_order(self, ready, monkeypatch):
        found = {
            "zellij": "/opt/homebrew/bin/zellij",
            "fzf": "/opt/homebrew/bin/fzf",
            "git": "/usr/bin/git",
            "docker": "/Applications/Docker/bin/docker",
        }
        monkeypatch.setattr(local.shutil, "which", lambda t, *a, **k: found.get(t))
        monkeypatch.setattr(local.os.path, "isdir", lambda d: d == "/usr/local/bin")
        local.setup()
        value = _play_vars(ready.call_args.args[1])["remo_local_path"]
        assert value == (
            "/opt/homebrew/bin:/usr/bin:/Applications/Docker/bin:/b:/usr/local/bin"
        )

    def test_nothing_detected_is_empty(self, ready, monkeypatch):
        monkeypatch.setattr(local.shutil, "which", lambda *a, **k: None)
        monkeypatch.setattr(local.os.path, "isdir", lambda d: False)
        local.setup()
        assert _play_vars(ready.call_args.args[1])["remo_local_path"] == "/b"


class TestOldSessionNote:
    def _setup(self, ready, monkeypatch, system, result=None, exc=None):
        monkeypatch.setattr(local.platform, "system", lambda: system)
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"], seen["env"], seen["timeout"] = argv, kw.get("env"), kw.get("timeout")
            if exc:
                raise exc
            return result

        monkeypatch.setattr(local.subprocess, "run", fake_run)
        return seen

    def _res(self, rc=0, out=""):
        import subprocess

        return subprocess.CompletedProcess([], rc, out, "")

    def test_sessions_found_prints_the_note_without_the_pinned_dir(self, ready, monkeypatch, capsys):
        monkeypatch.setenv("ZELLIJ_SOCKET_DIR", "/pinned")
        seen = self._setup(ready, monkeypatch, "Darwin", self._res(out="a\nb\n"))
        local.setup()
        out = capsys.readouterr().out
        assert "2 sessions started before this upgrade are still running outside" in out
        assert "zellij attach NAME" in out
        assert seen["argv"] == ["zellij", "list-sessions", "--no-formatting"]
        assert "ZELLIJ_SOCKET_DIR" not in seen["env"] and seen["timeout"] == 5

    def test_singular(self, ready, monkeypatch, capsys):
        self._setup(ready, monkeypatch, "Darwin", self._res(out="only\n"))
        local.setup()
        assert "1 session started before this upgrade is still running" in capsys.readouterr().out

    def test_exited_sessions_are_not_counted(self, ready, monkeypatch, capsys):
        out = (
            "live [Created 1h ago]\n"
            "gone [Created 2d ago] (EXITED - attach to resurrect)\n"
        )
        self._setup(ready, monkeypatch, "Darwin", self._res(out=out))
        local.setup()
        assert "1 session started before this upgrade is still running" in capsys.readouterr().out

    def test_only_exited_sessions_is_silent(self, ready, monkeypatch, capsys):
        out = "gone [Created 2d ago] (EXITED - attach to resurrect)\n"
        self._setup(ready, monkeypatch, "Darwin", self._res(out=out))
        local.setup()
        assert "started before this upgrade" not in capsys.readouterr().out

    def test_none_is_silent(self, ready, monkeypatch, capsys):
        self._setup(ready, monkeypatch, "Darwin", self._res(out="\n"))
        local.setup()
        assert "started before this upgrade" not in capsys.readouterr().out

    @pytest.mark.parametrize("exc", [FileNotFoundError("zellij"), OSError("x")])
    def test_error_is_silent(self, ready, monkeypatch, capsys, exc):
        self._setup(ready, monkeypatch, "Darwin", exc=exc)
        local.setup()
        assert "started before this upgrade" not in capsys.readouterr().out

    def test_timeout_is_silent(self, ready, monkeypatch, capsys):
        import subprocess

        self._setup(ready, monkeypatch, "Darwin", exc=subprocess.TimeoutExpired("zellij", 5))
        local.setup()
        assert "started before this upgrade" not in capsys.readouterr().out

    def test_nonzero_is_silent(self, ready, monkeypatch, capsys):
        self._setup(ready, monkeypatch, "Darwin", self._res(rc=1, out="a\n"))
        local.setup()
        assert "started before this upgrade" not in capsys.readouterr().out

    def test_non_darwin_never_runs_zellij(self, ready, monkeypatch):
        seen = self._setup(ready, monkeypatch, "Linux", self._res(out="a\n"))
        local.setup()
        assert "argv" not in seen


def test_a_changed_host_info_makes_setup_report_a_change(ready, home):
    local.setup()

    def rewrite_host_info(*a, **k):
        info = home / ".local" / "state" / "remo" / "host-info"
        info.parent.mkdir(parents=True, exist_ok=True)
        info.write_text("host_kind=local\nmenu_command=1\ninstalled_by=NEW\n")
        return 0

    ready.side_effect = rewrite_host_info
    assert local.setup().changed is True
