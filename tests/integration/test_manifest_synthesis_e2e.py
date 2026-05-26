"""US4 T071: fresh project gets `.remo/broker.toml` with default + .gitignore entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from remo_cli.core import manifest as manifest_mod


def test_fresh_project_gets_default_and_gitignore_entry(tmp_path: Path):
    # No existing manifest, no existing .gitignore.
    m = manifest_mod.synthesize_default(tmp_path)

    target = tmp_path / ".remo" / "broker.toml"
    assert target.exists()
    assert m.secrets == ["github_token"]

    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert ".remo/" in gitignore


def test_synthesis_preserves_existing_gitignore_lines(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("node_modules/\ndist/\n", encoding="utf-8")
    manifest_mod.synthesize_default(tmp_path)
    gitignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules/" in gitignore
    assert "dist/" in gitignore
    assert ".remo/" in gitignore


def test_synthesis_idempotent_when_remo_dir_exists(tmp_path: Path):
    """Second call should leave files unchanged (developer edits preserved)."""
    manifest_mod.synthesize_default(tmp_path)
    target = tmp_path / ".remo" / "broker.toml"
    target.write_text(
        'schema_version = 1\n[mcp]\nsecrets = ["custom_token"]\n',
        encoding="utf-8",
    )
    m = manifest_mod.synthesize_default(tmp_path)
    assert m.secrets == ["custom_token"]


def test_committed_devcontainer_manifest_wins_discovery(tmp_path: Path):
    (tmp_path / ".devcontainer").mkdir()
    (tmp_path / ".devcontainer" / "remo-broker.toml").write_text(
        'schema_version = 1\n[mcp]\nsecrets = ["committed_token"]\n',
        encoding="utf-8",
    )
    manifest_mod.synthesize_default(tmp_path)
    found = manifest_mod.discover(tmp_path)
    assert found is not None
    assert "devcontainer" in str(found)
