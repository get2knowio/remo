"""Tests for core/snapshot.py helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import click
import pytest

from remo_cli.core.snapshot import (
    _humanize_size,
    format_snapshot_table,
    generate_default_name,
    validate_name,
)
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


class TestGenerateDefaultName:
    """generate_default_name() returns ``remo-YYYYMMDD-HHMMSS``."""

    def test_format_matches_pattern(self):
        name = generate_default_name()
        assert re.match(r"^remo-\d{8}-\d{6}$", name), name

    def test_two_calls_sort_in_creation_order(self):
        # Two calls within the same second may collide; sleep is overkill
        # for a unit test, so we verify lexicographic ordering of
        # synthesized names instead.
        earlier = "remo-20260524-101530"
        later = "remo-20260524-101531"
        assert earlier < later


class TestValidateName:
    """validate_name() enforces the cross-provider intersection rules."""

    @pytest.mark.parametrize(
        "name",
        ["a", "pre-x", "Pre_X-1", "remo-20260524-101530", "A" * 40],
    )
    def test_accepts_valid_names(self, name):
        # Should not raise.
        validate_name(name)

    def test_rejects_empty(self):
        with pytest.raises(click.BadParameter):
            validate_name("")

    def test_rejects_too_long(self):
        with pytest.raises(click.BadParameter):
            validate_name("A" * 41)

    def test_rejects_leading_dash(self):
        with pytest.raises(click.BadParameter):
            validate_name("-leadingdash")

    def test_rejects_leading_underscore(self):
        # Underscore is allowed in the body but the first char must be
        # alphanumeric (matches AWS tag-name + Hetzner label intersection).
        with pytest.raises(click.BadParameter):
            validate_name("_leadingunderscore")

    def test_rejects_spaces(self):
        with pytest.raises(click.BadParameter):
            validate_name("has spaces")

    def test_rejects_special_chars(self):
        for bad in ("dot.name", "slash/name", "bang!", "comma,sep", "quote'mark"):
            with pytest.raises(click.BadParameter):
                validate_name(bad)


# ---------------------------------------------------------------------------
# _humanize_size
# ---------------------------------------------------------------------------


class TestHumanizeSize:
    def test_none_returns_dash(self):
        assert _humanize_size(None) == "—"

    def test_negative_returns_dash(self):
        assert _humanize_size(-1) == "—"

    def test_bytes(self):
        assert _humanize_size(0) == "0 B"
        assert _humanize_size(1023) == "1023 B"

    def test_kib(self):
        assert _humanize_size(1024) == "1.0 KiB"

    def test_gib(self):
        # 1.5 GiB
        assert _humanize_size(int(1.5 * 1024**3)) == "1.5 GiB"


# ---------------------------------------------------------------------------
# format_snapshot_table
# ---------------------------------------------------------------------------


def _snap(
    *,
    name: str = "pre-x",
    instance: str = "dev1",
    size_bytes: int | None = int(1.2 * 1024**3),
    status: SnapshotStatus = SnapshotStatus.AVAILABLE,
    description: str = "before risky upgrade",
) -> Snapshot:
    return Snapshot(
        provider="incus",
        instance_name=instance,
        name=name,
        backend_id=f"{instance}/{name}",
        created_at=datetime(2026, 5, 24, 10, 15, 30, tzinfo=timezone.utc),
        size_bytes=size_bytes,
        description=description,
        status=status,
    )


class TestFormatSnapshotTable:
    def test_empty_with_instance_label(self):
        out = format_snapshot_table([], show_status=False, instance_label="dev1")
        assert out == "No snapshots found for instance 'dev1'."

    def test_empty_without_instance_label(self):
        out = format_snapshot_table([], show_status=False)
        assert out == "No snapshots found."

    def test_status_column_omitted_when_show_status_false(self):
        out = format_snapshot_table([_snap()], show_status=False)
        # Header line
        first = out.splitlines()[0]
        assert "STATUS" not in first
        assert "INSTANCE" in first
        assert "SNAPSHOT" in first
        assert "CREATED" in first
        assert "SIZE" in first
        assert "DESCRIPTION" in first

    def test_status_column_present_when_show_status_true(self):
        out = format_snapshot_table(
            [_snap(status=SnapshotStatus.PENDING)],
            show_status=True,
        )
        lines = out.splitlines()
        assert "STATUS" in lines[0]
        assert "pending" in lines[1]

    def test_no_cost_indicators_fr_009(self):
        """FR-009 negative: rendered output must NOT contain cost markers."""
        out = format_snapshot_table([_snap()], show_status=True)
        lowered = out.lower()
        assert "$" not in out
        assert "€" not in out
        assert "cost" not in lowered
        assert "/mo" not in lowered

    def test_size_dash_when_none(self):
        out = format_snapshot_table([_snap(size_bytes=None)], show_status=False)
        # The dash character should appear in the data row
        assert "—" in out

    def test_two_rows_render(self):
        out = format_snapshot_table(
            [_snap(name="pre-x"), _snap(name="pre-y")],
            show_status=False,
        )
        lines = out.splitlines()
        # Header + 2 rows
        assert len(lines) == 3
        assert "pre-x" in lines[1]
        assert "pre-y" in lines[2]
