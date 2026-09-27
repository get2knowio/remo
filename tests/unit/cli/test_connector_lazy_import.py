"""FR-010/SC-002: the connector must work without the `web` extra installed,
and must never branch on `host.type` (Constitution II, FR-013).

Modeled on `tests/unit/web/test_lazy_import.py`.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = REPO_ROOT / "src"

_BLOCK_AND_RUN = """
import builtins

_BLOCKED = ("fastapi", "uvicorn", "starlette")
_real_import = builtins.__import__


def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    for blocked in _BLOCKED:
        if name == blocked or name.startswith(blocked + "."):
            raise ImportError(f"blocked for test: {name}")
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _fake_import

try:
    import fastapi  # noqa: F401
    raise SystemExit("SANITY FAIL: fastapi import was not blocked")
except ImportError:
    pass

import sys as _sys
# Sentinel: no remo_cli.web module is importable either (defense in depth --
# proves the connector path never even ATTEMPTS to reach it).
_sys.modules["remo_cli.web"] = None  # type: ignore[assignment]

import remo_cli.core.attach  # noqa: F401
import remo_cli.core.connector as connector_core
import remo_cli.providers.connector as connector_provider
import remo_cli.cli.connector  # noqa: F401

import json
import tempfile
from pathlib import Path as _Path

state = _Path(tempfile.mkdtemp())
(state / "ssh").mkdir()
(state / "registry.json").write_text(json.dumps({"version": 2, "hosts": []}))
(state / "id_ed25519").write_text("k")
(state / "known_hosts").write_text("")

import os as _os
_os.environ["REMO_CONNECTOR_STATE_DIR"] = str(state)

import io
import contextlib

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = connector_provider.attach(connector_core.encode_target("nope", "remo"))

assert rc == 1, f"expected rc=1, got {rc}"
out = buf.getvalue().strip()
assert out.startswith("remo-connector-error: not-exposed"), f"unexpected output: {out!r}"

leaked = [m for m in _sys.modules if m.startswith("remo_cli.web") and m != "remo_cli.web"]
assert not leaked, f"remo_cli.web submodules loaded: {leaked}"

print("IMPORT_OK")
"""


def _run_with_fastapi_blocked() -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC_DIR)
    return subprocess.run(
        [sys.executable, "-c", _BLOCK_AND_RUN],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def test_connector_works_without_web_extra_installed() -> None:
    result = _run_with_fastapi_blocked()
    assert result.returncode == 0, (
        "the connector path failed to run with fastapi/uvicorn/starlette blocked and "
        f"remo_cli.web sentinel-poisoned.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "IMPORT_OK" in result.stdout


# ---------------------------------------------------------------------------
# No host.type branching in the new modules (Constitution II, FR-013).
# ---------------------------------------------------------------------------

_NEW_MODULES = [
    REPO_ROOT / "src" / "remo_cli" / "core" / "attach.py",
    REPO_ROOT / "src" / "remo_cli" / "core" / "connector.py",
    REPO_ROOT / "src" / "remo_cli" / "providers" / "connector.py",
    REPO_ROOT / "src" / "remo_cli" / "cli" / "connector.py",
]


def _has_host_type_comparison(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "type"):
            continue
        # host.type == "..."  or  host.type in (...)
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.Eq, ast.In)):
                return True
    return False


def test_no_host_type_branching() -> None:
    for path in _NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not _has_host_type_comparison(tree), (
            f"{path} contains a `host.type == ...` / `host.type in (...)` comparison — "
            "provider-varying behavior must go through core.ssh.build_ssh_opts/"
            "build_ssh_base_cmd, never a literal type branch here (Constitution II)."
        )
