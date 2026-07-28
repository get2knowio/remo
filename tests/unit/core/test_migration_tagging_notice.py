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
    assert "remo proxmox sync --host <host>" in out
    # Not a menu of every provider -- only what the user actually has.
    assert "incus" not in out
    assert "hetzner" not in out


def test_host_scoped_providers_get_the_host_flag(capsys):
    known_hosts._print_tagging_notice(_report("incus", "hetzner"))
    out = _plain(capsys.readouterr())
    assert "remo incus sync --host <host>" in out
    # hetzner is flat-named: suggesting --host there would be wrong.
    assert "remo hetzner sync\n" in out


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
    assert "remo proxmox sync" in out
