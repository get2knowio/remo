"""remo installs exactly one executable, `remo`.

A `remo-cli` alias was added when the package was renamed for PyPI, so
`uvx remo-cli` would run. remo is no longer published to PyPI (constitution
IX), and the alias surfaced as a confusing second executable in every
`uv tool install` ("Installed 2 executables: remo, remo-cli").
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_only_the_remo_executable_is_installed() -> None:
    scripts = tomllib.loads(PYPROJECT.read_text())["project"]["scripts"]
    assert scripts == {"remo": "remo_cli.cli.main:main"}
