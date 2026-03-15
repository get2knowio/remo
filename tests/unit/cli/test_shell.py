"""Unit tests for remo.cli.shell module."""

from __future__ import annotations

from unittest.mock import MagicMock

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
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value="0.8.0")
        mock_confirm = mocker.patch("remo_cli.core.output.confirm")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_not_called()

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_behind_prompts_update(self, runner, mocker):
        """When remote is behind local, user is prompted to update."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.9.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value="0.8.0")
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
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.output.confirm", return_value=True)
        mock_update = mocker.patch("remo_cli.providers.hetzner.update", return_value=0)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_update.assert_called_once_with(name="webserver")

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_remote_ahead_shows_warning(self, runner, mocker):
        """When remote is ahead of local, a warning is shown."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="0.8.0")
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value="0.9.0")
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
        mocker.patch("remo_cli.core.ssh.check_remote_version", return_value=None)
        mock_confirm = mocker.patch("remo_cli.core.output.confirm", return_value=False)

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_confirm.assert_called_once()
        assert "no version info" in mock_confirm.call_args[0][0]

    @pytest.mark.usefixtures("_patch_shell_deps")
    def test_unknown_local_version_skips_check(self, runner, mocker):
        """When local version is unknown, skip the version check."""
        mocker.patch("remo_cli.core.version.get_current_version", return_value="unknown")
        mock_check = mocker.patch("remo_cli.core.ssh.check_remote_version")

        result = runner.invoke(shell, [])

        assert result.exit_code == 0
        mock_check.assert_not_called()


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
