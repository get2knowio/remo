"""Tests for the split-out `resize()` verb on providers/hetzner.py (021).

`update()` (three-intent: label backfill + optional volume resize + configure
play) is gone, replaced by three single-intent functions. This file
characterizes `resize()` — persistent-volume resize only, never the configure
play (SC-001: resize never runs the upgrade/configure path).
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import hetzner as providers_hetzner


@pytest.fixture(autouse=True)
def _no_network(mocker, tmp_config_dir):
    """Belt-and-suspenders: block real subprocess/ansible invocation."""
    mocker.patch("subprocess.run", side_effect=AssertionError("subprocess.run called"))


def test_resize_runs_resize_play_only(mocker):
    mocker.patch.object(
        providers_hetzner, "_lookup_hetzner_host", return_value="1.2.3.4"
    )
    run_playbook = mocker.patch.object(
        providers_hetzner, "run_playbook", return_value=0
    )

    providers_hetzner.resize(name="dev1", volume_size="20")

    playbooks_run = [call.args[0] for call in run_playbook.call_args_list]
    assert "hetzner_resize.yml" in playbooks_run
    assert "hetzner_configure.yml" not in playbooks_run


def test_resize_raises_precondition_error_when_server_not_registered(mocker):
    mocker.patch.object(providers_hetzner, "_lookup_hetzner_host", return_value="")

    with pytest.raises(PreconditionError):
        providers_hetzner.resize(name="dev1", volume_size="20")


class TestAddedSshHostGuard:
    """FR-012: resize must reject a manually-registered SSH host."""

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

    def test_resize_rejects_added_ssh_host(self):
        with pytest.raises(PreconditionError) as exc:
            providers_hetzner.resize(name=self.ADDED_NAME, volume_size="20")
        message = str(exc.value)
        assert "manually-registered SSH host" in message
        assert "remo remove" in message
