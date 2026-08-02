"""Characterization tests for ``providers/proxmox.py::resize`` (spec 021).

``resize`` is the resource-change-only half of the old three-intent
``update`` verb: it must trigger the resize playbook (never the dev-tools
configure playbook), and must raise ``PreconditionError`` both for an
unresolvable VMID and for a manually-registered (``remo add``) SSH host name
(FR-012 guard, mirrored from ``tests/unit/providers/test_added_provider_guard.py``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import PreconditionError
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost
from remo_cli.providers import proxmox as providers_proxmox


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class TestResize:
    def test_cores_triggers_resize_playbook_only(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        resize_shared = mocker.patch(
            "remo_cli.providers.proxmox._run_resize_shared", autospec=True
        )
        configure = mocker.patch("remo_cli.providers.proxmox.run_playbook")

        result = providers_proxmox.resize(
            name="dev1", host="node", host_user="root", cores=4
        )

        assert result is None
        resize_shared.assert_called_once()
        playbook_name, extra_vars = resize_shared.call_args.args[0], resize_shared.call_args.args[1]
        assert playbook_name == "proxmox_resize.yml"
        assert "-e" in extra_vars and "cores=4" in extra_vars
        # The dev-tools configure playbook (run via run_playbook directly)
        # must never be invoked by resize().
        configure.assert_not_called()

    def test_volume_size_triggers_resize_playbook_only(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        resize_shared = mocker.patch(
            "remo_cli.providers.proxmox._run_resize_shared", autospec=True
        )
        configure = mocker.patch("remo_cli.providers.proxmox.run_playbook")

        providers_proxmox.resize(
            name="dev1", host="node", host_user="root", volume_size="20"
        )

        resize_shared.assert_called_once()
        extra_vars = resize_shared.call_args.args[1]
        assert any("volume_size=" in v for v in extra_vars)
        configure.assert_not_called()

    def test_memory_triggers_resize_playbook_only(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="100")
        resize_shared = mocker.patch(
            "remo_cli.providers.proxmox._run_resize_shared", autospec=True
        )
        configure = mocker.patch("remo_cli.providers.proxmox.run_playbook")

        providers_proxmox.resize(
            name="dev1", host="node", host_user="root", memory=2048
        )

        resize_shared.assert_called_once()
        extra_vars = resize_shared.call_args.args[1]
        assert "memory=2048" in extra_vars
        configure.assert_not_called()

    def test_unresolvable_vmid_raises_precondition_error(self, mocker):
        mocker.patch("remo_cli.providers.proxmox._resolve_vmid", return_value="")
        resize_shared = mocker.patch(
            "remo_cli.providers.proxmox._run_resize_shared", autospec=True
        )

        with pytest.raises(PreconditionError, match="VMID"):
            providers_proxmox.resize(name="dev1", host="node", host_user="root", cores=4)

        resize_shared.assert_not_called()


class TestResizeAddedHostGuard:
    """FR-012: `resize` must reject a manually-registered (`remo add`) SSH host."""

    def test_resize_rejects_added_ssh_host(self, mocker, tmp_config_dir):
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
        mocker.patch(
            "subprocess.run", side_effect=AssertionError("subprocess.run called")
        )

        with pytest.raises(PreconditionError, match="manually-registered SSH host"):
            providers_proxmox.resize(name="box", host="node1")
