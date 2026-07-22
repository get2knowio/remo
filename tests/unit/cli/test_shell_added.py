"""`remo shell` degradation for manually-added SSH hosts (feature 014, FR-011).

An added (type="ssh") host has no managed tooling, so the pre-connect
tools/version check must be skipped — no "Update tools?" prompt — and the
command drops straight into a plain login shell via shell_connect.
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


def test_ssh_host_skips_version_check(runner, mocker) -> None:
    mocker.patch("remo_cli.core.ssh.resolve_remo_host", return_value=_ssh_host())
    mocker.patch(
        "remo_cli.providers.aws.auto_start_aws_if_stopped",
        side_effect=lambda h: h,
    )
    mocker.patch(
        "remo_cli.core.version.get_current_version", return_value="2.2.0"
    )
    check = mocker.patch("remo_cli.core.ssh.check_remote_version")
    connect = mocker.patch("remo_cli.core.ssh.shell_connect")

    result = runner.invoke(shell, ["mybox"])

    assert result.exit_code == 0
    check.assert_not_called()  # FR-011: no version probe for an added host
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
