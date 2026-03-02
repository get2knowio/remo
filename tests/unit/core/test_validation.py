"""Tests for remo.core.validation module."""

from __future__ import annotations

import pytest
import click

from remo_cli.core.validation import (
    ALL_TOOLS,
    build_tool_args,
    validate_name,
    validate_port,
    validate_region,
    validate_tool_name,
)


class TestValidateName:
    """Tests for validate_name()."""

    @pytest.mark.parametrize(
        "name",
        [
            "myhost",
            "host1",
            "my-host",
            "my.host",
            "my_host",
            "my/host",
            "a",
            "A",
            "0start",
            "9start",
            "abc123",
            "a.b-c_d/e",
        ],
    )
    def test_valid_names_accepted(self, name):
        # Should not raise
        validate_name(name)

    def test_empty_string_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_name("")

    @pytest.mark.parametrize(
        "name",
        [
            "-starts-with-dash",
            ".starts-with-dot",
            "_starts-with-underscore",
            "/starts-with-slash",
        ],
    )
    def test_starting_with_special_char_rejected(self, name):
        with pytest.raises(click.BadParameter):
            validate_name(name)

    def test_name_longer_than_63_chars_rejected(self):
        long_name = "a" * 64
        with pytest.raises(click.BadParameter):
            validate_name(long_name)

    def test_name_exactly_63_chars_accepted(self):
        name = "a" * 63
        # Should not raise
        validate_name(name)

    def test_name_with_spaces_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_name("my host")

    def test_name_with_special_chars_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_name("my@host")

    def test_custom_label_in_error_message(self):
        with pytest.raises(click.BadParameter, match="container"):
            validate_name("", label="container")

    def test_error_message_includes_invalid_value(self):
        with pytest.raises(click.BadParameter, match="-bad"):
            validate_name("-bad")


class TestValidatePort:
    """Tests for validate_port()."""

    def test_min_valid_port(self):
        # Should not raise
        validate_port(1)

    def test_max_valid_port(self):
        # Should not raise
        validate_port(65535)

    def test_common_ports(self):
        for port in [22, 80, 443, 8080, 3000]:
            validate_port(port)

    def test_zero_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_port(0)

    def test_negative_port_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_port(-1)

    def test_port_above_65535_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_port(65536)

    def test_non_int_string_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_port("80")  # type: ignore[arg-type]

    def test_non_int_float_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_port(80.5)  # type: ignore[arg-type]

    def test_error_message_includes_value(self):
        with pytest.raises(click.BadParameter, match="65536"):
            validate_port(65536)


class TestValidateRegion:
    """Tests for validate_region()."""

    @pytest.mark.parametrize(
        "region",
        [
            "us-west-2",
            "us-east-1",
            "eu-central-1",
            "ap-southeast-1",
            "sa-east-1",
        ],
    )
    def test_valid_aws_regions(self, region):
        # Should not raise
        validate_region(region)

    def test_empty_string_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_region("")

    @pytest.mark.parametrize(
        "region",
        [
            "invalid",
            "us-west",
            "us-west-",
            "US-WEST-2",
            "us-2-west",
            "us-west-2a",
            "123-abc-1",
        ],
    )
    def test_invalid_region_formats_rejected(self, region):
        with pytest.raises(click.BadParameter):
            validate_region(region)

    def test_error_message_includes_value(self):
        with pytest.raises(click.BadParameter, match="badregion"):
            validate_region("badregion")

    def test_error_message_mentions_aws_format(self):
        with pytest.raises(click.BadParameter, match="AWS region format"):
            validate_region("invalid")


class TestValidateToolName:
    """Tests for validate_tool_name()."""

    @pytest.mark.parametrize("tool", list(ALL_TOOLS))
    def test_all_valid_tools_accepted(self, tool):
        # Should not raise
        validate_tool_name(tool)

    def test_unknown_tool_rejected(self):
        with pytest.raises(click.BadParameter):
            validate_tool_name("nonexistent_tool")

    def test_error_message_lists_valid_tools(self):
        with pytest.raises(click.BadParameter, match="docker"):
            validate_tool_name("bad_tool")

    def test_error_message_includes_invalid_name(self):
        with pytest.raises(click.BadParameter, match="bad_tool"):
            validate_tool_name("bad_tool")

    def test_custom_flag_in_param_hint(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_tool_name("bad", flag="--only")
        assert exc_info.value.param_hint == "--only"

    def test_default_flag_is_tools(self):
        with pytest.raises(click.BadParameter) as exc_info:
            validate_tool_name("bad")
        assert exc_info.value.param_hint == "--tools"


class TestBuildToolArgs:
    """Tests for build_tool_args()."""

    def test_only_enables_specified_disables_others(self):
        args = build_tool_args(only=("docker",), skip=())
        # docker should be true, all others should be false
        assert "-e" in args
        assert "configure_docker=true" in args

        for tool in ALL_TOOLS:
            if tool != "docker":
                assert f"configure_{tool}=false" in args

    def test_only_multiple_tools(self):
        args = build_tool_args(only=("docker", "nodejs"), skip=())
        assert "configure_docker=true" in args
        assert "configure_nodejs=true" in args
        for tool in ALL_TOOLS:
            if tool not in ("docker", "nodejs"):
                assert f"configure_{tool}=false" in args

    def test_skip_disables_specified_only(self):
        args = build_tool_args(only=(), skip=("docker",))
        assert "configure_docker=false" in args
        # Other tools should NOT appear in args
        for tool in ALL_TOOLS:
            if tool != "docker":
                assert f"configure_{tool}=true" not in args
                assert f"configure_{tool}=false" not in args

    def test_skip_multiple_tools(self):
        args = build_tool_args(only=(), skip=("docker", "fzf"))
        assert "configure_docker=false" in args
        assert "configure_fzf=false" in args

    def test_empty_only_and_skip_returns_empty(self):
        args = build_tool_args(only=(), skip=())
        assert args == []

    def test_args_use_e_flag_format(self):
        args = build_tool_args(only=("docker",), skip=())
        # Every other element starting from 0 should be "-e"
        e_flags = [args[i] for i in range(0, len(args), 2)]
        values = [args[i] for i in range(1, len(args), 2)]
        assert all(flag == "-e" for flag in e_flags)
        assert all("configure_" in val for val in values)

    def test_only_with_invalid_tool_raises(self):
        with pytest.raises(click.BadParameter):
            build_tool_args(only=("nonexistent",), skip=())

    def test_skip_with_invalid_tool_raises(self):
        with pytest.raises(click.BadParameter):
            build_tool_args(only=(), skip=("nonexistent",))

    def test_only_all_tools(self):
        args = build_tool_args(only=ALL_TOOLS, skip=())
        for tool in ALL_TOOLS:
            assert f"configure_{tool}=true" in args
