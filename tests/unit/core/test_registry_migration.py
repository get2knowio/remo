"""Migration matrix tests (T015): legacy known_hosts -> registry.json v2.

Covers the full state-transition matrix from data-model.md §6 (S0-S5): all
5 types across their 4/6/7-field legacy variants, garbage/unknown lines,
empty vs missing legacy files, pre-existing backup suffixing, interrupted
(S3) and divergent (S4) both-present resolution, idempotent completed
migration, and the two legacy access-mode variants from research R5.
"""

from __future__ import annotations

import json

from remo_cli.core.config import get_registry_backup_path, get_registry_path
from remo_cli.core.registry import migrate_if_needed, read_registry
from tests.conftest import (
    LEGACY_FIXTURE_LINES,
    build_v2_host_entry,
    legacy_line,
    write_legacy_registry,
    write_v2_registry,
)


def _registry_doc(config_dir) -> dict:
    return json.loads((config_dir / "registry.json").read_text())


def _entry(doc: dict, type_: str, name: str) -> dict:
    return next(e for e in doc["hosts"] if e["type"] == type_ and e["name"] == name)


class TestFieldVariantMigrationMatrix:
    """All 5 types x their 4/6/7-field legacy variants map to the right
    nested v2 fields (contracts/registry-file-v2.md's per-type table)."""

    def test_all_types_and_field_variants(self, tmp_config_dir):
        lines = [
            # incus: instance_id -> incus.host_user; region has no v2 home.
            legacy_line("incus", "h1/c1", "1.1.1.1", "remo"),
            legacy_line("incus", "h1/c2", "1.1.1.2", "remo", "paul", "direct"),
            legacy_line("incus", "h1/c3", "1.1.1.3", "remo", "paul", "direct", "somenode"),
            # proxmox: instance_id -> proxmox.vmid, region -> proxmox.host_user.
            legacy_line("proxmox", "p/c1", "2.2.2.1", "remo"),
            legacy_line("proxmox", "p/c2", "2.2.2.2", "remo", "104", "direct"),
            legacy_line("proxmox", "p/c3", "2.2.2.3", "remo", "105", "direct", "root"),
            # aws: instance_id -> aws.instance_id, region -> aws.region.
            legacy_line("aws", "a1", "3.3.3.1", "remo"),
            legacy_line("aws", "a2", "3.3.3.2", "remo", "i-002", "ssm"),
            legacy_line("aws", "a3", "3.3.3.3", "remo", "i-003", "ssm", "us-west-2"),
            # hetzner: no nested fields, ever.
            legacy_line("hetzner", "h1", "4.4.4.1", "remo"),
            legacy_line(
                "hetzner", "h2", "4.4.4.2", "remo", "ignored_id", "direct", "ignored_region"
            ),
            # ssh: instance_id -> ssh.port (int), region -> ssh.identity_file.
            legacy_line("ssh", "s1", "nas1.lan", "admin"),
            legacy_line("ssh", "s2", "nas2.lan", "admin", "2022", "direct"),
            legacy_line("ssh", "s3", "nas3.lan", "admin", "2023", "direct", "/home/x/id_rsa"),
        ]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == len(lines)
        doc = _registry_doc(tmp_config_dir)

        e = _entry(doc, "incus", "h1/c1")
        assert e["access"] == "direct"
        assert "incus" not in e

        e = _entry(doc, "incus", "h1/c2")
        assert e["access"] == "direct"
        assert e["incus"] == {"host_user": "paul"}

        e = _entry(doc, "incus", "h1/c3")
        assert e["access"] == "direct"
        assert e["incus"] == {"host_user": "paul"}  # region dropped: no v2 home

        e = _entry(doc, "proxmox", "p/c1")
        assert e["access"] == "direct"
        assert "proxmox" not in e

        e = _entry(doc, "proxmox", "p/c2")
        assert e["proxmox"] == {"vmid": "104"}

        e = _entry(doc, "proxmox", "p/c3")
        assert e["proxmox"] == {"vmid": "105", "host_user": "root"}

        e = _entry(doc, "aws", "a1")
        assert e["access"] == "direct"
        assert "aws" not in e

        e = _entry(doc, "aws", "a2")
        assert e["access"] == "ssm"
        assert e["aws"] == {"instance_id": "i-002"}

        e = _entry(doc, "aws", "a3")
        assert e["access"] == "ssm"
        assert e["aws"] == {"instance_id": "i-003", "region": "us-west-2"}

        e = _entry(doc, "hetzner", "h1")
        assert "hetzner" not in e

        e = _entry(doc, "hetzner", "h2")
        assert "hetzner" not in e  # extra fields silently ignored, no error

        e = _entry(doc, "ssh", "s1")
        assert "ssh" not in e

        e = _entry(doc, "ssh", "s2")
        assert e["ssh"] == {"port": 2022}

        e = _entry(doc, "ssh", "s3")
        assert e["ssh"] == {"port": 2023, "identity_file": "/home/x/id_rsa"}


class TestGarbageAndUnknownLines:
    def test_garbage_line_not_migrated_and_preserved_in_backup(self, tmp_config_dir):
        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo"), "this line is garbage"]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert "this line is garbage" in report.skipped_lines
        backup_text = get_registry_backup_path().read_text()
        assert "this line is garbage" in backup_text

        view = read_registry()
        assert len(view.hosts) == 1
        assert view.hosts[0].name == "web1"

    def test_short_line_not_migrated(self, tmp_config_dir):
        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo"), "a:b:c"]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert "a:b:c" in report.skipped_lines
        backup_text = get_registry_backup_path().read_text()
        assert "a:b:c" in backup_text

    def test_unknown_type_line_preserved_not_dropped(self, tmp_config_dir):
        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo"), "docker:mybox:1.2.3.4:remo"]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == 2  # 1 known + 1 unknown, nothing dropped

        view = read_registry()
        assert view.unknown_entries == 1
        doc = _registry_doc(tmp_config_dir)
        docker_entries = [e for e in doc["hosts"] if e["type"] == "docker"]
        assert len(docker_entries) == 1
        assert docker_entries[0]["name"] == "mybox"
        assert docker_entries[0]["host"] == "1.2.3.4"


class TestTolerantMigration:
    """A legacy entry that parses but fails v2 validation is skipped and
    reported, never fatal (FR-009/FR-014) — migrating one bad entry must not
    brick every subsequent CLI command. The original bytes survive in the
    backup regardless."""

    def test_validation_failing_entry_skipped_not_fatal(self, tmp_config_dir):
        bad = legacy_line("ssh", "box", "nas.lan", "admin", "999999", "direct")  # port OOR
        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo"), bad]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == 1  # only the valid hetzner entry
        assert any("ssh:box" in s and "out of range" in s for s in report.skipped_lines)
        # The bad line's original bytes are preserved verbatim in the backup.
        assert bad in get_registry_backup_path().read_text()

        view = read_registry()
        assert [h.name for h in view.hosts] == ["web1"]

    def test_duplicate_type_name_deduped_not_fatal(self, tmp_config_dir):
        lines = [
            legacy_line("hetzner", "dup", "5.5.5.5", "remo"),
            legacy_line("hetzner", "dup", "6.6.6.6", "remo"),  # same (type, name)
        ]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == 1  # first occurrence kept
        assert any("duplicate" in s for s in report.skipped_lines)

        doc = _registry_doc(tmp_config_dir)
        dups = [e for e in doc["hosts"] if e["name"] == "dup"]
        assert len(dups) == 1
        assert dups[0]["host"] == "5.5.5.5"  # first wins


class TestBothPresentResilience:
    """Read-only both-present reads must not depend on the legacy file being
    readable, and non-readonly reads must degrade (not crash) if the legacy
    file becomes unreadable — the concurrent-migration-rename race window."""

    def test_readonly_both_present_ignores_unreadable_legacy(self, tmp_config_dir):
        write_v2_registry(tmp_config_dir, [build_v2_host_entry("hetzner", "web1", "5.5.5.5", "remo")])
        legacy = tmp_config_dir / "known_hosts"
        legacy.write_text(legacy_line("hetzner", "web1", "5.5.5.5", "remo") + "\n")
        legacy.chmod(0o000)
        try:
            view = read_registry(readonly=True)
        finally:
            legacy.chmod(0o600)
        assert [h.name for h in view.hosts] == ["web1"]

    def test_nonreadonly_both_present_degrades_on_unreadable_legacy(self, tmp_config_dir):
        write_v2_registry(tmp_config_dir, [build_v2_host_entry("hetzner", "web1", "5.5.5.5", "remo")])
        legacy = tmp_config_dir / "known_hosts"
        legacy.write_text(legacy_line("hetzner", "web1", "5.5.5.5", "remo") + "\n")
        legacy.chmod(0o000)
        try:
            view = read_registry(readonly=False)  # must not raise RegistryReadError
        finally:
            legacy.chmod(0o600)
        assert [h.name for h in view.hosts] == ["web1"]


class TestEmptyVsMissingLegacyFile:
    def test_empty_known_hosts_file_migrates_to_empty_registry(self, tmp_config_dir):
        (tmp_config_dir / "known_hosts").write_text("")

        report = migrate_if_needed()

        assert report is not None
        assert report.migrated_count == 0
        assert get_registry_path().exists()

        view = read_registry()
        assert view.hosts == []
        assert view.source_format == "v2"

    def test_missing_known_hosts_file_needs_no_migration(self, tmp_config_dir):
        assert not (tmp_config_dir / "known_hosts").exists()

        report = migrate_if_needed()

        assert report is None
        assert not get_registry_path().exists()

        view = read_registry()
        assert view.source_format == "empty"
        assert view.hosts == []
        assert not get_registry_path().exists()  # read alone never creates the file


class TestPreExistingBackupSuffixing:
    def test_existing_backup_is_never_clobbered(self, tmp_config_dir):
        pre_existing = tmp_config_dir / "known_hosts.v1.bak"
        pre_existing.write_text("PRE-EXISTING BACKUP CONTENT\n")

        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo")]
        write_legacy_registry(tmp_config_dir, lines)

        report = migrate_if_needed()

        assert report is not None
        assert report.backup_path.name == "known_hosts.v1.bak.1"
        assert pre_existing.read_text() == "PRE-EXISTING BACKUP CONTENT\n"
        assert report.backup_path.read_text() == "\n".join(lines) + "\n"


class TestInterruptedMigrationConvergence:
    """S3 (data-model.md §6): registry.json and known_hosts both present with
    equivalent content, as if migration wrote v2 then crashed before the
    rename. read_registry() must silently complete the rename."""

    def test_equivalent_both_present_completes_rename_silently(self, tmp_config_dir):
        lines = [legacy_line("hetzner", "web1", "5.5.5.5", "remo")]
        write_legacy_registry(tmp_config_dir, lines)
        entry = build_v2_host_entry("hetzner", "web1", "5.5.5.5", "remo", access="direct")
        write_v2_registry(tmp_config_dir, [entry])

        view = read_registry(readonly=False)

        assert view.warnings == []
        assert len(view.hosts) == 1
        assert view.hosts[0].name == "web1"
        assert not (tmp_config_dir / "known_hosts").exists()
        assert get_registry_backup_path().exists()


class TestDivergentBothPresent:
    """S4 (data-model.md §6): registry.json and known_hosts both present with
    differing content. v2 wins, never merged, warning surfaced, known_hosts
    left in place (no automatic resolution)."""

    def test_divergent_warns_v2_wins_never_merges(self, tmp_config_dir):
        write_legacy_registry(
            tmp_config_dir, [legacy_line("hetzner", "web1", "5.5.5.5", "remo")]
        )
        write_v2_registry(
            tmp_config_dir,
            [build_v2_host_entry("hetzner", "web1", "9.9.9.9", "remo", access="direct")],
        )

        view = read_registry(readonly=False)

        assert any("differ" in w for w in view.warnings)
        assert len(view.hosts) == 1
        assert view.hosts[0].host == "9.9.9.9"  # v2 wins
        assert (tmp_config_dir / "known_hosts").exists()  # never renamed away
        assert (tmp_config_dir / "registry.json").exists()


class TestCompletedMigrationNeverReruns:
    def test_second_call_is_a_true_no_op(self, tmp_config_dir):
        write_legacy_registry(
            tmp_config_dir, [legacy_line("hetzner", "web1", "5.5.5.5", "remo")]
        )
        report1 = migrate_if_needed()
        assert report1 is not None
        backup_path = report1.backup_path
        mtime_before = backup_path.stat().st_mtime

        report2 = migrate_if_needed()

        assert report2 is None
        assert backup_path.stat().st_mtime == mtime_before
        assert not (tmp_config_dir / "known_hosts.v1.bak.1").exists()

        read_registry(readonly=False)  # a normal read must not re-trigger anything
        assert not (tmp_config_dir / "known_hosts.v1.bak.1").exists()
        assert backup_path.stat().st_mtime == mtime_before


class TestLegacyAccessModeVariants:
    """research R5 — the two legacy access-mode variants no current writer
    produces but old files can contain. Both must map to access: "direct"
    (only aws may ever be "ssm"), despite superficially looking SSM-ish."""

    def test_incus_implicit_ssm_maps_to_direct(self, tmp_config_dir):
        write_legacy_registry(tmp_config_dir, [LEGACY_FIXTURE_LINES["incus_implicit_ssm"]])

        report = migrate_if_needed()

        assert report is not None
        doc = _registry_doc(tmp_config_dir)
        e = _entry(doc, "incus", "old/box")
        assert e["access"] == "direct"

    def test_proxmox_empty_access_maps_to_direct(self, tmp_config_dir):
        write_legacy_registry(tmp_config_dir, [LEGACY_FIXTURE_LINES["proxmox_empty_access"]])

        report = migrate_if_needed()

        assert report is not None
        doc = _registry_doc(tmp_config_dir)
        e = _entry(doc, "proxmox", "old/pct")
        assert e["access"] == "direct"
