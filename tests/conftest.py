"""Shared fixtures for remo tests."""

import os
import tempfile

import pytest


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
def mock_subprocess(mocker):
    """Mock subprocess.run for testing commands that shell out."""
    return mocker.patch("subprocess.run")
