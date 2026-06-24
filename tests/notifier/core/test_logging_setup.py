"""Tests for secret-safe logging (T007a, finding G1 / SC-006 / FR-017)."""

from __future__ import annotations

import json

from remo_cli.notifier import logging_setup


def test_sensitive_keys_redacted_at_info(capsys) -> None:
    logging_setup.configure_logging(level="debug", json_logs=True)
    log = logging_setup.get_logger("test")

    log.info(
        "approval_received",
        approval_id="abc",
        decision="allow",
        bot_token="12345:SECRET",
        workspace="/home/me/project",
        policy_message="please approve rm -rf",
    )

    line = capsys.readouterr().out.strip().splitlines()[-1]
    event = json.loads(line)

    # Structural fields survive.
    assert event["approval_id"] == "abc"
    assert event["decision"] == "allow"
    # Sensitive fields are redacted, not present verbatim.
    assert event["bot_token"] == "[redacted]"
    assert event["workspace"] == "[redacted]"
    assert event["policy_message"] == "[redacted]"
    assert "12345:SECRET" not in line
    assert "/home/me/project" not in line


def test_sensitive_keys_kept_at_debug(capsys) -> None:
    logging_setup.configure_logging(level="debug", json_logs=True)
    log = logging_setup.get_logger("test")

    log.debug("debug_detail", bot_token="12345:SECRET", workspace="/home/me/project")

    line = capsys.readouterr().out.strip().splitlines()[-1]
    event = json.loads(line)

    # At DEBUG, developer opt-in keeps full detail.
    assert event["bot_token"] == "12345:SECRET"
    assert event["workspace"] == "/home/me/project"


def test_info_suppressed_when_level_is_warning(capsys) -> None:
    logging_setup.configure_logging(level="warning", json_logs=True)
    log = logging_setup.get_logger("test")

    log.info("should_not_appear", approval_id="abc")
    out = capsys.readouterr().out
    assert "should_not_appear" not in out
