"""Unit tests for `web/state.py` (011-web-adopt, T009).

Covers the full configuration-state detection matrix from research R2 (all
four states, EACCES/probe-failure paths, the mount-configured precedence
rule), service keypair generation per research R3 (create-once with correct
permissions and comment; reuse-never-regenerate; state.json contents), and
the half-pair -> broken rule.

Permission-dependent cases use the skipif-root pattern established by 010's
unreadable-registry tests (root bypasses permission bits). Keypair
generation shells out to the real `ssh-keygen` (already a runtime dependency
of the service image), skipped when the binary is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime

import pytest

from remo_cli.web.state import (
    ConfigurationState,
    ServiceIdentityError,
    detect_state,
    ensure_service_identity,
    load_service_identity,
)

skip_if_root = pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
skip_without_ssh_keygen = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen not available"
)


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


# ---------------------------------------------------------------------------
# State detection matrix (research R2)
# ---------------------------------------------------------------------------


class TestUnconfigured:
    def test_empty_writable_dir(self, state_dir):
        state_dir.unconfigured()
        assert detect_state(state_dir.settings()) is ConfigurationState.UNCONFIGURED

    def test_keypair_without_registry_still_unconfigured(self, state_dir):
        # "generated, awaiting first push" -- keypair exists, no registry.
        state_dir.write_keypair()
        state_dir.write_state_json()
        assert detect_state(state_dir.settings()) is ConfigurationState.UNCONFIGURED

    def test_missing_remo_home_with_writable_parent(self, state_dir, monkeypatch):
        # REMO_HOME does not exist yet but can be created: adoptable.
        missing = state_dir.home.parent / "not-created-yet"
        monkeypatch.setenv("REMO_HOME", str(missing))
        assert detect_state(state_dir.settings()) is ConfigurationState.UNCONFIGURED


class TestAdopted:
    def test_registry_plus_keypair_on_writable_volume(self, state_dir):
        state_dir.adopted()
        assert detect_state(state_dir.settings()) is ConfigurationState.ADOPTED

    def test_adopted_without_state_json_is_still_adopted(self, state_dir):
        # state.json is metadata, not a required artifact for detection.
        state_dir.write_registry()
        state_dir.write_keypair()
        assert detect_state(state_dir.settings()) is ConfigurationState.ADOPTED


class TestMountConfigured:
    @skip_if_root
    def test_registry_plus_readonly_home(self, state_dir):
        state_dir.mount_configured_readonly()
        assert detect_state(state_dir.settings()) is ConfigurationState.MOUNT_CONFIGURED

    def test_registry_plus_user_identity_in_home_ssh(self, state_dir):
        state_dir.mount_configured_user_identity()
        assert detect_state(state_dir.settings()) is ConfigurationState.MOUNT_CONFIGURED

    def test_registry_plus_explicit_identity_env(self, state_dir, tmp_path):
        state_dir.write_registry()
        key = tmp_path / "mounted_key"
        key.write_text("fake key\n")
        state_dir.set_identity_env(key)
        assert detect_state(state_dir.settings()) is ConfigurationState.MOUNT_CONFIGURED

    def test_precedence_user_identity_beats_service_keypair(self, state_dir):
        # Both a user identity AND a full service keypair present: explicit
        # mounts are the operator's stated intent -- mount_configured wins.
        state_dir.adopted()
        state_dir.add_user_identity()
        assert detect_state(state_dir.settings()) is ConfigurationState.MOUNT_CONFIGURED

    @skip_if_root
    def test_precedence_readonly_home_beats_service_keypair(self, state_dir):
        state_dir.adopted()
        state_dir.chmod(state_dir.home, 0o555)
        assert detect_state(state_dir.settings()) is ConfigurationState.MOUNT_CONFIGURED

    def test_user_identity_without_registry_is_not_mount_configured(self, state_dir):
        # mount_configured requires the registry; identity alone on a
        # writable volume is just an unconfigured service.
        state_dir.add_user_identity()
        assert detect_state(state_dir.settings()) is ConfigurationState.UNCONFIGURED


class TestBroken:
    @skip_if_root
    def test_unreadable_registry(self, state_dir):
        state_dir.broken_unreadable_registry()
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    @skip_if_root
    def test_unreadable_private_key(self, state_dir):
        state_dir.adopted()
        state_dir.chmod(state_dir.private_key_path, 0o000)
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    @skip_if_root
    def test_untraversable_home_probe_failure(self, state_dir):
        # EACCES on the directory itself: probes cannot even stat the
        # artifacts. Must classify (broken), never traceback.
        state_dir.write_registry()
        state_dir.chmod(state_dir.home, 0o000)
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    def test_half_pair_private_only(self, state_dir):
        state_dir.broken_half_pair(keep="private")
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    def test_half_pair_public_only(self, state_dir):
        state_dir.broken_half_pair(keep="public")
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    def test_half_pair_with_registry(self, state_dir):
        state_dir.write_registry()
        state_dir.broken_half_pair(keep="private")
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    def test_registry_without_any_identity(self, state_dir):
        # Registry on a writable volume, no service keypair, no user
        # identity: nothing can authenticate -- a damaged adoption.
        state_dir.write_registry()
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN

    @skip_if_root
    def test_readonly_home_without_registry(self, state_dir):
        state_dir.chmod(state_dir.home, 0o555)
        assert detect_state(state_dir.settings()) is ConfigurationState.BROKEN


# ---------------------------------------------------------------------------
# Service keypair generation (research R3, FR-002)
# ---------------------------------------------------------------------------


@skip_without_ssh_keygen
class TestServiceIdentityGeneration:
    def test_first_call_creates_keypair_with_perms_comment_and_state_json(self, state_dir):
        settings = state_dir.settings()
        identity = ensure_service_identity(settings)

        assert state_dir.private_key_path.is_file()
        assert state_dir.public_key_path.is_file()
        assert _mode(state_dir.web_identity_dir) == 0o700
        assert _mode(state_dir.private_key_path) == 0o600
        assert _mode(state_dir.public_key_path) == 0o644

        # deployment_id: 8-char URL-safe token, embedded as the key comment.
        assert len(identity.deployment_id) == 8
        assert set(identity.deployment_id) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )
        public_line = state_dir.public_key_path.read_text().strip()
        assert public_line.startswith("ssh-ed25519 ")
        assert public_line.endswith(f"remo-web@{identity.deployment_id}")
        assert identity.public_key == public_line

        state = json.loads(state_dir.state_json_path.read_text())
        assert state["deployment_id"] == identity.deployment_id
        assert datetime.fromisoformat(state["created_at"])  # parseable ISO-8601
        assert identity.created_at == state["created_at"]

    def test_second_call_reuses_never_regenerates(self, state_dir, monkeypatch):
        settings = state_dir.settings()
        first = ensure_service_identity(settings)
        private_bytes = state_dir.private_key_path.read_bytes()
        state_bytes = state_dir.state_json_path.read_bytes()

        # FR-002: existing key files must never be regenerated -- prove no
        # subprocess runs at all on the second call.
        def _forbidden(*args, **kwargs):
            raise AssertionError("ssh-keygen must not run when the keypair exists")

        monkeypatch.setattr("remo_cli.web.state.subprocess.run", _forbidden)

        second = ensure_service_identity(settings)
        assert second.deployment_id == first.deployment_id
        assert second.public_key == first.public_key
        assert state_dir.private_key_path.read_bytes() == private_bytes
        assert state_dir.state_json_path.read_bytes() == state_bytes

    def test_generation_flips_detection_to_adopted_once_registry_lands(self, state_dir):
        settings = state_dir.settings()
        ensure_service_identity(settings)
        assert detect_state(settings) is ConfigurationState.UNCONFIGURED
        state_dir.write_registry()
        assert detect_state(settings) is ConfigurationState.ADOPTED


class TestServiceIdentityGuards:
    def test_half_pair_raises_instead_of_regenerating(self, state_dir):
        state_dir.broken_half_pair(keep="private")
        with pytest.raises(ServiceIdentityError):
            ensure_service_identity(state_dir.settings())
        # The surviving half must not have been clobbered.
        assert state_dir.private_key_path.is_file()
        assert not state_dir.public_key_path.exists()

    def test_half_pair_public_only_raises(self, state_dir):
        state_dir.broken_half_pair(keep="public")
        with pytest.raises(ServiceIdentityError):
            ensure_service_identity(state_dir.settings())


class TestLoadServiceIdentity:
    def test_absent_keypair_returns_none(self, state_dir):
        assert load_service_identity(state_dir.settings()) is None

    def test_half_pair_returns_none(self, state_dir):
        state_dir.broken_half_pair(keep="private")
        assert load_service_identity(state_dir.settings()) is None

    def test_loads_existing_identity_without_side_effects(self, state_dir):
        state_dir.write_keypair(deployment_id="abcd1234")
        state_dir.write_state_json(deployment_id="abcd1234", created_at="2026-07-16T00:00:00+00:00")

        identity = load_service_identity(state_dir.settings())

        assert identity is not None
        assert identity.deployment_id == "abcd1234"
        assert identity.created_at == "2026-07-16T00:00:00+00:00"
        assert identity.public_key.endswith("remo-web@abcd1234")
        assert identity.private_key_path == state_dir.private_key_path

    def test_missing_state_json_falls_back_to_key_comment(self, state_dir):
        state_dir.write_keypair(deployment_id="wxyz9876")

        identity = load_service_identity(state_dir.settings())

        assert identity is not None
        assert identity.deployment_id == "wxyz9876"
        assert identity.created_at is None


# ---------------------------------------------------------------------------
# Resolved SSH options on WebSettings (T003 / research R6)
# ---------------------------------------------------------------------------


class TestResolvedSshSettings:
    def test_adopted_mode_resolves_web_identity_paths(self, state_dir):
        state_dir.adopted()
        settings = state_dir.settings()
        assert settings.ssh_identity_file == str(state_dir.private_key_path)
        assert settings.ssh_known_hosts_file == str(state_dir.web_identity_dir / "known_hosts")

    def test_mounted_mode_resolves_none(self, state_dir):
        state_dir.mount_configured_user_identity()
        settings = state_dir.settings()
        assert settings.ssh_identity_file is None
        assert settings.ssh_known_hosts_file is None

    def test_unconfigured_mode_resolves_none(self, state_dir):
        state_dir.unconfigured()
        settings = state_dir.settings()
        assert settings.ssh_identity_file is None
        assert settings.ssh_known_hosts_file is None

    def test_api_token_from_env(self, state_dir, monkeypatch):
        monkeypatch.setenv("REMO_WEB_API_TOKEN", "  sekrit-token  ")
        assert state_dir.settings().api_token == "sekrit-token"
        monkeypatch.setenv("REMO_WEB_API_TOKEN", "")
        assert state_dir.settings().api_token == ""
