"""Hetzner case of remo's post-migration tagging notice (moved from remo's
tests/unit/core/test_migration_tagging_notice.py when hetzner became a
plugin): hetzner supports the managed marker and is flat-named, so it is
named without a ``--host`` flag."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from remo_cli.core import known_hosts
from remo_cli.core.registry import MigrationReport

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def _reset_notice_latch():
    known_hosts._migration_notice_shown = False
    yield
    known_hosts._migration_notice_shown = False


def test_hetzner_is_named_without_the_host_flag(capsys):
    report = MigrationReport(
        migrated_count=2,
        backup_path=Path("known_hosts.v1.bak"),
        skipped_lines=[],
        migrated_types=("hetzner", "incus"),
    )
    known_hosts._print_tagging_notice(report)
    out = _ANSI.sub("", capsys.readouterr().out)
    assert "remo incus tag <name> --host <host>" in out
    # hetzner is flat-named: suggesting --host there would be wrong.
    assert "remo hetzner tag <name>\n" in out
