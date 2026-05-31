"""Unit tests for remo.cli.shell module."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from click.testing import CliRunner
from jinja2 import Environment, FileSystemLoader

from remo_cli.cli.shell import shell
from remo_cli.models.host import KnownHost

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_ROOT = REPO_ROOT / "ansible"


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


def _render_template(relative_path: str, **context: str) -> str:
    env = Environment(
        autoescape=False,
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
    )
    return env.get_template(relative_path).render(**context)


def _wait_for_log_lines(path: Path, count: int) -> list[list[str]]:
    for _ in range(50):
        if path.exists():
            lines = path.read_text().splitlines()
            if len(lines) >= count:
                return [json.loads(line) for line in lines]
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {count} devcontainer call(s) in {path}")


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
        """When remote is behind local, user is prompted to update."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_called_once()
        assert "v0.8.0" in mock_confirm.call_args[0][0]
        assert "v0.9.0" in mock_confirm.call_args[0][0]

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_behind_update_accepted(self, runner, mocker):
        """When user accepts update, provider update is called."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        mocker.patch("remo_cli.core.output.confirm", return_value=True)
        mock_update = mocker.patch("remo_cli.providers.hetzner.update", return_value=0)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_update.assert_called_once_with(name="webserver")

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_update_failure_prompts_before_connect(self, runner, mocker):
        """When tools update fails, user is prompted to confirm connect."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        # First confirm() = "Update?" → True; second = "Connect anyway?" → True
        mock_confirm = mocker.patch(
            "remo_cli.core.output.confirm", side_effect=[True, True]
        )
        mocker.patch("remo_cli.providers.hetzner.update", return_value=2)
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        assert mock_confirm.call_count == 2
        assert "Connect anyway?" in mock_confirm.call_args_list[1][0][0]
        mock_shell_connect.assert_called_once()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_update_failure_decline_aborts(self, runner, mocker):
        """When user declines after failed update, shell_connect is not called."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=("0.8.0", None))
        # First confirm() = "Update?" → True; second = "Connect anyway?" → False
        mocker.patch("remo_cli.core.output.confirm", side_effect=[True, False])
        mocker.patch("remo_cli.providers.hetzner.update", return_value=2)
        mock_shell_connect = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, [])

        assert result.exit_code == 2
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
        """When remote has no version marker, user is prompted to update."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=(None, None))
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_called_once()
        assert "no version info" in mock_confirm.call_args[0][0]

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
    def test_reserved_vault_project_is_allowed(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["-p", "_remo-vault"])

        assert result.exit_code == 0
        _, kwargs = mock_sc.call_args
        assert kwargs["project"] == "_remo-vault"

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_invalid_project_name_is_rejected(self, runner, mocker):
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_sc = mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["-p", "bad name"])

        assert result.exit_code != 0
        mock_sc.assert_not_called()
        assert "Invalid project" in result.output

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
        mocker.patch("remo_cli.core.ssh.shell_connect")

        result = runner.invoke(shell, ["-p", "my-app", "--detach"])

        assert result.exit_code == 2


class TestProjectLaunchTemplate:
    def test_detached_devcontainer_uses_generated_config_and_fetch_wrapper(self, tmp_path: Path) -> None:
        script = _render_template(
            "roles/user_setup/templates/project-launch.sh.j2",
            dev_workspace_dir=str(tmp_path / "projects"),
        )
        script_path = tmp_path / "project-launch.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        home = tmp_path / "home"
        (home / ".local" / "share" / "remo-secrets").mkdir(parents=True)
        (home / ".cache").mkdir(parents=True)
        feature_path = home / ".local" / "share" / "remo-secrets" / "feature-devcontainer.json"
        feature_path.write_text(
            _render_template("roles/remo_secrets_feature/templates/feature-devcontainer.json.j2")
        )

        project_dir = tmp_path / "projects" / "demo"
        (project_dir / ".devcontainer").mkdir(parents=True)
        (project_dir / ".devcontainer" / "devcontainer.json").write_text(
            json.dumps({"name": "Demo", "customizations": {"vscode": {"settings": {"editor.tabSize": 2}}}})
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        devcontainer_log = tmp_path / "devcontainer.log"
        (bin_dir / "devcontainer").write_text(
            "#!/bin/bash\n"
            "python3 - \"$@\" <<'PY'\n"
            "import json, os, pathlib, sys\n"
            "log = pathlib.Path(os.environ['FAKE_DEVCONTAINER_LOG'])\n"
            "with log.open('a', encoding='utf-8') as fh:\n"
            "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "PY\n"
        )
        (bin_dir / "devcontainer").chmod(0o755)
        (bin_dir / "zellij").write_text("#!/bin/bash\nexit 0\n")
        (bin_dir / "zellij").chmod(0o755)

        result = subprocess.run(
            [str(script_path), "--project", "demo", "--detach", "--exec", "echo ready"],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(home),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "FAKE_DEVCONTAINER_LOG": str(devcontainer_log),
            },
        )

        assert result.returncode == 0, result.stderr
        generated = home / ".cache" / "remo-devcontainer-configs" / "demo.json"
        merged = json.loads(generated.read_text())
        assert any("target=/workspace/.remo/manifest.toml" in mount for mount in merged["mounts"])
        assert merged["containerEnv"]["REMO_BROKER_PROJECT_SOCKET"] == "/run/remo-broker/${localWorkspaceFolderBasename}.sock"

        calls = _wait_for_log_lines(devcontainer_log, 2)
        assert any("--config" in call for call in calls)
        exec_call = next(call for call in calls if call[0] == "exec")
        assert "remo-fetch-secrets" in exec_call[-1]

    def test_managed_vault_project_skips_feature_injection(self, tmp_path: Path) -> None:
        script = _render_template(
            "roles/user_setup/templates/project-launch.sh.j2",
            dev_workspace_dir=str(tmp_path / "projects"),
        )
        script_path = tmp_path / "project-launch.sh"
        script_path.write_text(script)
        script_path.chmod(0o755)

        home = tmp_path / "home"
        (home / ".local" / "share" / "remo-secrets").mkdir(parents=True)
        vault_dir = tmp_path / "projects" / "_remo-vault" / ".devcontainer"
        vault_dir.mkdir(parents=True)
        (vault_dir / "devcontainer.json").write_text(json.dumps({"name": "Vault"}))

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        devcontainer_log = tmp_path / "devcontainer.log"
        (bin_dir / "devcontainer").write_text(
            "#!/bin/bash\n"
            "python3 - \"$@\" <<'PY'\n"
            "import json, os, pathlib, sys\n"
            "log = pathlib.Path(os.environ['FAKE_DEVCONTAINER_LOG'])\n"
            "with log.open('a', encoding='utf-8') as fh:\n"
            "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "PY\n"
        )
        (bin_dir / "devcontainer").chmod(0o755)
        (bin_dir / "zellij").write_text("#!/bin/bash\nexit 0\n")
        (bin_dir / "zellij").chmod(0o755)

        result = subprocess.run(
            [str(script_path), "--project", "_remo-vault", "--detach", "--exec", "echo ready"],
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "HOME": str(home),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "FAKE_DEVCONTAINER_LOG": str(devcontainer_log),
            },
        )

        assert result.returncode == 0, result.stderr
        assert not (home / ".cache" / "remo-devcontainer-configs" / "_remo-vault.json").exists()
        calls = _wait_for_log_lines(devcontainer_log, 2)
        assert all("--config" not in call for call in calls)
        exec_call = next(call for call in calls if call[0] == "exec")
        assert "remo-fetch-secrets" not in exec_call[-1]

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


class TestRunProviderUpdate:
    """Tests for _run_provider_update()."""

    def test_aws_update(self, mocker):
        from remo_cli.cli.shell import _run_provider_update

        host = KnownHost(type="aws", name="devbox", host="1.2.3.4", user="remo")
        mock_update = mocker.patch("remo_cli.providers.aws.update", return_value=0)

        _run_provider_update(host)

        mock_update.assert_called_once_with(name="devbox")

    def test_hetzner_update(self, mocker):
        from remo_cli.cli.shell import _run_provider_update

        host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="remo")
        mock_update = mocker.patch("remo_cli.providers.hetzner.update", return_value=0)

        _run_provider_update(host)

        mock_update.assert_called_once_with(name="webserver")

    def test_incus_update_extracts_container_name(self, mocker):
        from remo_cli.cli.shell import _run_provider_update

        host = KnownHost(type="incus", name="myhost/devcontainer", host="192.168.1.50", user="remo")
        mock_update = mocker.patch("remo_cli.providers.incus.update", return_value=0)

        _run_provider_update(host)

        mock_update.assert_called_once_with(name="devcontainer")

    def test_proxmox_update_extracts_node_and_container(self, mocker):
        from remo_cli.cli.shell import _run_provider_update

        host = KnownHost(
            type="proxmox",
            name="lab1/dev1",
            host="192.168.1.46",
            user="remo",
            instance_id="100",
            access_mode="direct",
            region="root",
        )
        mock_update = mocker.patch("remo_cli.providers.proxmox.update", return_value=0)

        _run_provider_update(host)

        mock_update.assert_called_once_with(name="dev1", host="lab1", user="root")
