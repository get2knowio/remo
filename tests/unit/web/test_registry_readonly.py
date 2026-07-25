"""Web readonly registry tests (T025, US3).

Covers quickstart.md §6: `remo web check`/discovery against a read-only
volume must see the same host set in either registry format, with zero
writes/mkdirs, and must degrade gracefully (never crash) on a per-entry
malformed record or on a structurally newer-format `registry.json` -- the
latter mapping all the way through to `web.state.detect_state()` returning
`ConfigurationState.BROKEN` (T023).
"""

from __future__ import annotations

import os

import pytest

from remo_cli.core.registry import RegistryNewerVersionError, read_registry
from remo_cli.web.state import ConfigurationState, detect_state
from tests.conftest import (
    LEGACY_FIXTURE_LINES,
    build_v2_host_entry,
    write_legacy_registry,
    write_v2_registry,
)

skip_if_root = pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")

# The two "legacy access-mode variant" fixture entries exist to pin the
# migration mapper's type-first rule (research R5) and are covered there
# (test_registry_migration.py); the readonly parity/no-side-effects tests
# here only need the five ordinary types.
_STANDARD_LEGACY_TYPES = ("incus", "proxmox", "aws", "hetzner", "ssh")


def _host_key(h) -> tuple:
    """A hashable/sortable projection of every `KnownHost` field."""
    return (h.type, h.name, h.host, h.user, h.instance_id, h.access_mode, h.region)


def _host_keys(hosts) -> set[tuple]:
    return {_host_key(h) for h in hosts}


def _snapshot_mtimes(directory) -> dict[str, float]:
    return {p.name: p.stat().st_mtime for p in directory.iterdir()}


def _snapshot_names(directory) -> set[str]:
    return {p.name for p in directory.iterdir()}


# ---------------------------------------------------------------------------
# Both formats on a read-only (chmod 555) REMO_HOME: no side effects
# ---------------------------------------------------------------------------


@skip_if_root
class TestReadonlyNoSideEffects:
    def test_legacy_format_on_readonly_dir(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "remo"
        config_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))

        lines = [LEGACY_FIXTURE_LINES[t] for t in _STANDARD_LEGACY_TYPES]
        write_legacy_registry(config_dir, lines)

        names_before = _snapshot_names(config_dir)
        mtimes_before = _snapshot_mtimes(config_dir)

        config_dir.chmod(0o555)
        try:
            view = read_registry(readonly=True)
        finally:
            config_dir.chmod(0o755)

        assert view.source_format == "legacy"
        assert len(view.hosts) == len(_STANDARD_LEGACY_TYPES)
        assert {h.type for h in view.hosts} == set(_STANDARD_LEGACY_TYPES)

        # No side effects: nothing created, nothing touched.
        assert _snapshot_names(config_dir) == names_before
        assert _snapshot_mtimes(config_dir) == mtimes_before
        assert not (config_dir / "registry.json").exists()
        assert not (config_dir / "registry.lock").exists()

    def test_v2_format_on_readonly_dir(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "remo"
        config_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))

        entries = [
            build_v2_host_entry("incus", "nuc/dev1", "dev1.incus", "remo", host_user="paul"),
            build_v2_host_entry(
                "aws",
                "buildbox",
                "203.0.113.7",
                "remo",
                access="ssm",
                instance_id="i-0abc123",
                region="us-east-1",
            ),
        ]
        write_v2_registry(config_dir, entries)

        names_before = _snapshot_names(config_dir)
        mtimes_before = _snapshot_mtimes(config_dir)

        config_dir.chmod(0o555)
        try:
            view = read_registry(readonly=True)
        finally:
            config_dir.chmod(0o755)

        assert view.source_format == "v2"
        assert {h.name for h in view.hosts} == {"nuc/dev1", "buildbox"}

        assert _snapshot_names(config_dir) == names_before
        assert _snapshot_mtimes(config_dir) == mtimes_before
        assert not (config_dir / "registry.lock").exists()


# ---------------------------------------------------------------------------
# CLI-vs-web host-set parity
# ---------------------------------------------------------------------------


class TestCliVsWebParity:
    def test_legacy_fixture_parses_identically_readonly_and_migrating(
        self, tmp_path, monkeypatch
    ):
        lines = [LEGACY_FIXTURE_LINES[t] for t in _STANDARD_LEGACY_TYPES]

        # "Web" read: readonly=True, in its own REMO_HOME -- never migrates.
        web_dir = tmp_path / "web-remo"
        web_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(web_dir))
        write_legacy_registry(web_dir, lines)
        web_view = read_registry(readonly=True)

        # "CLI" read: readonly=False, in a SEPARATE REMO_HOME so migration's
        # side effect (rewriting registry.json + renaming the backup) can't
        # interfere with the web read above.
        cli_dir = tmp_path / "cli-remo"
        cli_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(cli_dir))
        write_legacy_registry(cli_dir, lines)
        cli_view = read_registry(readonly=False)

        assert web_view.source_format == "legacy"
        assert cli_view.source_format == "v2"  # migrated on the non-readonly read
        assert (cli_dir / "registry.json").exists()
        assert not (cli_dir / "known_hosts").exists()  # renamed to the .v1.bak backup

        assert _host_keys(web_view.hosts) == _host_keys(cli_view.hosts)


# ---------------------------------------------------------------------------
# Single malformed entry degrades to a warning, not a crash
# ---------------------------------------------------------------------------


class TestMalformedEntryDegrades:
    def test_v2_entry_missing_user_degrades_to_warning(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "remo"
        config_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))

        good_entry = build_v2_host_entry("incus", "nuc/dev1", "dev1.incus", "remo")
        bad_entry = {
            "type": "incus",
            "name": "nuc/dev2",
            "host": "dev2.incus",
            # "user" is missing entirely.
            "access": "direct",
        }
        write_v2_registry(config_dir, [good_entry, bad_entry])

        view = read_registry(readonly=True)

        assert len(view.hosts) == 1
        assert view.hosts[0].name == "nuc/dev1"
        assert any("nuc/dev2" in w or "index" in w for w in view.warnings)


# ---------------------------------------------------------------------------
# Newer-version registry.json -> RegistryNewerVersionError -> BROKEN state
# ---------------------------------------------------------------------------


class TestNewerVersionMapsToBroken:
    def test_read_registry_raises_newer_version_error(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "remo"
        config_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))
        write_v2_registry(config_dir, [], version=99)

        with pytest.raises(RegistryNewerVersionError):
            read_registry(readonly=True)

    def test_detect_state_reports_broken_for_newer_version_registry(
        self, tmp_path, monkeypatch
    ):
        config_dir = tmp_path / "remo"
        config_dir.mkdir()
        user_home = tmp_path / "user-home"
        user_home.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))
        monkeypatch.setenv("HOME", str(user_home))
        monkeypatch.delenv("REMO_WEB_SSH_IDENTITY_FILE", raising=False)
        write_v2_registry(config_dir, [], version=99)

        assert detect_state() is ConfigurationState.BROKEN
