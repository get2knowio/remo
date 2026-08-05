"""Which SSH identity the *service* uses for a host (`WebSettings.ssh_identity_for`).

An added host's registry entry stores the operator's key path — a **workstation**
path. The service, which commonly runs in a container, generally does not have
that file. Handing it to ssh anyway is not a harmless no-op: `build_ssh_opts`
pairs an identity with `IdentitiesOnly=yes`, so a missing file suppresses every
key that *would* have worked and turns a working mounted-mode deployment into
a guaranteed `auth_failed`.

Adopted mode was never affected (it always passes the service's own key). This
pins both modes so neither regresses into the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remo_cli.models.host import KnownHost
from remo_cli.web.config import WebSettings


def _added_host(identity: str = "") -> KnownHost:
    return KnownHost(
        type="ssh",
        name="mbp",
        host="10.0.0.5",
        user="remo",
        instance_id="2222",
        access_mode="direct",
        region=identity,
    )


@pytest.fixture
def settings(tmp_path: Path) -> WebSettings:
    s = WebSettings()
    s.web_identity_dir = tmp_path / "web-identity"
    return s


class TestMountedMode:
    """No service identity: fall back to ambient keys unless the path is real."""

    def test_a_missing_workstation_key_path_is_not_used(self, settings, mocker):
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=False)

        # The regression: this used to return the path, and ssh then refused
        # every other key because of IdentitiesOnly=yes.
        assert settings.ssh_identity_for(_added_host("/home/you/.ssh/id_ed25519")) is None

    def test_a_key_path_that_does_resolve_here_is_honored(self, settings, mocker, tmp_path):
        # A home-directory mount can make the recorded path genuinely valid;
        # blanket-suppressing it would break that deployment.
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=False)
        key = tmp_path / "id_ed25519"
        key.write_text("PRIVATE KEY")

        assert settings.ssh_identity_for(_added_host(str(key))) == str(key)

    def test_a_directory_is_not_mistaken_for_a_key(self, settings, mocker, tmp_path):
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=False)
        assert settings.ssh_identity_for(_added_host(str(tmp_path))) is None

    def test_no_stored_identity_yields_none(self, settings, mocker):
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=False)
        assert settings.ssh_identity_for(_added_host()) is None

    def test_provider_hosts_never_carry_an_identity(self, settings, mocker):
        # `ssh_identity` is None for every non-ssh type; a Proxmox entry's
        # `region` (the node login) must never be read as a key path.
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=False)
        host = KnownHost(type="proxmox", name="n/c", host="h", user="remo", region="root")
        assert settings.ssh_identity_for(host) is None


class TestAdoptedMode:
    """The service's own key, always — it is the one `remo web push` authorized."""

    def test_service_key_wins_over_a_stored_identity(self, settings, mocker, tmp_path):
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=True)
        real_key = tmp_path / "operator_key"
        real_key.write_text("PRIVATE KEY")

        chosen = settings.ssh_identity_for(_added_host(str(real_key)))

        assert chosen == str(settings.service_private_key_path)
        assert chosen != str(real_key)

    def test_service_key_used_even_with_no_stored_identity(self, settings, mocker):
        mocker.patch.object(WebSettings, "_service_identity_active", return_value=True)
        assert settings.ssh_identity_for(_added_host()) == str(
            settings.service_private_key_path
        )
