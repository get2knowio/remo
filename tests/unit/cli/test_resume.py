"""`remo resume` CLI wiring (spec 028, contracts/cli-resume.md)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.resume import resume
from remo_cli.core import tab_records
from remo_cli.core.remo_host_client import TabLookup, ZellijState
from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity
from remo_cli.models.host import KnownHost


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def host_h():
    return KnownHost(type="hetzner", name="H", host="1.1.1.1", user="remo")


@pytest.fixture
def host_i():
    return KnownHost(type="ssh", name="I", host="2.2.2.2", user="remo")


@pytest.fixture
def world(monkeypatch, tmp_path, mocker, host_h, host_i):
    """A tab (kitty window 7), a tmp REMO_HOME, hosts H and I registered, and
    every network-touching seam mocked."""
    monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    key = derive_tab_key(
        detect_tab_identity({"KITTY_WINDOW_ID": "7"}), tab_records.get_secret()
    )

    class World:
        pass

    w = World()
    w.key = key
    w.hosts = [host_h, host_i]
    w.connect = mocker.patch("remo_cli.cli.shell.connect_to_host")
    w.autostart = mocker.patch(
        "remo_cli.cli.shell.auto_start_host", side_effect=lambda h: h
    )
    w.get_hosts = mocker.patch(
        "remo_cli.core.known_hosts.get_known_hosts", side_effect=lambda *a, **k: list(w.hosts)
    )
    w.lookup = mocker.patch(
        "remo_cli.core.resume.run_lookup",
        return_value=(TabLookup("A", 1, ZellijState.ACTIVE), "ok"),
    )
    w.live = mocker.patch("remo_cli.core.resume.is_project_live", return_value=True)
    w.resolve = mocker.patch("remo_cli.core.ssh.resolve_remo_host", side_effect=lambda n=None: host_h)
    w.picker = mocker.patch("remo_cli.core.ssh.pick_environment", return_value=host_h)
    return w


def lines(result) -> list[str]:
    return [ln for ln in result.output.splitlines() if ln.strip()]


class TestResumeSuccess:
    def test_stopped_instance_is_started_before_the_lookup(self, runner, world, host_h):
        """FR-014: the lookup must reach the started instance at its new
        address, and connect_to_host must not query the state a second time."""
        started = KnownHost(type="hetzner", name="H", host="9.9.9.9", user="remo")
        order: list[str] = []
        world.autostart.side_effect = lambda h: order.append("start") or started
        world.lookup.side_effect = lambda h, k: (
            order.append(f"lookup:{h.host}") or (TabLookup("A", 1, ZellijState.ACTIVE), "ok")
        )
        tab_records.record(world.key, "H", None)
        result = runner.invoke(resume, [])
        assert result.exit_code == 0, result.output
        assert order == ["start", "lookup:9.9.9.9"]
        args, kwargs = world.connect.call_args
        assert args[0] is started
        assert kwargs["auto_started"] is True
        assert kwargs["project"] == "A"

    def test_shell_fallback_leaves_auto_start_to_connect(self, runner, world):
        result = runner.invoke(resume, [])  # no record -> remo shell
        assert result.exit_code == 0, result.output
        world.autostart.assert_not_called()
        assert world.connect.call_args.kwargs.get("auto_started", False) is False

    def test_attaches_without_picker(self, runner, world, host_h):
        tab_records.record(world.key, "H", None)
        result = runner.invoke(resume, [])
        assert result.exit_code == 0, result.output
        assert lines(result) == ["Resuming A on H"] or "Resuming A on H" in result.output
        assert result.output.count("\n") <= 2
        world.connect.assert_called_once()
        args, kwargs = world.connect.call_args
        assert args == (host_h,)
        assert kwargs["project"] == "A"
        assert kwargs["tab_key"] == world.key
        assert kwargs["tunnels"] == ()
        world.picker.assert_not_called()
        world.resolve.assert_not_called()

    def test_flags_pass_through(self, runner, world):
        tab_records.record(world.key, "H", None)
        runner.invoke(resume, ["-L", "8080:80", "--no-open", "--no-update-check"])
        _, kwargs = world.connect.call_args
        assert kwargs["tunnels"] == ("8080:80",)
        assert kwargs["no_open"] is True
        assert kwargs["no_update_check"] is True

    def test_fast_path_does_not_list_sessions(self, runner, world):
        """SC-004: the lookup is the only extra round-trip."""
        tab_records.record(world.key, "H", "recorded-project")
        runner.invoke(resume, [])
        world.live.assert_not_called()
        assert world.connect.call_args.kwargs["project"] == "A"

    def test_matching_name_resumes(self, runner, world):
        tab_records.record(world.key, "H", None)
        runner.invoke(resume, ["H"])
        assert world.connect.call_args.kwargs["project"] == "A"


class TestNameResolution:
    def test_host_scoped_short_name_is_the_recorded_host(self, runner, world):
        """`remo shell dev` reaches "node/dev", so `remo resume dev` must resume it."""
        scoped = KnownHost(type="incus", name="node/dev", host="3.3.3.3", user="remo")
        world.hosts.append(scoped)
        tab_records.record(world.key, "node/dev", None)
        result = runner.invoke(resume, ["dev"])
        assert result.exit_code == 0, result.output
        assert "Resuming A on node/dev" in result.output
        assert world.connect.call_args.args == (scoped,)
        assert world.connect.call_args.kwargs["project"] == "A"
        world.resolve.assert_not_called()

    def test_short_name_of_another_host_is_still_a_mismatch(self, runner, world):
        world.hosts.append(KnownHost(type="incus", name="node/dev", host="3.3.3.3", user="remo"))
        tab_records.record(world.key, "H", None)
        result = runner.invoke(resume, ["dev"])
        assert "This tab last used H, not node/dev — opening remo shell node/dev" in result.output
        world.resolve.assert_called_once_with("node/dev")
        world.lookup.assert_not_called()


class TestRecordRefresh:
    def test_attach_refreshes_record_with_attached_project(self, runner, world):
        tab_records.record(world.key, "H", "old")
        before = tab_records.load(world.key)
        runner.invoke(resume, [])
        after = tab_records.load(world.key)
        assert after is not None and before is not None
        assert (after.host, after.project) == ("H", "A")
        assert after.recorded_at >= before.recorded_at

    def test_menu_refresh_keeps_remembered_project(self, runner, world):
        tab_records.record(world.key, "H", "B")
        world.lookup.return_value = (None, "unsupported")
        world.live.return_value = False
        runner.invoke(resume, [])
        after = tab_records.load(world.key)
        assert after is not None and (after.host, after.project) == ("H", "B")
        assert world.connect.call_args.kwargs.get("project") is None

    def test_refresh_failure_is_silent_and_still_connects(self, runner, world, mocker):
        tab_records.record(world.key, "H", None)
        mocker.patch(
            "remo_cli.core.tab_records.record",
            side_effect=tab_records.TabRecordError("disk on fire"),
        )
        result = runner.invoke(resume, [])
        assert result.exit_code == 0, result.output
        assert "disk on fire" not in result.output
        assert len(lines(result)) == 1
        assert world.connect.call_args.kwargs["project"] == "A"


class TestFallbacks:
    def test_no_tab_variable(self, runner, world, monkeypatch, host_h):
        monkeypatch.delenv("KITTY_WINDOW_ID")
        result = runner.invoke(resume, [])
        assert result.exit_code == 0
        assert "This terminal exposes no tab identity — opening remo shell" in result.output
        world.resolve.assert_called_once_with(None)
        _, kwargs = world.connect.call_args
        assert kwargs.get("project") is None
        assert kwargs["tab_key"] is None
        world.lookup.assert_not_called()

    def test_no_tab_variable_several_hosts_goes_through_picker(self, runner, mocker, world, monkeypatch, host_h):
        monkeypatch.delenv("KITTY_WINDOW_ID")
        world.resolve.side_effect = None
        # real resolve_remo_host with several registered hosts -> picker
        mocker.stopall()
        mocker.patch("remo_cli.cli.shell.connect_to_host")
        mocker.patch("remo_cli.core.ssh.get_known_hosts", return_value=world.hosts)
        picker = mocker.patch("remo_cli.core.ssh.pick_environment", return_value=host_h)
        result = runner.invoke(resume, [])
        assert result.exit_code == 0, result.output
        picker.assert_called_once()

    def test_no_record(self, runner, world):
        result = runner.invoke(resume, [])
        assert "Nothing recorded for this tab — opening remo shell" in result.output
        world.resolve.assert_called_once_with(None)
        world.lookup.assert_not_called()
        # the shell path records the new tab
        assert tab_records.load(world.key) is not None

    def test_recorded_host_removed_does_not_exit(self, runner, world):
        tab_records.record(world.key, "GONE", "A")
        result = runner.invoke(resume, [])
        assert result.exit_code == 0, result.output
        assert "GONE is no longer registered — opening remo shell" in result.output
        world.resolve.assert_called_once_with(None)
        world.lookup.assert_not_called()

    def test_other_name_is_remo_shell_other(self, runner, world):
        tab_records.record(world.key, "H", None)
        result = runner.invoke(resume, ["OTHER"])
        assert "This tab last used H, not OTHER — opening remo shell OTHER" in result.output
        world.resolve.assert_called_once_with("OTHER")
        world.lookup.assert_not_called()

    def test_lookup_unsupported_added_host_names_configure(self, runner, world, host_i):
        tab_records.record(world.key, "I", None)
        world.lookup.return_value = (None, "unsupported")
        result = runner.invoke(resume, [])
        assert "I can't resume a tab's project yet — run 'remo configure I'" in result.output
        args, kwargs = world.connect.call_args
        assert args == (host_i,)
        assert kwargs.get("project") is None
        assert kwargs["tab_key"] == world.key

    def test_lookup_unsupported_provider_host_names_upgrade(self, runner, world):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (None, "unsupported")
        result = runner.invoke(resume, [])
        assert "run 'remo hetzner upgrade H'" in result.output

    def test_lookup_failed_opens_menu(self, runner, world):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (None, "failed")
        result = runner.invoke(resume, [])
        assert "Couldn't ask H which project this tab used — opening its project menu" in result.output
        assert world.connect.call_args.kwargs.get("project") is None

    def test_host_session_not_live(self, runner, world):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (TabLookup("A", 1, ZellijState.EXITED), "ok")
        result = runner.invoke(resume, [])
        assert "A is no longer running on H — opening its project menu" in result.output
        assert world.connect.call_args.kwargs.get("project") is None
        world.live.assert_not_called()

    def test_host_has_no_entry_for_tab(self, runner, world):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (TabLookup(None, None, None), "ok")
        result = runner.invoke(resume, [])
        assert "Nothing recorded for this tab on H — opening its project menu" in result.output
        assert world.connect.call_args.kwargs.get("project") is None

    def test_every_path_prints_exactly_one_line(self, runner, world, monkeypatch):
        scenarios = []
        # (record host/project, lookup result, argv)
        scenarios.append((None, None, []))
        scenarios.append((("H", None), (TabLookup("A", 1, ZellijState.ACTIVE), "ok"), []))
        scenarios.append((("H", None), (TabLookup("A", 1, ZellijState.ABSENT), "ok"), []))
        scenarios.append((("H", None), (None, "unsupported"), []))
        scenarios.append((("H", None), (None, "failed"), []))
        scenarios.append((("H", None), (TabLookup(None, None, None), "ok"), []))
        scenarios.append((("GONE", None), None, []))
        scenarios.append((("H", None), None, ["OTHER"]))
        for rec, lookup, argv in scenarios:
            tab_records.forget_all()
            world.key = derive_tab_key(
                detect_tab_identity({"KITTY_WINDOW_ID": "7"}), tab_records.get_secret()
            )
            if rec:
                tab_records.record(world.key, *rec)
            if lookup:
                world.lookup.return_value = lookup
            result = runner.invoke(resume, argv)
            assert result.exit_code == 0, (rec, lookup, result.output)
            assert len(lines(result)) == 1, (rec, lookup, result.output)

    def test_unreadable_store_warns_once_and_opens_shell(self, runner, world, mocker):
        mocker.patch(
            "remo_cli.core.tab_records.get_secret",
            side_effect=tab_records.TabRecordError("disk on fire"),
        )
        result = runner.invoke(resume, [])
        assert result.exit_code == 0
        assert len(lines(result)) == 1
        assert "disk on fire" in result.output
        world.resolve.assert_called_once_with(None)
        assert world.connect.call_args.kwargs["tab_key"] is None


class TestOldHostWorkstationProject:
    """US3: only the workstation is upgraded."""

    def test_several_hosts_picker_never_invoked(self, runner, world, host_h):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (None, "unsupported")
        runner.invoke(resume, [])
        world.picker.assert_not_called()
        world.resolve.assert_not_called()
        assert world.connect.call_args.args == (host_h,)

    def test_record_project_active_attaches(self, runner, world):
        tab_records.record(world.key, "H", "A")
        world.lookup.return_value = (None, "unsupported")
        world.live.return_value = True
        result = runner.invoke(resume, [])
        assert "Resuming A on H" in result.output
        assert world.connect.call_args.kwargs["project"] == "A"
        world.live.assert_called_once()
        assert world.live.call_args.args[1] == "A"

    def test_record_project_exited_goes_to_menu_never_attaches(self, runner, world):
        tab_records.record(world.key, "H", "A")
        world.lookup.return_value = (None, "unsupported")
        world.live.return_value = False
        result = runner.invoke(resume, [])
        assert "A is no longer running on H — opening its project menu" in result.output
        assert world.connect.call_args.kwargs.get("project") is None

    def test_lookup_ok_without_entry_still_uses_record_project(self, runner, world):
        tab_records.record(world.key, "H", "A")
        world.lookup.return_value = (TabLookup(None, None, None), "ok")
        runner.invoke(resume, [])
        assert world.connect.call_args.kwargs["project"] == "A"

    def test_explicit_matching_name_behaves_like_plain_resume(self, runner, world):
        tab_records.record(world.key, "H", None)
        world.lookup.return_value = (None, "unsupported")
        result = runner.invoke(resume, ["H"])
        assert "can't resume a tab's project yet" in result.output
        world.picker.assert_not_called()


class TestForget:
    def test_forget_removes_only_this_tab(self, runner, world):
        other = "f" * 32
        tab_records.record(world.key, "H", "A")
        tab_records.record(other, "H", "B")
        result = runner.invoke(resume, ["--forget"])
        assert result.exit_code == 0
        assert len(lines(result)) == 1
        assert tab_records.load(world.key) is None
        assert tab_records.load(other) is not None
        world.connect.assert_not_called()
        world.lookup.assert_not_called()

    def test_forget_nothing_recorded_is_noop_line(self, runner, world):
        result = runner.invoke(resume, ["--forget"])
        assert result.exit_code == 0
        assert "Nothing recorded for this tab" in result.output
        world.connect.assert_not_called()

    def test_forget_without_identity(self, runner, world, monkeypatch):
        monkeypatch.delenv("KITTY_WINDOW_ID")
        result = runner.invoke(resume, ["--forget"])
        assert result.exit_code == 0
        assert "no tab identity" in result.output
        world.connect.assert_not_called()

    def test_forget_all_clears_and_rotates_secret(self, runner, world, tmp_path):
        tab_records.record(world.key, "H", "A")
        before = (tmp_path / "remo" / "tab-secret").read_text()
        result = runner.invoke(resume, ["--forget-all"])
        assert result.exit_code == 0
        assert tab_records.load(world.key) is None
        assert (tmp_path / "remo" / "tab-secret").read_text() != before
        world.connect.assert_not_called()

    @pytest.mark.parametrize(
        "argv",
        [
            ["--forget", "--forget-all"],
            ["--forget", "H"],
            ["--forget", "-L", "8080"],
            ["--forget-all", "H"],
            ["--forget-all", "-L", "8080"],
        ],
    )
    def test_combinations_are_usage_errors(self, runner, world, argv):
        result = runner.invoke(resume, argv)
        assert result.exit_code == 2
        world.connect.assert_not_called()

    def test_store_failure_is_one_error_line_exit_1(self, runner, world, mocker):
        mocker.patch(
            "remo_cli.core.tab_records.forget",
            side_effect=tab_records.TabRecordError("read-only"),
        )
        result = runner.invoke(resume, ["--forget"])
        assert result.exit_code == 1
        assert "read-only" in result.output

        mocker.patch(
            "remo_cli.core.tab_records.forget_all",
            side_effect=tab_records.TabRecordError("read-only"),
        )
        result = runner.invoke(resume, ["--forget-all"])
        assert result.exit_code == 1


def test_help_mentions_terminals_shell_and_upgrade(runner):
    result = runner.invoke(resume, ["--help"])
    assert result.exit_code == 0
    assert "TERM_SESSION_ID" in result.output
    assert "remo shell" in result.output
    assert "upgrade" in result.output
