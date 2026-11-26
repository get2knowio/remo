"""`remo resume` against the local host (spec 029): same decisions, no SSH."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from remo_cli.cli.resume import resume
from remo_cli.core import tab_records
from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity
from remo_cli.models.host import KnownHost
from tests.conftest import seed_registry

BASH = "/opt/homebrew/bin/bash"
LOCAL = KnownHost("local", "local", "localhost", "paul", access_mode="direct")


@pytest.fixture
def world(tmp_path, monkeypatch, tmp_config_dir, mocker):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    state = tmp_path / ".local" / "state" / "remo"
    state.mkdir(parents=True)
    (state / "local.env").write_text(f"REMO_BASH={BASH}\n")
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    (tmp_path / ".local" / "bin" / "project-menu").write_text("")
    seed_registry(tmp_config_dir, [LOCAL])
    key = derive_tab_key(detect_tab_identity({"KITTY_WINDOW_ID": "7"}), tab_records.get_secret())

    class World:
        pass

    w = World()
    w.key = key
    w.host_calls = []
    w.reply = {"protocol_version": 1, "key": key, "project": "a",
               "recorded_at": 1, "zellij_state": "active"}
    w.returncode = 0

    def fake_invoke(argv, *, timeout):
        w.host_calls.append((argv, timeout))
        return mocker.MagicMock(
            returncode=w.returncode, stdout=json.dumps(w.reply).encode(), stderr=b""
        )

    mocker.patch("remo_cli.core.remo_host_client._invoke", side_effect=fake_invoke)
    w.connect = mocker.patch("remo_cli.cli.shell.connect_to_host", return_value=None)
    return w


def test_lookup_runs_through_the_local_prefix_with_the_5s_budget(world):
    tab_records.record(world.key, "local", None)
    result = CliRunner().invoke(resume, [])
    assert result.exit_code == 0, result.output
    argv, timeout = world.host_calls[0]
    assert argv[:4] == [BASH, "-c", 'eval "$*"', "remo-local"]
    assert "ssh" not in argv
    assert argv[4:8] == [
        'PATH="$HOME/.local/bin:$PATH"', "remo-host", "sessions", "lookup",
    ]
    assert timeout == 5.0


def test_attaches_the_remembered_project(world):
    tab_records.record(world.key, "local", None)
    result = CliRunner().invoke(resume, [])
    assert result.exit_code == 0, result.output
    args, kwargs = world.connect.call_args
    assert args[0].type == "local"
    assert kwargs["project"] == "a"
    assert kwargs["tab_key"] == world.key
    assert "Resuming a on local" in result.output


def test_a_host_without_a_record_falls_back_to_the_menu(world):
    world.reply = {"protocol_version": 1, "key": world.key, "project": None,
                   "recorded_at": None, "zellij_state": None}
    tab_records.record(world.key, "local", None)
    world.connect.reset_mock()
    # Row 5-6: the host knows nothing, no remembered project, not live -> menu.
    result = CliRunner().invoke(resume, [])
    assert result.exit_code == 0, result.output
    assert world.connect.call_args.kwargs["project"] is None


def test_an_old_script_set_reads_as_not_upgraded_and_names_local_setup(world):
    world.returncode = 4  # unknown `sessions` subcommand on a pre-028 remo-host
    tab_records.record(world.key, "local", None)
    result = CliRunner().invoke(resume, [])
    assert result.exit_code == 0, result.output
    assert "remo local setup" in result.output
    assert world.connect.call_args.kwargs["project"] is None


def test_setup_not_completed_falls_back_instead_of_a_traceback(world, tmp_path):
    # The access layer refuses before any lookup runs; resume degrades to the
    # menu fallback and the connection reports the actionable message.
    (tmp_path / ".local" / "state" / "remo" / "local.env").unlink()
    tab_records.record(world.key, "local", "a")
    result = CliRunner().invoke(resume, [])
    assert result.exception is None, repr(result.exception)
    assert world.host_calls == []
    assert world.connect.call_args.kwargs.get("project") is None
