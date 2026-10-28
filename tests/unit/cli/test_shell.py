"""Unit tests for remo.cli.shell module."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.shell import shell
from remo_cli.models.host import KnownHost


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def hetzner_host():
    return KnownHost(
        type="hetzner",
        name="webserver",
        host="5.6.7.8",
        user="remo",
    )


@pytest.fixture
def _patch_shell_deps(mocker, hetzner_host):
    """Patch all common dependencies for shell command tests."""
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=hetzner_host)
    mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", return_value=hetzner_host)
    mocker.patch("remo_cli.core.ssh.shell_connect")


class TestShellVersionCheck:
    """Tests for the pre-shell version check behavior."""

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_no_update_check_skips_version_check(self, runner, mocker):
        """--no-update-check skips the remote version check entirely."""
        mock_check = mocker.patch("remo_cli.core.ssh.check_remote_version")
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")

        result = runner.invoke(shell, ["--no-update-check"])

        assert result.exit_code == 0
        mock_check.assert_not_called()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_equal_versions_proceeds_silently(self, runner, mocker):
        """When remote and local versions match, no prompt is shown."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_behind_prompts_update(self, runner, mocker):
        """When remote is behind local, user is prompted to update, naming
        the exact `remo <type> upgrade <name>` command that will run (SC-003)."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_called_once()
        prompt = mock_confirm.call_args[0][0]
        assert "v0.8.0" in prompt
        assert "v0.9.0" in prompt
        assert "remo hetzner upgrade webserver" in prompt

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_behind_update_accepted(self, runner, mocker):
        """When user accepts update, provider update_entry is called."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mocker.patch("remo_cli.core.output.confirm", return_value=True)
        mock_update_entry = mocker.patch("remo_cli.providers.hetzner.update_entry")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_update_entry.assert_called_once()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_update_failure_prompts_before_connect(self, runner, mocker):
        """When tools update fails, user is prompted to confirm connect."""
        from remo_cli.core.errors import OperationFailedError

        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        # First confirm() = "Update?" → True; second = "Connect anyway?" → True
        mock_confirm = mocker.patch(
            "remo_cli.core.output.confirm", side_effect=[True, True]
        )
        mocker.patch(
            "remo_cli.providers.hetzner.update_entry",
            side_effect=OperationFailedError("playbook rc=2"),
        )
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        assert mock_confirm.call_count == 2
        assert "Connect anyway?" in mock_confirm.call_args_list[1][0][0]
        mock_shell_connect.assert_called_once()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_update_failure_decline_aborts(self, runner, mocker):
        """When user declines after failed update, shell_connect is not called."""
        from remo_cli.core.errors import OperationFailedError

        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        # First confirm() = "Update?" → True; second = "Connect anyway?" → False
        mocker.patch("remo_cli.core.output.confirm", side_effect=[True, False])
        mocker.patch(
            "remo_cli.providers.hetzner.update_entry",
            side_effect=OperationFailedError("playbook rc=2"),
        )
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 1
        mock_shell_connect.assert_not_called()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_ahead_shows_warning(self, runner, mocker):
        """When remote is ahead of local, a warning is shown."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.9.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        assert "newer tools" in result.output
        assert "uv tool upgrade remo-cli" in result.output

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_no_marker_prompts_update(self, runner, mocker):
        """When remote has no version marker, user is prompted to update,
        naming the exact `remo <type> upgrade <name>` command (SC-003)."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=(None, None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_called_once()
        prompt = mock_confirm.call_args[0][0]
        assert "no version info" in prompt
        assert "remo hetzner upgrade webserver" in prompt

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_prompt_names_host_scoped_upgrade_command(self, runner, mocker):
        """A HOST_SCOPED instance (incus/proxmox) gets `--host <host>` appended,
        with the container's short name (not `host/container`) as NAME."""
        incus_host = KnownHost(
            type="incus", name="lab1/dev1", host="192.168.1.50", user="remo"
        )
        mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=incus_host)
        mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", return_value=incus_host)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        prompt = mock_confirm.call_args[0][0]
        assert "remo incus upgrade dev1 --host lab1" in prompt

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_prompt_names_host_user_when_registry_carries_one(self, runner, mocker):
        """Passing `--host` short-circuits the registry lookup inside
        `upgrade`, so the hint must also name the host-user flag whenever the
        entry carries one -- otherwise the printed command silently falls back
        to the provider default and is not what accepting the prompt runs."""
        incus_host = KnownHost(
            type="incus",
            name="lab1/dev1",
            host="192.168.1.50",
            user="remo",
            instance_id="paul",  # incus stores the Incus-host SSH user here
        )
        mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=incus_host)
        mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", return_value=incus_host)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        prompt = mock_confirm.call_args[0][0]
        assert "remo incus upgrade dev1 --host lab1 --host-user paul" in prompt

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_ssh_error_skips_update_prompt(self, runner, mocker):
        """When SSH itself fails, the user is warned and not prompted to update."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch(
            "remo_cli.core.ssh.check_remote_version",
            return_value=(None, "Host key verification failed."),
        )
        mock_confirm = mocker.patch("remo_cli.core.output.confirm")
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()
        assert "Could not check tools version" in result.output
        assert "Host key verification failed." in result.output
        mock_shell_connect.assert_called_once()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_unknown_local_version_skips_check(self, runner, mocker):
        """When local version is unknown, skip the version check."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_check = mocker.patch("remo_cli.core.ssh.check_remote_version")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_check.assert_not_called()


class TestShellAutoStartAwsFailure:
    """auto_start_aws_if_stopped raising ProviderError (018) exits with its
    exit_code and prints the message, instead of an uncaught SystemExit."""

    def test_precondition_error_exits_with_message(self, runner, mocker, hetzner_host):
        from remo_cli.core.errors import PreconditionError

        mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=hetzner_host)
        mocker.patch(
            "remo_cli.providers.aws.auto_start_aws_if_stopped",
            side_effect=PreconditionError("Instance i-123 is currently stopping."),
        )
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 1
        assert "Instance i-123 is currently stopping." in result.output
        mock_shell_connect.assert_not_called()

    def test_auto_started_caller_skips_the_second_state_query(self, mocker, hetzner_host):
        """`remo resume` starts the instance before its lookup and passes
        auto_started=True; connect_to_host must not query EC2 again."""
        from remo_cli.cli.shell import connect_to_host

        mock_start = mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")
        connect_to_host(
            hetzner_host, tunnels=(), no_open=True, no_update_check=True, auto_started=True
        )
        mock_start.assert_not_called()
        assert mock_sc.call_args.args[0] is hetzner_host


class TestShellProjectLaunchFlags:
    """Tests for the -p / --exec / --detach passthrough flags."""

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_project_flag_forwards_to_shell_connect(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["-p", "my-app"])

        assert result.exit_code == 0
        _, kwargs = mock_sc.call_args
        assert kwargs["project"] == "my-app"
        assert kwargs["detach"] is False
        assert kwargs["exec_cmd"] is None

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_exec_passthrough(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(
            shell, ["-p", "my-app", "--exec", "claude --remote-control"]
        )

        assert result.exit_code == 0
        _, kwargs = mock_sc.call_args
        assert kwargs["project"] == "my-app"
        assert kwargs["exec_cmd"] == "claude --remote-control"

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_detach_with_exec(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(
            shell,
            [
                "-p",
                "my-app",
                "--detach",
                "--exec",
                "claude remote-control --name remo-rc",
            ],
        )

        assert result.exit_code == 0
        _, kwargs = mock_sc.call_args
        assert kwargs["detach"] is True
        assert kwargs["exec_cmd"] == "claude remote-control --name remo-rc"

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_detach_without_exec_errors(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["-p", "my-app", "--detach"])

        assert result.exit_code == 2
        mock_sc.assert_not_called()
        assert "--detach requires --exec" in result.output

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_detach_with_tunnels_errors(self, runner, mocker):
        # -L port forwarding is useless with --detach because the SSH session
        # exits immediately; surface that as an error rather than silently
        # forwarding to a tunnel that dies before the user can use it.
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(
            shell, ["-p", "my-app", "-L", "8080", "--detach", "--exec", "true"]
        )

        assert result.exit_code == 2
        mock_sc.assert_not_called()
        assert "-L port forwarding cannot be combined with --detach" in result.output

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_exec_without_project_errors(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["--exec", "pytest"])

        assert result.exit_code == 2
        mock_sc.assert_not_called()
        assert "-p/--project" in result.output

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_no_new_flags_preserves_legacy_call(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        _, kwargs = mock_sc.call_args
        assert kwargs["project"] is None
        assert kwargs["detach"] is False
        assert kwargs["exec_cmd"] is None


class TestBuildProjectLaunchRemoteCmd:
    """Tests for the SSH remote-command string builder."""

    def test_project_only(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        assert (
            build_project_launch_remote_cmd("my-app", detach=False, exec_cmd=None)
            == "~/.local/bin/project-launch --project my-app"
        )

    def test_project_with_exec(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        # --exec value is forwarded as ONE shell-quoted arg so the remote
        # `project-launch` script can pass it intact to `bash -lc`.
        assert (
            build_project_launch_remote_cmd(
                "my-app", detach=False, exec_cmd="claude --remote-control"
            )
            == "~/.local/bin/project-launch --project my-app "
            "--exec 'claude --remote-control'"
        )

    def test_project_detach_with_exec(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        assert (
            build_project_launch_remote_cmd(
                "my-app",
                detach=True,
                exec_cmd="claude remote-control --name remo-rc",
            )
            == "~/.local/bin/project-launch --project my-app --detach "
            "--exec 'claude remote-control --name remo-rc'"
        )

    def test_exec_preserves_shell_operators_and_vars(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        # Vars and operators stay literal in the outgoing command — they get
        # interpreted by `bash -lc` on the remote, not by the local builder.
        out = build_project_launch_remote_cmd(
            "my-app",
            detach=False,
            exec_cmd='echo $REMO_PROJECT && pwd',
        )
        # Single-quoted by shlex.quote, so $ and && survive unmangled.
        assert "'echo $REMO_PROJECT && pwd'" in out

    def test_project_with_special_chars_is_quoted(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        out = build_project_launch_remote_cmd(
            "weird name", detach=False, exec_cmd=None
        )
        assert "'weird name'" in out

    def test_exec_empty_string_is_ignored(self):
        from remo_cli.core.ssh import build_project_launch_remote_cmd

        # `--exec ""` shouldn't append `--` with no args (would be an error
        # on the server). It collapses to project-only.
        assert (
            build_project_launch_remote_cmd("my-app", detach=False, exec_cmd="")
            == "~/.local/bin/project-launch --project my-app"
        )


class TestRunProviderUpgrade:
    """Tests for _run_tools_upgrade(): registry-dispatched via
    provider_registry + the Protocol's update_entry(entry) verb (018), plus the
    added-host path onto providers.added.configure() (#178)."""

    def test_aws_update(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade

        host = KnownHost(type="aws", name="devbox", host="1.2.3.4", user="remo")
        mock_update_entry = mocker.patch("remo_cli.providers.aws.update_entry")

        _run_tools_upgrade(host)

        mock_update_entry.assert_called_once_with(host)

    def test_hetzner_update(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade

        host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="remo")
        mock_update_entry = mocker.patch("remo_cli.providers.hetzner.update_entry")

        _run_tools_upgrade(host)

        mock_update_entry.assert_called_once_with(host)

    def test_incus_update(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade

        host = KnownHost(type="incus", name="myhost/devcontainer", host="192.168.1.50", user="remo")
        mock_update_entry = mocker.patch("remo_cli.providers.incus.update_entry")

        _run_tools_upgrade(host)

        mock_update_entry.assert_called_once_with(host)

    def test_proxmox_update(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade

        host = KnownHost(
            type="proxmox",
            name="lab1/dev1",
            host="192.168.1.46",
            user="remo",
            instance_id="100",
            access_mode="direct",
            region="root",
        )
        mock_update_entry = mocker.patch("remo_cli.providers.proxmox.update_entry")

        _run_tools_upgrade(host)

        mock_update_entry.assert_called_once_with(host)

    def test_unknown_type_raises_precondition_error_naming_the_type(self):
        from remo_cli.cli.shell import _run_tools_upgrade
        from remo_cli.core.errors import PreconditionError

        host = KnownHost(type="totally-unknown", name="foo", host="1.2.3.4", user="remo")

        with pytest.raises(PreconditionError, match="totally-unknown"):
            _run_tools_upgrade(host)

    def test_added_ssh_host_dispatches_to_configure(self, mocker):
        """An added host has no provider; `remo configure` is its upgrade verb.

        Pinned because get_provider("ssh") would raise: the dispatch has to
        branch before the registry lookup, not after it.
        """
        from remo_cli.cli.shell import _run_tools_upgrade

        host = KnownHost(type="ssh", name="mybox", host="1.2.3.4", user="remo")
        configure = mocker.patch("remo_cli.providers.added.configure")

        _run_tools_upgrade(host)

        configure.assert_called_once_with(name="mybox", assume_yes=True)

    def test_added_ssh_host_configure_failure_propagates(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade
        from remo_cli.core.errors import OperationFailedError

        host = KnownHost(type="ssh", name="mybox", host="1.2.3.4", user="remo")
        mocker.patch(
            "remo_cli.providers.added.configure",
            side_effect=OperationFailedError("playbook rc=2"),
        )

        with pytest.raises(OperationFailedError, match="rc=2"):
            _run_tools_upgrade(host)

    def test_update_entry_failure_propagates_as_provider_error(self, mocker):
        from remo_cli.cli.shell import _run_tools_upgrade
        from remo_cli.core.errors import OperationFailedError

        host = KnownHost(type="aws", name="devbox", host="1.2.3.4", user="remo")
        mocker.patch(
            "remo_cli.providers.aws.update_entry",
            side_effect=OperationFailedError("boom"),
        )

        with pytest.raises(OperationFailedError):
            _run_tools_upgrade(host)


class TestShellFlowCharacterization:
    """Spec 028 T002: pin the whole `remo shell` flow before it is extracted
    into connect_to_host(), so the refactor cannot change behaviour."""

    @pytest.fixture
    def calls(self, mocker, hetzner_host):
        order: list[str] = []
        started_host = KnownHost(type="hetzner", name="webserver", host="9.9.9.9", user="remo")

        def _resolve(name):
            order.append(f"resolve:{name}")
            return hetzner_host

        def _autostart(host):
            order.append("autostart")
            return started_host

        def _check(host):
            order.append("version-check")
            return ("0.8.0", None)

        def _connect(*args, **kwargs):
            order.append("connect")

        mocker.patch("remo_cli.core.ssh.resolve_remo_host", side_effect=_resolve)
        mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", side_effect=_autostart)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", side_effect=_check)
        connect = mocker.patch("remo_cli.core.ssh.shell_connect", side_effect=_connect)
        return order, connect, started_host

    def test_order_and_plain_call(self, runner, calls):
        order, connect, started = calls
        result = runner.invoke(shell, ["webserver"])
        assert result.exit_code == 0
        assert order == ["resolve:webserver", "autostart", "version-check", "connect"]
        # The host handed to shell_connect is the one auto-start returned.
        connect.assert_called_once()
        args, kwargs = connect.call_args
        assert args[0] is started
        assert args[1:] == ([], False)
        assert (kwargs["project"], kwargs["detach"], kwargs["exec_cmd"]) == (None, False, None)

    def test_no_update_check_skips_only_the_check(self, runner, calls):
        order, connect, _ = calls
        result = runner.invoke(shell, ["--no-update-check"])
        assert result.exit_code == 0
        assert order == ["resolve:None", "autostart", "connect"]

    def test_project_call(self, runner, calls):
        _, connect, _ = calls
        runner.invoke(shell, ["-p", "A"])
        args, kwargs = connect.call_args
        assert args[1:] == ([], False)
        assert (kwargs["project"], kwargs["detach"], kwargs["exec_cmd"]) == ("A", False, None)

    def test_project_exec_detach_call(self, runner, calls):
        _, connect, _ = calls
        runner.invoke(shell, ["-p", "A", "--exec", "x", "--detach"])
        args, kwargs = connect.call_args
        assert args[1:] == ([], False)
        assert (kwargs["project"], kwargs["detach"], kwargs["exec_cmd"]) == ("A", True, "x")

    def test_tunnels_and_no_open_call(self, runner, calls):
        _, connect, _ = calls
        runner.invoke(shell, ["-L", "8080:80", "-L", "3000", "--no-open"])
        args, _ = connect.call_args
        assert args[1:] == (["8080:80", "3000"], True)


class TestShellTabRecording:
    """Spec 028 T018: `remo shell` records the tab and forwards its key, but
    never resumes by itself (FR-012a)."""

    @pytest.fixture
    def tab(self, monkeypatch, tmp_path, mocker, hetzner_host):
        from remo_cli.core import tab_records
        from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity

        monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
        monkeypatch.setenv("KITTY_WINDOW_ID", "7")
        mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=hetzner_host)
        mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", return_value=hetzner_host)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")

        def _attempt(*args, on_connect=None, **kwargs):
            # The real shell_connect calls on_connect just before ssh runs.
            if on_connect is not None:
                on_connect()

        connect = mocker.patch("remo_cli.core.ssh.shell_connect", side_effect=_attempt)
        identity = detect_tab_identity({"KITTY_WINDOW_ID": "7"})
        key = derive_tab_key(identity, tab_records.get_secret())
        return key, connect

    def test_records_host_without_project(self, runner, tab):
        from remo_cli.core import tab_records

        key, connect = tab
        assert runner.invoke(shell, []).exit_code == 0
        rec = tab_records.load(key)
        assert rec is not None and (rec.host, rec.project) == ("webserver", None)
        assert connect.call_args.kwargs["tab_key"] == key

    def test_records_project(self, runner, tab):
        from remo_cli.core import tab_records

        key, connect = tab
        runner.invoke(shell, ["-p", "A"])
        rec = tab_records.load(key)
        assert rec is not None and rec.project == "A"
        assert connect.call_args.kwargs["tab_key"] == key

    def test_detach_records_nothing(self, runner, tab):
        from remo_cli.core import tab_records

        key, connect = tab
        runner.invoke(shell, ["-p", "A", "--exec", "x", "--detach"])
        assert tab_records.load(key) is None
        assert connect.call_args.kwargs["tab_key"] is None

    def test_no_tab_variable_records_nothing(self, runner, tab, monkeypatch, tmp_path):
        key, connect = tab
        monkeypatch.delenv("KITTY_WINDOW_ID")
        runner.invoke(shell, [])
        assert connect.call_args.kwargs["tab_key"] is None
        assert not (tmp_path / "remo" / "tab-records.json").exists()

    def test_store_failure_warns_once_and_still_connects(self, runner, tab, mocker):
        from remo_cli.core.tab_records import TabRecordError

        key, connect = tab
        mocker.patch("remo_cli.core.tab_records.record", side_effect=TabRecordError("disk full"))
        result = runner.invoke(shell, [])
        assert result.exit_code == 0
        assert result.output.count("Could not remember this tab") == 1
        assert connect.call_args.kwargs["tab_key"] == key

    def test_secret_failure_degrades_to_no_key(self, runner, tab, mocker):
        from remo_cli.core.tab_records import TabRecordError

        _, connect = tab
        mocker.patch("remo_cli.core.tab_records.get_secret", side_effect=TabRecordError("nope"))
        result = runner.invoke(shell, [])
        assert result.exit_code == 0
        assert "Could not remember this tab" in result.output
        assert connect.call_args.kwargs["tab_key"] is None

    def test_shell_never_looks_up_or_resumes(self, runner, tab, mocker):
        """FR-012a: even with a live record, `remo shell` is exactly what it
        was apart from the forwarded key."""
        from remo_cli.core import tab_records

        key, connect = tab
        tab_records.record(key, "webserver", "A")
        import remo_cli.core.resume as resume

        run_lookup = mocker.patch.object(resume, "run_lookup")
        decide = mocker.patch.object(resume, "decide_resume")
        live = mocker.patch.object(resume, "is_project_live")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        run_lookup.assert_not_called()
        decide.assert_not_called()
        live.assert_not_called()
        assert connect.call_args.kwargs["project"] is None


class TestTabRecordedOnlyOnAttempt:
    """Issue #248: the tab's record is written only once ssh is actually about
    to run. Every pre-connect refusal leaves the PREVIOUS record intact."""

    @pytest.fixture
    def tab(self, monkeypatch, tmp_path, mocker, hetzner_host):
        from remo_cli.core import tab_records
        from remo_cli.core.tab_identity import derive_tab_key, detect_tab_identity

        monkeypatch.setenv("REMO_HOME", str(tmp_path / "remo"))
        monkeypatch.setenv("KITTY_WINDOW_ID", "7")
        mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=hetzner_host)
        mocker.patch("remo_cli.providers.aws.auto_start_aws_if_stopped", return_value=hetzner_host)
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mocker.patch("remo_cli.core.ssh.build_ssh_base_cmd", return_value=["ssh", "x"])
        mocker.patch("remo_cli.core.ssh.reset_terminal")
        # CliRunner's stdin has no fileno; the tty save/restore is not under test.
        mocker.patch("remo_cli.core.ssh.sys")
        mocker.patch("remo_cli.core.ssh.termios.tcgetattr", return_value=None)
        run = mocker.patch("remo_cli.core.ssh.subprocess.run")
        key = derive_tab_key(
            detect_tab_identity({"KITTY_WINDOW_ID": "7"}), tab_records.get_secret()
        )
        tab_records.record(key, "previous-host", "previous-project")
        return key, run

    @staticmethod
    def _unchanged(key):
        from remo_cli.core import tab_records

        rec = tab_records.load(key)
        assert rec is not None
        assert (rec.host, rec.project) == ("previous-host", "previous-project")

    def test_invalid_tunnel_spec_keeps_previous_record(self, runner, tab):
        key, run = tab
        result = runner.invoke(shell, ["-p", "A", "-L", "not-a-port"])
        assert result.exit_code != 0
        assert "Invalid tunnel specification" in result.output
        run.assert_not_called()
        self._unchanged(key)

    def test_local_port_in_use_keeps_previous_record(self, runner, tab, mocker):
        key, run = tab
        mocker.patch("remo_cli.core.ssh.shutil.which", return_value="/usr/bin/ss")
        run.return_value = mocker.Mock(stdout="LISTEN 0 4096 *:8080")
        result = runner.invoke(shell, ["-p", "A", "-L", "8080"])
        assert result.exit_code != 0
        assert "already in use" in result.output
        self._unchanged(key)

    def test_auto_start_failure_keeps_previous_record(self, runner, tab, mocker):
        from remo_cli.core.errors import OperationFailedError

        key, run = tab
        mocker.patch(
            "remo_cli.providers.aws.auto_start_aws_if_stopped",
            side_effect=OperationFailedError("cannot start"),
        )
        result = runner.invoke(shell, ["-p", "A"])
        assert result.exit_code != 0
        run.assert_not_called()
        self._unchanged(key)

    def test_declining_connect_anyway_keeps_previous_record(self, runner, tab, mocker):
        from remo_cli.core.errors import OperationFailedError

        key, run = tab
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mocker.patch(
            "remo_cli.cli.shell._run_tools_upgrade", side_effect=OperationFailedError("boom")
        )
        # yes to the upgrade, no to "Connect anyway?"
        mocker.patch("remo_cli.core.output.confirm", side_effect=[True, False])
        result = runner.invoke(shell, ["-p", "A"])
        assert result.exit_code != 0
        run.assert_not_called()
        self._unchanged(key)

    def test_declining_the_upgrade_still_connects_and_records(self, runner, tab, mocker):
        """Declining the upgrade does not abort, so the connection happens."""
        from remo_cli.core import tab_records

        key, run = tab
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mocker.patch("remo_cli.core.output.confirm", return_value=False)
        result = runner.invoke(shell, ["-p", "A"])
        assert result.exit_code == 0, result.output
        run.assert_called_once()
        rec = tab_records.load(key)
        assert rec is not None and (rec.host, rec.project) == ("webserver", "A")

    def test_normal_connect_records_before_ssh_and_forwards_key(self, runner, tab):
        from remo_cli.core import tab_records

        key, run = tab
        seen = {}

        def _ssh(cmd, env=None):
            rec = tab_records.load(key)
            seen["record"] = (rec.host, rec.project) if rec else None
            seen["env_key"] = (env or {}).get("REMO_TAB_KEY")

        run.side_effect = _ssh
        result = runner.invoke(shell, ["-p", "A"])
        assert result.exit_code == 0, result.output
        assert seen == {"record": ("webserver", "A"), "env_key": key}

    def test_store_failure_at_connect_warns_once_and_still_connects(self, runner, tab, mocker):
        from remo_cli.core.tab_records import TabRecordError

        key, run = tab
        mocker.patch("remo_cli.core.tab_records.record", side_effect=TabRecordError("disk full"))
        result = runner.invoke(shell, ["-p", "A"])
        assert result.exit_code == 0
        assert result.output.count("Could not remember this tab") == 1
        run.assert_called_once()
        assert run.call_args.kwargs["env"]["REMO_TAB_KEY"] == key
