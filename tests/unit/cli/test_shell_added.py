"""`remo shell` behaviour for manually-added SSH hosts (feature 014, FR-011).

The pre-connect version check applies to added (type="ssh") hosts, because 022
gave them `remo configure` — the same shared tasks/configure_dev_tools.yml role
list a provider upgrade runs, writing the same `~/.remo-version` marker. What
FR-011 protects is narrower than "skip the check": an added host that was never
configured has no marker, and must drop straight into a plain login shell rather
than nag on every connect. See #178.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.shell import shell
from remo_cli.models.host import KnownHost


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _ssh_host() -> KnownHost:
    return KnownHost(
        type="ssh",
        name="mybox",
        host="1.2.3.4",
        user="remo",
        instance_id="22",
        access_mode="direct",
    )


def _incus_host() -> KnownHost:
    return KnownHost(type="incus", name="h/dev", host="10.0.0.5", user="remo")


def test_unconfigured_ssh_host_connects_without_prompting(runner, mocker) -> None:
    """FR-011, restated as what it actually protects.

    An added host with no ``~/.remo-version`` was never ``remo configure``d —
    a plain SSH box by the operator's choice — so it drops straight into a login
    shell. The probe itself DOES run now (it is how we learn there is no
    marker); what must not happen is a prompt.

    This replaces an earlier assertion that the probe was never issued at all,
    which pinned #178: it made a configured-but-stale added host permanently
    invisible to the check.
    """
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=_ssh_host())
    mocker.patch(
        "remo_cli.providers.aws.auto_start_aws_if_stopped",
        side_effect=lambda h: h,
    )
    mocker.patch(
        "remo_cli.core.version.get_current_version", return_value="2.2.0"
    )
    check = mocker.patch(
        "remo_cli.core.ssh.check_remote_version", return_value=(None, None)
    )
    upgrade = mocker.patch("remo_cli.cli.shell._run_tools_upgrade")
    connect = mocker.patch("remo_cli.core.ssh.shell_connect")

    result = runner.invoke(shell, ["mybox"], input="")

    assert result.exit_code == 0
    check.assert_called_once()
    upgrade.assert_not_called()
    assert "no version info" not in result.output
    connect.assert_called_once()


def test_stale_configured_ssh_host_is_offered_remo_configure(runner, mocker) -> None:
    """The #178 regression: a configured added host that is behind must be told.

    `remo configure` runs the same shared tasks/configure_dev_tools.yml role
    list a provider upgrade runs, so the prompt has to name it — `remo ssh
    upgrade` is not a command that exists.
    """
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=_ssh_host())
    mocker.patch(
        "remo_cli.providers.aws.auto_start_aws_if_stopped",
        side_effect=lambda h: h,
    )
    mocker.patch(
        "remo_cli.core.version.get_current_version", return_value="4.3.4"
    )
    mocker.patch(
        "remo_cli.core.ssh.check_remote_version", return_value=("4.3.2", None)
    )
    upgrade = mocker.patch("remo_cli.cli.shell._run_tools_upgrade")
    connect = mocker.patch("remo_cli.core.ssh.shell_connect")

    result = runner.invoke(shell, ["mybox"], input="y\n")

    assert result.exit_code == 0
    assert "4.3.2" in result.output and "4.3.4" in result.output
    assert "remo configure mybox" in result.output
    assert "remo ssh upgrade" not in result.output
    upgrade.assert_called_once()
    connect.assert_called_once()


def test_ssh_host_ahead_of_client_warns_like_any_other(runner, mocker) -> None:
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=_ssh_host())
    mocker.patch(
        "remo_cli.providers.aws.auto_start_aws_if_stopped",
        side_effect=lambda h: h,
    )
    mocker.patch(
        "remo_cli.core.version.get_current_version", return_value="4.3.2"
    )
    mocker.patch(
        "remo_cli.core.ssh.check_remote_version", return_value=("4.3.4", None)
    )
    upgrade = mocker.patch("remo_cli.cli.shell._run_tools_upgrade")
    connect = mocker.patch("remo_cli.core.ssh.shell_connect")

    result = runner.invoke(shell, ["mybox"])

    assert result.exit_code == 0
    assert "newer tools" in result.output
    upgrade.assert_not_called()
    connect.assert_called_once()


def test_managed_host_still_runs_version_check(runner, mocker) -> None:
    # Contrast: a provider host DOES get the version check (gate is type-specific).
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=_incus_host())
    mocker.patch(
        "remo_cli.providers.aws.auto_start_aws_if_stopped",
        side_effect=lambda h: h,
    )
    mocker.patch(
        "remo_cli.core.version.get_current_version", return_value="2.2.0"
    )
    check = mocker.patch(
        "remo_cli.core.ssh.check_remote_version", return_value=("2.2.0", None)
    )
    mocker.patch("remo_cli.core.version.version_is_newer", return_value=False)
    connect = mocker.patch("remo_cli.core.ssh.shell_connect")

    result = runner.invoke(shell, ["dev"])

    assert result.exit_code == 0
    check.assert_called_once()
    connect.assert_called_once()
