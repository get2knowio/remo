"""Fixtures for the remo-provider-ec2 suite.

This suite runs on its own (``pytest plugins/ec2/tests``), against an
installed remo-cli, so it cannot lean on remo's ``tests/conftest.py``; the
fixtures it needs are restated here (a plugin test must never import
from remo's `tests` package, which is not installed with remo-cli).
"""

from __future__ import annotations

import json
import os

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_tab_identity(monkeypatch):
    """A developer's own terminal (tmux, kitty, iTerm...) must not leak into
    tests that reach `remo shell` (spec 028)."""
    from remo_cli.core.tab_identity import TAB_VARIABLES

    for variable in (*TAB_VARIABLES, "TMUX", "REMO_TAB_KEY"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Provide a temporary config directory and set REMO_HOME to it."""
    config_dir = tmp_path / "remo"
    config_dir.mkdir()
    old_home = os.environ.get("REMO_HOME")
    os.environ["REMO_HOME"] = str(config_dir)
    yield config_dir
    if old_home is None:
        os.environ.pop("REMO_HOME", None)
    else:
        os.environ["REMO_HOME"] = old_home


@pytest.fixture
def seed_registry():
    """Write a v2 registry.json from ``KnownHost`` objects, through the real
    ``known_host_to_entry`` serializer (remo's tests/conftest.seed_registry)."""

    def _seed(config_dir, hosts: list) -> None:
        from remo_cli.core.registry import known_host_to_entry

        doc = {"version": 2, "hosts": [known_host_to_entry(h) for h in hosts]}
        (config_dir / "registry.json").write_text(json.dumps(doc, indent=2) + "\n")

    return _seed
