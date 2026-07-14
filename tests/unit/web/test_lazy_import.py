"""Unit tests for NFR-008: the ordinary CLI must never require
fastapi/uvicorn, even indirectly via `remo web` group registration (T022).

Covers T023 from specs/010-web-session-interface/tasks.md:

- Importing `remo_cli.cli.main` (which triggers `_register_commands()`,
  which imports `remo_cli.cli.web`) must succeed even when `fastapi` /
  `uvicorn` are not importable. Verified by spawning a subprocess with
  `builtins.__import__` monkeypatched to block those modules, so the test
  proves the boundary regardless of whether the `web` extra happens to be
  installed in the environment running the suite.
- `remo web serve` / `remo web check`, invoked without the `web` extra
  installed, exit non-zero with the `pip install "remo-cli[web]"` hint in
  their output and never a raw traceback.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src"

# Run in a fresh subprocess (rather than monkeypatching builtins.__import__
# in-process) so the block can't leak into other tests via a polluted
# sys.modules / import-cache state.
_BLOCK_AND_IMPORT_MAIN = """
import builtins

_BLOCKED = ("fastapi", "uvicorn", "starlette")
_real_import = builtins.__import__


def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    for blocked in _BLOCKED:
        if name == blocked or name.startswith(blocked + "."):
            raise ImportError(f"blocked for test: {name}")
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _fake_import

# Sanity-check the block is actually in effect before trusting the result
# below -- otherwise a bug in the fake importer would make this test pass
# for the wrong reason.
try:
    import fastapi  # noqa: F401
    raise SystemExit("SANITY FAIL: fastapi import was not blocked")
except ImportError:
    pass

import remo_cli.cli.main  # noqa: F401

print("IMPORT_OK")
"""


def _run_with_fastapi_blocked() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC_DIR)
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_AND_IMPORT_MAIN],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class TestLazyImportNFR008:
    """Importing remo_cli.cli.main must not require fastapi/uvicorn."""

    def test_cli_main_imports_with_fastapi_uvicorn_blocked(self):
        result = _run_with_fastapi_blocked()
        assert result.returncode == 0, (
            "remo_cli.cli.main failed to import with fastapi/uvicorn "
            f"blocked.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "IMPORT_OK" in result.stdout

    def test_cli_main_imports_normally(self):
        # Documents current sandbox state too: as of writing, fastapi/uvicorn
        # are not installed at all here, so this also covers the "extra
        # genuinely absent" case end-to-end.
        import remo_cli.cli.main  # noqa: F401


def _fastapi_is_importable() -> bool:
    try:
        import fastapi  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(
    _fastapi_is_importable(),
    reason=(
        "fastapi is importable in this environment; the 'web extra genuinely "
        "absent' CLI behavior can't be exercised in-process here (see "
        "TestLazyImportNFR008 for the environment-independent regression test)."
    ),
)
class TestWebCommandsWithoutExtra:
    """`remo web serve` / `remo web check` without the `web` extra installed."""

    def test_serve_without_extra_prints_install_hint_not_traceback(self):
        from remo_cli.cli.main import cli

        result = CliRunner().invoke(cli, ["web", "serve"])

        assert result.exit_code != 0
        assert 'pip install "remo-cli[web]"' in result.output
        assert "Traceback" not in result.output

    def test_check_without_extra_prints_install_hint_not_traceback(self):
        from remo_cli.cli.main import cli

        result = CliRunner().invoke(cli, ["web", "check"])

        assert result.exit_code != 0
        assert 'pip install "remo-cli[web]"' in result.output
        assert "Traceback" not in result.output
