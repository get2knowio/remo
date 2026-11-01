"""`remo shell`'s pre-connect upgrade dispatches to the Hetzner plugin.

Moved from remo's tests/unit/cli/test_shell.py
(TestRunProviderUpgrade.test_hetzner_update) when Hetzner became an
out-of-tree plugin: `_run_tools_upgrade` must reach the entry-point-registered
provider's ``update_entry`` exactly as it did the built-in one.
"""

from __future__ import annotations

from remo_cli.models.host import KnownHost


def test_hetzner_update(mocker):
    from remo_cli.cli.shell import _run_tools_upgrade

    host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="remo")
    mock_update_entry = mocker.patch("remo_provider_hetzner.provider.update_entry")

    _run_tools_upgrade(host)

    mock_update_entry.assert_called_once_with(host)
