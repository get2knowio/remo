"""`uv.lock` must record the same project version as `pyproject.toml`.

release-please bumps `pyproject.toml` (via `extra-files` in
`release-please-config.json`) but knows nothing about `uv.lock`, which carries
the project's own version in its `remo-cli` package entry. Nothing reconciled
the two, so every release shipped a lockfile one version behind — 3.1.0 went
out with a lockfile still saying 3.0.0 — and the drift then surfaced as an
unrelated one-line diff in whichever PR next ran `uv`, since any `uv` command
regenerates it.

`.github/workflows/release-please.yml` now amends `uv lock` onto the release PR.
This test is the backstop: it fails the build if the two ever disagree again,
whether because that automation was skipped (no App token, a fork) or because
someone hand-edited a version. The fix is always the same one command, named in
the failure message.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"

PACKAGE_NAME = "remo-cli"


def _pyproject_version() -> str:
    return str(tomllib.loads(PYPROJECT.read_text())["project"]["version"])


def _locked_version() -> str | None:
    """The version `uv.lock` records for this project's own package entry."""
    for package in tomllib.loads(UV_LOCK.read_text()).get("package", []):
        if package.get("name") == PACKAGE_NAME:
            version = package.get("version")
            return None if version is None else str(version)
    return None


def test_lockfile_records_the_project_version() -> None:
    declared = _pyproject_version()
    locked = _locked_version()

    assert locked is not None, (
        f"uv.lock has no `{PACKAGE_NAME}` package entry — the lockfile is not for "
        "this project. Regenerate it with `uv lock`."
    )
    assert locked == declared, (
        f"uv.lock records {PACKAGE_NAME} {locked} but pyproject.toml declares "
        f"{declared}. A release bumped one without the other; run `uv lock` and "
        "commit the result."
    )
