"""Unit tests for remo.core.picker module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core.picker import pick_environment
from remo_cli.models.host import KnownHost


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_host():
    return KnownHost(type="hetzner", name="myhost", host="1.2.3.4", user="remo")


@pytest.fixture
def multiple_hosts():
    return [
        KnownHost(type="hetzner", name="web", host="1.1.1.1", user="remo"),
        KnownHost(type="aws", name="dev", host="2.2.2.2", user="remo"),
        KnownHost(type="incus", name="local/container", host="3.3.3.3", user="remo"),
    ]


# ---------------------------------------------------------------------------
# pick_environment() - empty list
# ---------------------------------------------------------------------------


class TestPickEnvironmentEmpty:
    """Tests for pick_environment() with no hosts."""

    def test_empty_list_raises_system_exit(self):
        """An empty host list raises SystemExit."""
        with pytest.raises(SystemExit, match="No remo environments registered"):
            pick_environment([])


# ---------------------------------------------------------------------------
# pick_environment() - single host
# ---------------------------------------------------------------------------


class TestPickEnvironmentSingleHost:
    """Tests for pick_environment() with exactly one host."""

    def test_single_host_returns_directly(self, single_host):
        """A single host is returned without invoking InquirerPy."""
        result = pick_environment([single_host])
        assert result is single_host

    def test_single_host_does_not_import_inquirerpy(self, single_host, mocker):
        """With a single host, InquirerPy should NOT be imported."""
        # If InquirerPy were imported, it would go through __import__.
        # We verify by patching builtins.__import__ to track calls.
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__
        import_calls = []

        def tracking_import(name, *args, **kwargs):
            import_calls.append(name)
            return original_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=tracking_import)

        result = pick_environment([single_host])

        assert result is single_host
        assert "InquirerPy" not in import_calls


# ---------------------------------------------------------------------------
# pick_environment() - multiple hosts
# ---------------------------------------------------------------------------


class TestPickEnvironmentMultipleHosts:
    """Tests for pick_environment() with multiple hosts."""

    def test_multiple_hosts_invokes_inquirer_fuzzy(self, multiple_hosts, mocker):
        """With multiple hosts, InquirerPy fuzzy selector is invoked."""
        expected_host = multiple_hosts[1]

        # Mock the InquirerPy module's inquirer.fuzzy().execute()
        mock_execute = MagicMock(return_value=expected_host)
        mock_fuzzy = MagicMock()
        mock_fuzzy.execute = mock_execute

        mock_inquirer = MagicMock()
        mock_inquirer.fuzzy.return_value = mock_fuzzy

        mocker.patch.dict(
            "sys.modules",
            {"InquirerPy": MagicMock(inquirer=mock_inquirer)},
        )
        # We need to patch the import inside the function.  Since
        # pick_environment does ``from InquirerPy import inquirer`` inside
        # the function body, we reload or call it so the patched module is used.
        # The simplest approach: patch the inquirer at module import level.

        # Actually, since the function does a lazy import inside the body,
        # we can mock the module in sys.modules before the call.
        import sys as _sys
        mock_inquirer_module = MagicMock()
        mock_inquirer_module.inquirer = mock_inquirer
        _sys.modules["InquirerPy"] = mock_inquirer_module

        try:
            result = pick_environment(multiple_hosts)
        finally:
            # Clean up the mock from sys.modules
            _sys.modules.pop("InquirerPy", None)

        assert result is expected_host
        mock_inquirer.fuzzy.assert_called_once()
        call_kwargs = mock_inquirer.fuzzy.call_args
        # Verify that choices were passed
        choices = call_kwargs[1]["choices"] if "choices" in call_kwargs[1] else call_kwargs[0][0]
        assert len(choices) == 3

    def test_multiple_hosts_choices_format(self, multiple_hosts, mocker):
        """Choices passed to fuzzy contain display_name and host info."""
        expected_host = multiple_hosts[0]

        mock_execute = MagicMock(return_value=expected_host)
        mock_fuzzy = MagicMock()
        mock_fuzzy.execute = mock_execute
        mock_inquirer = MagicMock()
        mock_inquirer.fuzzy.return_value = mock_fuzzy

        import sys as _sys
        mock_inquirer_module = MagicMock()
        mock_inquirer_module.inquirer = mock_inquirer
        _sys.modules["InquirerPy"] = mock_inquirer_module

        try:
            pick_environment(multiple_hosts)
        finally:
            _sys.modules.pop("InquirerPy", None)

        call_kwargs = mock_inquirer.fuzzy.call_args[1]
        choices = call_kwargs["choices"]

        # Check format: "type: display_name (host)"
        assert choices[0]["name"] == "hetzner: web (1.1.1.1)"
        assert choices[0]["value"] is multiple_hosts[0]

        # Incus host with "/" in name uses display_name property
        incus_host = multiple_hosts[2]
        assert choices[2]["name"] == f"incus: {incus_host.display_name} (3.3.3.3)"

    def test_keyboard_interrupt_raises_system_exit(self, multiple_hosts, mocker):
        """KeyboardInterrupt during fuzzy selection raises SystemExit(0)."""
        mock_fuzzy = MagicMock()
        mock_fuzzy.execute.side_effect = KeyboardInterrupt
        mock_inquirer = MagicMock()
        mock_inquirer.fuzzy.return_value = mock_fuzzy

        import sys as _sys
        mock_inquirer_module = MagicMock()
        mock_inquirer_module.inquirer = mock_inquirer
        _sys.modules["InquirerPy"] = mock_inquirer_module

        try:
            with pytest.raises(SystemExit) as exc_info:
                pick_environment(multiple_hosts)
            assert exc_info.value.code == 0
        finally:
            _sys.modules.pop("InquirerPy", None)

    def test_none_result_raises_system_exit(self, multiple_hosts, mocker):
        """When fuzzy returns None (user cancelled), raises SystemExit(0)."""
        mock_fuzzy = MagicMock()
        mock_fuzzy.execute.return_value = None
        mock_inquirer = MagicMock()
        mock_inquirer.fuzzy.return_value = mock_fuzzy

        import sys as _sys
        mock_inquirer_module = MagicMock()
        mock_inquirer_module.inquirer = mock_inquirer
        _sys.modules["InquirerPy"] = mock_inquirer_module

        try:
            with pytest.raises(SystemExit) as exc_info:
                pick_environment(multiple_hosts)
            assert exc_info.value.code == 0
        finally:
            _sys.modules.pop("InquirerPy", None)
