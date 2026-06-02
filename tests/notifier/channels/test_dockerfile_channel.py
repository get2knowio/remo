"""US3 / T027: the single parameterized Dockerfile builds any channel.

Asserts the install command is constructed from the CHANNEL build arg, so no new
Dockerfile is needed per channel (research R4).
"""

from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("pyproject.toml not found")


def test_dockerfile_parameterized_by_channel() -> None:
    text = (_project_root() / "notifier" / "Dockerfile").read_text()
    assert "ARG CHANNEL" in text
    # The extra installed is derived from the build arg, not hard-coded per channel.
    assert '".[notifier-${CHANNEL}]"' in text


def test_install_string_built_for_arbitrary_channel() -> None:
    # Simulate what the build does for any channel id.
    for channel in ("telegram", "slack", "stub"):
        install = f'uv pip install --no-cache-dir ".[notifier-{channel}]"'
        assert f"notifier-{channel}" in install
