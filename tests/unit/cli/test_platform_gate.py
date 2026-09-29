"""Tests for the native-Windows startup gate in remo_cli.cli.main.

The gate's whole value is *where* it runs, not just what it prints: importing
this module registers the command groups, and that import chain reaches
core/registry.py's module-level `import fcntl`. On Windows that raises before
Click exists, so anything downstream of it -- a group callback, a `main()`
try/except -- is unreachable. Hence the source-order assertion below.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

import remo_cli.cli.main as main_module
from remo_cli.core.platform import UNSUPPORTED_PLATFORM_MESSAGE


def _main_source() -> str:
    source_file = inspect.getsourcefile(main_module)
    assert source_file is not None
    return Path(source_file).read_text(encoding="utf-8")


def _module_level_call_line(source: str, call: str) -> int:
    """1-based line of the unindented, bare `call` statement."""
    matches = [
        lineno
        for lineno, line in enumerate(source.splitlines(), start=1)
        if line == call
    ]
    assert len(matches) == 1, f"expected exactly one module-level `{call}`, found {matches}"
    return matches[0]


class TestEnforceSupportedPlatform:
    def test_returns_silently_on_a_supported_platform(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(main_module, "is_supported_platform", lambda: True)

        main_module._enforce_supported_platform()

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_exits_1_on_native_windows(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit 1 = failure, per the CLI's exit-code contract (0/1/3). Patches
        the predicate rather than sys.platform: faking sys.platform in-process
        sends Click's own `sys.platform.startswith("win")` branches into
        msvcrt, which does not exist here."""
        monkeypatch.setattr(main_module, "is_supported_platform", lambda: False)

        with pytest.raises(SystemExit) as exc_info:
            main_module._enforce_supported_platform()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == UNSUPPORTED_PLATFORM_MESSAGE
        assert captured.out == "", "the diagnostic belongs on stderr"


class TestGateRunsBeforeCommandRegistration:
    def test_guard_precedes_register_commands(self) -> None:
        """If these two ever swap, the gate silently stops working: the
        ModuleNotFoundError fires first and the friendly message never prints.
        Nothing at runtime on Linux can catch that, so pin it in the source."""
        source = _main_source()

        guard_line = _module_level_call_line(source, "_enforce_supported_platform()")
        register_line = _module_level_call_line(source, "_register_commands()")

        assert guard_line < register_line, (
            "_enforce_supported_platform() must run before _register_commands(), "
            "whose imports reach core/registry.py's `import fcntl`."
        )

    def test_gate_imports_cannot_reach_fcntl(self) -> None:
        """Everything imported above the guard must be fcntl-free, or the guard
        is dead code on the very platform it exists for."""
        source = _main_source()
        guard_line = _module_level_call_line(source, "_enforce_supported_platform()")
        preamble = "\n".join(source.splitlines()[:guard_line])

        # core.completion -> core.config is the deepest reach-in above the
        # guard today; core.platform imports nothing from remo_cli at all.
        assert "from remo_cli.core.registry import" not in preamble
        assert "from remo_cli.cli." not in preamble
