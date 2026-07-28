"""Characterization tests for ``providers/incus.py::resize`` (021-cli-plane-separation).

``resize`` is the resource-change-only verb split out of the former
three-intent ``update`` (spec 021). It must apply resource changes via the
resize playbook and must never run the dev-tools configure playbook — that's
``upgrade``'s job now. Also covers the FR-012 added-ssh-host guard.
"""

from __future__ import annotations

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import incus as providers_incus


class TestResizeRunsResizePlaybookOnly:
    def test_cores_triggers_resize_playbook(self, mocker):
        resize_spy = mocker.patch("remo_cli.providers.incus._run_resize_playbook")
        configure_spy = mocker.patch("remo_cli.providers.incus.run_playbook")
        providers_incus.resize(name="dev1", host="h", host_user="u", cores=4)
        resize_spy.assert_called_once_with(
            name="dev1",
            host="h",
            user="u",
            volume_size="",
            cores=4,
            memory=0,
            verbose=False,
        )
        configure_spy.assert_not_called()

    def test_volume_size_triggers_resize_playbook(self, mocker):
        resize_spy = mocker.patch("remo_cli.providers.incus._run_resize_playbook")
        configure_spy = mocker.patch("remo_cli.providers.incus.run_playbook")
        providers_incus.resize(name="dev1", host="h", host_user="u", volume_size="20")
        resize_spy.assert_called_once()
        assert resize_spy.call_args.kwargs["volume_size"] == "20"
        configure_spy.assert_not_called()

    def test_memory_triggers_resize_playbook(self, mocker):
        resize_spy = mocker.patch("remo_cli.providers.incus._run_resize_playbook")
        configure_spy = mocker.patch("remo_cli.providers.incus.run_playbook")
        providers_incus.resize(name="dev1", host="h", host_user="u", memory=2048)
        resize_spy.assert_called_once()
        assert resize_spy.call_args.kwargs["memory"] == 2048
        configure_spy.assert_not_called()

    def test_host_lookup_when_host_not_given(self, mocker):
        mocker.patch(
            "remo_cli.providers.incus._lookup_incus_host",
            return_value=("myhost", "paul"),
        )
        resize_spy = mocker.patch("remo_cli.providers.incus._run_resize_playbook")
        configure_spy = mocker.patch("remo_cli.providers.incus.run_playbook")
        providers_incus.resize(name="dev1", cores=2)
        resize_spy.assert_called_once_with(
            name="dev1",
            host="myhost",
            user="paul",
            volume_size="",
            cores=2,
            memory=0,
            verbose=False,
        )
        configure_spy.assert_not_called()


class TestResizeGuardsAddedSshHost:
    """FR-012: resize on a manually-registered SSH host must fail clearly,
    not silently mis-target it."""

    @pytest.fixture(autouse=True)
    def _no_network(self, mocker):
        mocker.patch("subprocess.run", side_effect=AssertionError("subprocess.run called"))

    def test_resize_rejects_added_ssh_host(self, tmp_config_dir):
        save_known_host(
            KnownHost(
                type="ssh",
                name="box",
                host="1.2.3.4",
                user="remo",
                instance_id="22",
                access_mode="direct",
            )
        )
        with pytest.raises(PreconditionError) as exc:
            providers_incus.resize(name="box", cores=2)
        message = str(exc.value)
        assert "manually-registered SSH host" in message
        assert "remo remove" in message
        assert "box" in message
