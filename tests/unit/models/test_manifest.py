"""Tests for the ProjectManifest dataclass."""

import pytest

from remo_cli.models.manifest import (
    SUPPORTED_SCHEMA_VERSIONS,
    ManifestValidationError,
    ProjectManifest,
)


def test_supported_versions_contains_one():
    assert 1 in SUPPORTED_SCHEMA_VERSIONS


def test_valid_minimal():
    m = ProjectManifest(schema_version=1, secrets=["github_token"])
    assert m.secrets == ["github_token"]


def test_valid_multiple_secrets_and_notes():
    m = ProjectManifest(
        schema_version=1,
        secrets=["github_token", "npm_token"],
        notes="frontend project",
    )
    assert m.notes == "frontend project"


def test_invalid_schema_version():
    with pytest.raises(ManifestValidationError, match="unsupported schema_version"):
        ProjectManifest(schema_version=999, secrets=[])


def test_invalid_secret_name_uppercase():
    with pytest.raises(ManifestValidationError, match="invalid secret name"):
        ProjectManifest(schema_version=1, secrets=["GitHubToken"])


def test_invalid_secret_name_dash():
    with pytest.raises(ManifestValidationError, match="invalid secret name"):
        ProjectManifest(schema_version=1, secrets=["github-token"])


def test_invalid_secret_name_starts_digit():
    with pytest.raises(ManifestValidationError, match="invalid secret name"):
        ProjectManifest(schema_version=1, secrets=["1secret"])


def test_invalid_secrets_not_list():
    with pytest.raises(ManifestValidationError, match="must be an array"):
        ProjectManifest(schema_version=1, secrets="github_token")  # type: ignore[arg-type]
