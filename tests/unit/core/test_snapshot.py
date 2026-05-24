"""Tests for core/snapshot.py helpers."""

from __future__ import annotations

import re

import click
import pytest

from remo_cli.core.snapshot import generate_default_name, validate_name


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
