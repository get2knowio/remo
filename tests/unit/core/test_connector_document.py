"""E4/contracts/session-document.md: the shipped document and the code
constants agree, and the document is reachable through importlib.resources
(package data), not a repo-relative path."""

from __future__ import annotations

import json

from remo_cli.core.connector import LAUNCHER_PATH, TARGET_MAX_CHARS, TARGET_PATTERN, load_document_text


def test_document_parses_as_json() -> None:
    doc = json.loads(load_document_text())
    assert isinstance(doc, dict)


def test_document_schema_version_and_session_type() -> None:
    doc = json.loads(load_document_text())
    assert doc["schemaVersion"] == "1.0"
    assert doc["sessionType"] == "InteractiveCommands"


def test_document_declares_exactly_one_parameter() -> None:
    doc = json.loads(load_document_text())
    assert list(doc["parameters"]) == ["target"]


def test_document_parameter_pattern_and_max_chars_match_constants() -> None:
    doc = json.loads(load_document_text())
    target = doc["parameters"]["target"]
    assert target["allowedPattern"] == TARGET_PATTERN
    assert target["maxChars"] == TARGET_MAX_CHARS


def test_document_command_invokes_only_the_launcher() -> None:
    doc = json.loads(load_document_text())
    commands = doc["properties"]["linux"]["commands"]
    assert commands == f"exec {LAUNCHER_PATH} connector attach -- {{{{ target }}}}"


def test_document_run_as_elevated_is_false() -> None:
    doc = json.loads(load_document_text())
    assert doc["properties"]["linux"]["runAsElevated"] is False


def test_document_file_ends_with_exactly_one_newline() -> None:
    text = load_document_text()
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_document_is_reachable_via_importlib_resources() -> None:
    """Reachable through package data (not a repo-relative path lookup), so
    the wheel-install smoke job exercises the shipped file (R14)."""
    import importlib.resources

    resource = importlib.resources.files("remo_cli.core").joinpath(
        "remo_attach_document.json"
    )
    assert resource.is_file()
    assert resource.read_text(encoding="utf-8") == load_document_text()
