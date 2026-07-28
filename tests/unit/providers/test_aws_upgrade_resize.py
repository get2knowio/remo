"""Tests for the split `upgrade()`/`resize()` verbs on providers/aws.py (021).

`update()` (three-intent: IP refresh + optional EBS resize + configure play)
is gone, replaced by two single-intent functions:

- `upgrade()` — in-instance configure play only, never touches the EBS
  volume (SC-001: upgrade never touches provider-side resource state).
- `resize()` — EBS volume resize only, never runs the configure play.

Both still refresh the known-hosts registry entry (current IP/instance ID)
before acting, and both honor the FR-012 added-SSH-host guard.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import aws as providers_aws


RUNNING_INSTANCE = {
    "InstanceId": "i-0123456789abcdef0",
    "PublicIpAddress": "1.2.3.4",
}


@pytest.fixture(autouse=True)
def _no_network(mocker, tmp_config_dir):
    """Belt-and-suspenders: block real subprocess/ansible invocation."""
    mocker.patch("subprocess.run", side_effect=AssertionError("subprocess.run called"))


def test_upgrade_runs_configure_play_only(mocker):
    mocker.patch(
        "remo_cli.providers.aws._get_running_instance", return_value=RUNNING_INSTANCE
    )
    mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-west-2")
    save_known_host_spy = mocker.patch("remo_cli.providers.aws.save_known_host")
    run_playbook = mocker.patch("remo_cli.providers.aws.run_playbook", return_value=0)
    mocker.patch(
        "remo_cli.providers.aws.build_configure_extra_vars", return_value=[]
    )

    providers_aws.upgrade(name="dev1")

    playbooks_run = [call.args[0] for call in run_playbook.call_args_list]
    assert "aws_configure.yml" in playbooks_run
    assert "aws_resize.yml" not in playbooks_run
    save_known_host_spy.assert_called_once()


def test_resize_runs_resize_play_only(mocker):
    mocker.patch(
        "remo_cli.providers.aws._get_running_instance", return_value=RUNNING_INSTANCE
    )
    mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-west-2")
    save_known_host_spy = mocker.patch("remo_cli.providers.aws.save_known_host")
    run_playbook = mocker.patch("remo_cli.providers.aws.run_playbook", return_value=0)

    providers_aws.resize(name="dev1", volume_size="100")

    playbooks_run = [call.args[0] for call in run_playbook.call_args_list]
    assert "aws_resize.yml" in playbooks_run
    assert "aws_configure.yml" not in playbooks_run
    save_known_host_spy.assert_called_once()


def test_upgrade_raises_precondition_error_when_no_running_instance(mocker):
    mocker.patch("remo_cli.providers.aws._get_running_instance", return_value=None)
    mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-west-2")

    with pytest.raises(PreconditionError):
        providers_aws.upgrade(name="dev1")


def test_resize_raises_precondition_error_when_no_running_instance(mocker):
    mocker.patch("remo_cli.providers.aws._get_running_instance", return_value=None)
    mocker.patch("remo_cli.providers.aws.get_aws_region", return_value="us-west-2")

    with pytest.raises(PreconditionError):
        providers_aws.resize(name="dev1", volume_size="100")


class TestAddedSshHostGuard:
    """FR-012: upgrade/resize must reject a manually-registered SSH host."""

    ADDED_NAME = "box"

    @pytest.fixture(autouse=True)
    def added_ssh_host(self, tmp_config_dir):
        save_known_host(
            KnownHost(
                type="ssh",
                name=self.ADDED_NAME,
                host="1.2.3.4",
                user="remo",
                instance_id="22",
                access_mode="direct",
            )
        )

    def test_upgrade_rejects_added_ssh_host(self):
        with pytest.raises(PreconditionError) as exc:
            providers_aws.upgrade(name=self.ADDED_NAME)
        message = str(exc.value)
        assert "manually-registered SSH host" in message
        assert "remo remove" in message

    def test_resize_rejects_added_ssh_host(self):
        with pytest.raises(PreconditionError) as exc:
            providers_aws.resize(name=self.ADDED_NAME, volume_size="100")
        message = str(exc.value)
        assert "manually-registered SSH host" in message
        assert "remo remove" in message
