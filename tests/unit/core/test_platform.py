"""Tests for remo_cli.core.platform — the POSIX-only support gate."""

from __future__ import annotations

import pytest

from remo_cli.core.platform import (
    UNSUPPORTED_PLATFORM_MESSAGE,
    UNSUPPORTED_PLATFORMS,
    WSL_DOCS_URL,
    is_supported_platform,
)


class TestIsSupportedPlatform:
    @pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd", "cygwin"])
    def test_posix_platforms_are_supported(self, platform: str) -> None:
        assert is_supported_platform(platform) is True

    def test_native_windows_is_not_supported(self) -> None:
        assert is_supported_platform("win32") is False

    def test_defaults_to_the_running_interpreter(self) -> None:
        """No argument reads sys.platform. The suite itself only ever runs on a
        supported platform, so this is the one assertion available."""
        assert is_supported_platform() is True

    def test_cygwin_is_deliberately_not_gated(self) -> None:
        """Cygwin provides fcntl. Untested is not the same as known-broken, so
        it must not creep into the blocklist alongside win32."""
        assert UNSUPPORTED_PLATFORMS == frozenset({"win32"})


class TestUnsupportedPlatformMessage:
    def test_names_the_supported_platforms(self) -> None:
        assert "Linux or macOS" in UNSUPPORTED_PLATFORM_MESSAGE

    def test_explains_the_underlying_cause(self) -> None:
        """The user's first encounter with this is a ModuleNotFoundError naming
        fcntl, so the message has to connect itself to that symptom."""
        assert "fcntl" in UNSUPPORTED_PLATFORM_MESSAGE

    def test_gives_an_actionable_remedy(self) -> None:
        assert "WSL2" in UNSUPPORTED_PLATFORM_MESSAGE
        assert "wsl --install" in UNSUPPORTED_PLATFORM_MESSAGE
        assert "uv tool install remo-cli" in UNSUPPORTED_PLATFORM_MESSAGE
        assert WSL_DOCS_URL in UNSUPPORTED_PLATFORM_MESSAGE

    def test_is_prefixed_like_every_other_cli_error(self) -> None:
        assert UNSUPPORTED_PLATFORM_MESSAGE.startswith("Error: ")
