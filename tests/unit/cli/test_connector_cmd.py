"""US4: `remo connector document` CLI wiring (specs/025-ssm-connector, T021)."""

from __future__ import annotations

from click.testing import CliRunner

from remo_cli.cli.main import cli
from remo_cli.core.connector import load_document_text


def _invoke(monkeypatch, tmp_path, *args: str):
    """Invoke the CLI with an isolated $HOME so the ambient post-command
    nudges (stale shell completion / passive update check) — which read the
    REAL `Path.home()`, not $REMO_HOME — cannot pollute stdout and defeat an
    exact-output assertion."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return CliRunner().invoke(cli, list(args), catch_exceptions=False)


def test_document_prints_shipped_file(monkeypatch, tmp_path) -> None:
    result = _invoke(monkeypatch, tmp_path, "connector", "document")
    assert result.exit_code == 0
    assert result.output == load_document_text()


def test_document_name_flag_prints_only_the_name(monkeypatch, tmp_path) -> None:
    result = _invoke(monkeypatch, tmp_path, "connector", "document", "--name")
    assert result.exit_code == 0
    assert result.output == "remo-attach\n"


def test_connector_help_lists_the_full_command_surface(monkeypatch, tmp_path) -> None:
    result = _invoke(monkeypatch, tmp_path, "connector", "--help")
    assert result.exit_code == 0
    for name in ("attach", "document", "enroll", "status", "unenroll"):
        assert name in result.output, f"'{name}' missing from `remo connector --help`"


def test_enroll_help_has_no_activation_code_option_and_mentions_stdin(
    monkeypatch, tmp_path
) -> None:
    result = _invoke(monkeypatch, tmp_path, "connector", "enroll", "--help")
    assert result.exit_code == 0
    assert "--activation-code" not in result.output
    assert "stdin" in result.output.lower() or "prompt" in result.output.lower()
