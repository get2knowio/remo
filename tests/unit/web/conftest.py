"""Shared fixtures for web unit tests (011-web-adopt, T002).

Provides `state_dir`: a factory that lays a temp ``REMO_HOME`` out in each of
the four configuration states from research R2 / data-model
`ConfigurationState`:

- ``unconfigured``  -- empty writable dir
- ``adopted``       -- registry + ``web-identity/`` keypair pair + state.json
- ``mount_configured`` -- registry + read-only dir, OR registry + a user SSH
  identity without a service keypair
- ``broken``        -- artifacts present but unreadable (or a half-pair)

The fixture isolates ``$HOME`` to a temp directory and clears
``REMO_WEB_SSH_IDENTITY_FILE`` so a developer's real ``~/.ssh`` keys never
leak into user-identity detection, and restores any permissions it lowered
so pytest can clean tmp dirs up.

Settings follow the existing convention: ``REMO_HOME`` is set via the
top-level ``tmp_config_dir`` fixture, so a plain ``WebSettings()`` (or
``state_dir.settings()``) resolves every state path into the temp dir.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from remo_cli.web.config import WebSettings

DEFAULT_DEPLOYMENT_ID = "dep12345"

_DEFAULT_REGISTRY_LINES = ["incus:dev:127.0.0.1:remo"]

_FAKE_PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "bm90IGEgcmVhbCBrZXksIGp1c3QgYSBmaXh0dXJl\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


class StateDirFactory:
    """Builds ``REMO_HOME`` layouts for each `ConfigurationState`."""

    def __init__(self, home: Path, user_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = home
        self.user_home = user_home
        self._monkeypatch = monkeypatch
        self._perm_restores: list[tuple[Path, int]] = []

    # -- paths ---------------------------------------------------------------

    @property
    def registry_path(self) -> Path:
        return self.home / "known_hosts"

    @property
    def web_identity_dir(self) -> Path:
        return self.home / "web-identity"

    @property
    def private_key_path(self) -> Path:
        return self.web_identity_dir / "id_ed25519"

    @property
    def public_key_path(self) -> Path:
        return self.web_identity_dir / "id_ed25519.pub"

    @property
    def state_json_path(self) -> Path:
        return self.web_identity_dir / "state.json"

    def settings(self, **overrides: object) -> WebSettings:
        overrides.setdefault("ssh_control_dir", str(self.user_home / "ssh-ctrl"))
        return WebSettings(**overrides)  # type: ignore[arg-type]

    # -- building blocks -------------------------------------------------------

    def write_registry(self, lines: list[str] | None = None) -> Path:
        lines = _DEFAULT_REGISTRY_LINES if lines is None else lines
        self.registry_path.write_text("".join(f"{line}\n" for line in lines))
        return self.registry_path

    def write_keypair(
        self,
        deployment_id: str = DEFAULT_DEPLOYMENT_ID,
        *,
        private: bool = True,
        public: bool = True,
    ) -> None:
        self.web_identity_dir.mkdir(mode=0o700, exist_ok=True)
        if private:
            self.private_key_path.write_text(_FAKE_PRIVATE_KEY)
            self.private_key_path.chmod(0o600)
        if public:
            self.public_key_path.write_text(
                f"ssh-ed25519 AAAAC3fixturekey remo-web@{deployment_id}\n"
            )
            self.public_key_path.chmod(0o644)

    def write_state_json(
        self,
        deployment_id: str = DEFAULT_DEPLOYMENT_ID,
        created_at: str = "2026-07-16T00:00:00+00:00",
    ) -> None:
        self.web_identity_dir.mkdir(mode=0o700, exist_ok=True)
        self.state_json_path.write_text(
            json.dumps({"deployment_id": deployment_id, "created_at": created_at})
        )

    def add_user_identity(self) -> Path:
        """A readable ``~/.ssh/id_ed25519`` in the isolated fake ``$HOME``."""
        ssh_dir = self.user_home / ".ssh"
        ssh_dir.mkdir(mode=0o700, exist_ok=True)
        key = ssh_dir / "id_ed25519"
        key.write_text(_FAKE_PRIVATE_KEY)
        key.chmod(0o600)
        return key

    def set_identity_env(self, path: Path) -> None:
        """Point ``REMO_WEB_SSH_IDENTITY_FILE`` (the explicit override) at a key."""
        self._monkeypatch.setenv("REMO_WEB_SSH_IDENTITY_FILE", str(path))

    def chmod(self, path: Path, mode: int) -> None:
        """chmod that records the original mode for teardown restoration."""
        self._perm_restores.append((path, path.stat().st_mode & 0o777))
        path.chmod(mode)

    def restore_permissions(self) -> None:
        for path, mode in reversed(self._perm_restores):
            try:
                path.chmod(mode)
            except OSError:
                pass
        self._perm_restores.clear()

    # -- complete layouts (one per ConfigurationState) -------------------------

    def unconfigured(self) -> Path:
        """Empty writable dir -- nothing else to do, `home` already is one."""
        return self.home

    def adopted(self, deployment_id: str = DEFAULT_DEPLOYMENT_ID) -> Path:
        self.write_registry()
        self.write_keypair(deployment_id)
        self.write_state_json(deployment_id)
        return self.home

    def mount_configured_readonly(self) -> Path:
        """Registry present, ``REMO_HOME`` read-only (the `:ro` bind mount)."""
        self.write_registry()
        self.chmod(self.home, 0o555)
        return self.home

    def mount_configured_user_identity(self) -> Path:
        """Registry present + user SSH identity, no service keypair."""
        self.write_registry()
        self.add_user_identity()
        return self.home

    def broken_unreadable_registry(self) -> Path:
        """Registry present but this process cannot read it."""
        self.write_registry()
        self.chmod(self.registry_path, 0o000)
        return self.home

    def broken_half_pair(self, *, keep: str = "private") -> Path:
        """Exactly one of id_ed25519 / id_ed25519.pub exists."""
        self.write_keypair(private=keep == "private", public=keep == "public")
        return self.home


@pytest.fixture
def state_dir(
    tmp_config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StateDirFactory]:
    """A `StateDirFactory` rooted at a temp ``REMO_HOME``, HOME-isolated."""
    user_home = tmp_path / "user-home"
    user_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv("REMO_WEB_SSH_IDENTITY_FILE", raising=False)
    monkeypatch.delenv("REMO_WEB_API_TOKEN", raising=False)

    factory = StateDirFactory(home=tmp_config_dir, user_home=user_home, monkeypatch=monkeypatch)
    yield factory
    factory.restore_permissions()
