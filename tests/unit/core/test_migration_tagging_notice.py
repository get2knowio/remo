"""The one-time post-migration tagging notice (see
core/known_hosts._print_tagging_notice).

Managed tagging (feature 013) and registry v2 (feature 015) are unrelated
features that ship in the same release, so every instance in a migrating
registry predates tagging. Migration is the one event that reaches exactly
that population exactly once, which is why the notice rides along there
instead of being backfilled implicitly by `remo shell`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remo_cli.core import known_hosts
from remo_cli.core.provider_registry import (
    ConnectionSpec,
    NameFormat,
    ProviderDescriptor,
    temporary_registration,
)
from remo_cli.core.registry import MigrationReport


@pytest.fixture(autouse=True)
def _reset_notice_latch():
    """The notice is latched to fire once per process; unlatch per test."""
    known_hosts._migration_notice_shown = False
    yield
    known_hosts._migration_notice_shown = False


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(captured) -> str:
    """print_info colorizes; assertions are about wording, not escapes."""
    return _ANSI.sub("", captured.out)


def _report(*types: str) -> MigrationReport:
    return MigrationReport(
        migrated_count=len(types),
        backup_path=Path("known_hosts.v1.bak"),
        skipped_lines=[],
        migrated_types=tuple(sorted(types)),
    )


def test_names_only_the_providers_present(capsys):
    known_hosts._print_tagging_notice(_report("proxmox"))
    out = _plain(capsys.readouterr())
    assert "remo proxmox tag <name> --host <host>" in out
    # Not a menu of every provider -- only what the user actually has.
    assert "incus" not in out
    assert "hetzner" not in out


def _flat_taggable(type_name: str = "flatcloud") -> ProviderDescriptor:
    """A flat-named provider that supports the managed marker -- no built-in
    has that shape now that hetzner is a plugin."""
    return ProviderDescriptor(
        type_name=type_name,
        display_name=type_name.title(),
        default_instance_name=type_name,
        name_format=NameFormat.FLAT,
        registry_fields=(),
        connection=ConnectionSpec(),
        implementation=f"{type_name}_provider.provider",
        supports_managed_marker=True,
    )


def test_host_scoped_providers_get_the_host_flag(capsys):
    with temporary_registration(_flat_taggable("flatcloud")):
        known_hosts._print_tagging_notice(_report("incus", "flatcloud"))
    out = _plain(capsys.readouterr())
    assert "remo incus tag <name> --host <host>" in out
    # flatcloud is flat-named: suggesting --host there would be wrong.
    assert "remo flatcloud tag <name>\n" in out


def test_uninstalled_provider_type_is_left_out(capsys):
    # Entries of a plugin type can migrate on a machine without that plugin
    # (vultr stands in for one); there is no `remo vultr` group to point at,
    # so it is not named.
    known_hosts._print_tagging_notice(_report("incus", "vultr"))
    out = _plain(capsys.readouterr())
    assert "remo incus tag <name> --host <host>" in out
    assert "vultr" not in out


def test_silent_when_no_provider_supports_tagging(capsys):
    # aws has no managed-marker backfill; ssh is not a provider at all.
    known_hosts._print_tagging_notice(_report("aws", "ssh"))
    assert _plain(capsys.readouterr()) == ""


def test_silent_on_an_empty_migration(capsys):
    known_hosts._print_tagging_notice(_report())
    assert _plain(capsys.readouterr()) == ""


def test_hedges_because_tag_state_is_unknowable_offline(capsys):
    """We cannot know an instance's tag state without reaching the provider --
    which is the access this whole change removes. The wording must not claim
    certainty it does not have."""
    known_hosts._print_tagging_notice(_report("proxmox"))
    out = _plain(capsys.readouterr())
    assert "may not be tagged" in out
    assert "are not tagged" not in out


def test_rides_along_with_the_main_migration_notice(capsys):
    known_hosts._print_migration_notice(_report("proxmox"))
    out = _plain(capsys.readouterr())
    assert "Migrated 1 registry entry" in out
    assert "remo proxmox tag <name>" in out
