"""Log/secret redaction tests (T055, FR-028).

Two guarantees, tested separately:

(a) A source-grep regression guard: no file under `src/remo_cli/web/` (or
    `core/remo_host_client.py` / `core/ssh.py`) contains a `print`/
    `logging.*`/`click.echo` call whose arguments directly interpolate a
    variable named/containing `token`, `ws_token`, `proxy_cmd`, or
    `private_key`. This is intentionally blunt (a source scan, not a
    semantic one) -- see `web/logging_config.py`'s module docstring for why
    the PRIMARY guarantee is architectural (never construct the message in
    the first place) rather than relying on the redaction filter alone.

(b) The `RedactingFilter` itself: given a fabricated `LogRecord` containing
    a token/proxy-command/private-key-shaped string, the filtered message
    has the secret replaced/masked.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

import pytest

from remo_cli.web.logging_config import RedactingFilter, configure_logging

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCANNED_FILES = [
    *sorted((_REPO_ROOT / "src" / "remo_cli" / "web").rglob("*.py")),
    _REPO_ROOT / "src" / "remo_cli" / "core" / "remo_host_client.py",
    _REPO_ROOT / "src" / "remo_cli" / "core" / "ssh.py",
]

_SECRET_NAME_MARKERS = ("token", "ws_token", "proxy_cmd", "private_key")

# Calls whose arguments are inspected for a leaking secret-shaped name.
_LOG_CALL_NAMES = {
    "print",
    "echo",  # click.echo(...) / ctx.echo(...)
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "critical",
    "exception",
    "log",
}


def _call_target_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _names_in_node(node: ast.AST) -> set[str]:
    """All `ast.Name`/`ast.Attribute` identifiers referenced anywhere in *node*."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _leaking_calls(tree: ast.AST, path: Path) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _call_target_name(node)
        if target not in _LOG_CALL_NAMES:
            continue
        referenced = _names_in_node(node)
        for marker in _SECRET_NAME_MARKERS:
            if any(marker in name.lower() for name in referenced):
                findings.append(
                    f"{path}:{node.lineno}: {target}(...) references a "
                    f"'{marker}'-shaped name -- route through redaction or "
                    "rename/remove the raw interpolation"
                )
    return findings


class TestSourceAuditNoRawSecretLogging:
    def test_no_log_call_interpolates_a_secret_shaped_variable(self):
        all_findings: list[str] = []
        for path in _SCANNED_FILES:
            if not path.is_file():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            all_findings.extend(_leaking_calls(tree, path))

        assert not all_findings, "Potential secret leak(s) in logging/print calls:\n" + "\n".join(
            all_findings
        )

    def test_scanned_file_set_is_non_empty(self):
        # Guards against the scan silently covering zero files (e.g. a path
        # typo) and the test above passing for the wrong reason.
        assert sum(1 for p in _SCANNED_FILES if p.is_file()) >= 5


# ---------------------------------------------------------------------------
# RedactingFilter behavior
# ---------------------------------------------------------------------------


def _make_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="remo_cli.web.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


class TestRedactingFilter:
    @pytest.mark.parametrize(
        "message",
        [
            "opening ws_token=abcDEF123-_veryLongOpaqueValue1234567890 for terminal-1",
            "token=abcDEF123-_veryLongOpaqueValue1234567890 rejected: expired",
            "Token: abcDEF123-_veryLongOpaqueValue1234567890",
        ],
    )
    def test_token_values_are_masked(self, message: str):
        record = _make_record(message)
        RedactingFilter().filter(record)
        rendered = record.getMessage()
        assert "abcDEF123" not in rendered
        assert "<redacted>" in rendered

    def test_proxy_command_is_masked(self):
        message = (
            "ssh failed with -o "
            "ProxyCommand=aws ssm start-session --region us-west-2 --target i-0abc123"
        )
        record = _make_record(message)
        RedactingFilter().filter(record)
        rendered = record.getMessage()
        assert "start-session" not in rendered
        assert "i-0abc123" not in rendered
        assert "<redacted>" in rendered

    def test_private_key_block_is_masked(self):
        message = (
            "loaded identity:\n-----BEGIN OPENSSH PRIVATE KEY-----\n"
            "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQyNTUxOQ\n"
            "-----END OPENSSH PRIVATE KEY-----\nend."
        )
        record = _make_record(message)
        RedactingFilter().filter(record)
        rendered = record.getMessage()
        assert "b3BlbnNzaC1rZXktdjEA" not in rendered
        assert "<redacted>" in rendered

    def test_ordinary_message_passes_through_unchanged(self):
        message = "Remo web service starting on http://127.0.0.1:8080"
        record = _make_record(message)
        RedactingFilter().filter(record)
        assert record.getMessage() == message

    def test_filter_never_raises_on_non_string_msg(self):
        record = logging.LogRecord(
            name="remo_cli.web.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg={"not": "a string"},
            args=(),
            exc_info=None,
        )
        # Must not raise; filter() always returns True (never drops records).
        assert RedactingFilter().filter(record) is True


class TestConfigureLogging:
    def test_configure_logging_is_idempotent_and_installs_filter(self):
        logger = logging.getLogger("remo_cli")

        # `configure_logging()` may already have been called once by an
        # earlier test in this same process (the `remo_cli` logger is a
        # process-wide singleton, and `configure_logging()` is documented as
        # safe to call "from `create_app()` on every app construction --
        # including once per test", so other test modules legitimately do).
        # Call it once here first to reach that steady state deterministically
        # regardless of test execution order, THEN measure idempotency from
        # that baseline -- this is what "idempotent" actually means (repeat
        # calls are no-ops), not "the very first call in the whole process".
        configure_logging()
        before = len(logger.handlers)

        configure_logging()
        configure_logging()

        after = len(logger.handlers)
        assert after == before
        assert any(
            isinstance(f, RedactingFilter) for h in logger.handlers for f in h.filters
        )
