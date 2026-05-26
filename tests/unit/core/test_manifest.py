"""Tests for remo_cli.core.manifest (discovery, synthesis, validation)."""

from pathlib import Path

import pytest

from remo_cli.core import manifest as manifest_mod
from remo_cli.core.manifest import ManifestError


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return tmp_path


def test_discover_none(project):
    assert manifest_mod.discover(project) is None


def test_discover_committed_priority(project):
    (project / ".devcontainer").mkdir()
    committed = project / ".devcontainer" / "remo-broker.toml"
    committed.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"github_token\"]\n", encoding="utf-8"
    )
    (project / ".remo").mkdir()
    synthesized = project / ".remo" / "broker.toml"
    synthesized.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"npm_token\"]\n", encoding="utf-8"
    )
    found = manifest_mod.discover(project)
    assert found == committed


def test_discover_falls_back_to_synthesized(project):
    (project / ".remo").mkdir()
    synthesized = project / ".remo" / "broker.toml"
    synthesized.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"npm_token\"]\n", encoding="utf-8"
    )
    found = manifest_mod.discover(project)
    assert found == synthesized


def test_synthesize_default_creates_files(project):
    m = manifest_mod.synthesize_default(project)
    target = project / ".remo" / "broker.toml"
    assert target.exists()
    assert m.schema_version == 1
    assert m.secrets == ["github_token"]


def test_synthesize_default_appends_gitignore(project):
    manifest_mod.synthesize_default(project)
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    assert ".remo/" in gitignore


def test_synthesize_default_idempotent_gitignore(project):
    (project / ".gitignore").write_text("node_modules/\n.remo/\n", encoding="utf-8")
    manifest_mod.synthesize_default(project)
    gitignore = (project / ".gitignore").read_text(encoding="utf-8")
    # No duplicate entry
    assert gitignore.count(".remo/") == 1


def test_synthesize_default_idempotent_file(project):
    m1 = manifest_mod.synthesize_default(project)
    target = project / ".remo" / "broker.toml"
    target.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"custom_token\"]\n", encoding="utf-8"
    )
    m2 = manifest_mod.synthesize_default(project)
    # File preserved, not overwritten
    assert m2.secrets == ["custom_token"]
    assert m1 != m2


def test_load_valid(project):
    p = project / "broker.toml"
    p.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"github_token\", \"npm_token\"]\n",
        encoding="utf-8",
    )
    m = manifest_mod.load(p)
    assert m.secrets == ["github_token", "npm_token"]


def test_load_de_duplicates(project):
    p = project / "broker.toml"
    p.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"a_token\", \"a_token\", \"b_token\"]\n",
        encoding="utf-8",
    )
    m = manifest_mod.load(p)
    assert m.secrets == ["a_token", "b_token"]


def test_load_rejects_unknown_schema(project):
    p = project / "broker.toml"
    p.write_text(
        "schema_version = 99\n[mcp]\nsecrets = [\"github_token\"]\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="unsupported schema_version"):
        manifest_mod.load(p)


def test_load_rejects_bad_secret_pattern(project):
    p = project / "broker.toml"
    p.write_text(
        "schema_version = 1\n[mcp]\nsecrets = [\"Has-Caps\"]\n", encoding="utf-8"
    )
    with pytest.raises(ManifestError):
        manifest_mod.load(p)


def test_load_rejects_missing_mcp(project):
    p = project / "broker.toml"
    p.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(ManifestError):
        manifest_mod.load(p)


def test_load_invalid_toml(project):
    p = project / "broker.toml"
    p.write_text("not = valid = toml", encoding="utf-8")
    with pytest.raises(ManifestError, match="TOML parse error"):
        manifest_mod.load(p)
