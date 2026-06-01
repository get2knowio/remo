"""Tests for the notifier config loader (T009)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from remo_cli.notifier.config import load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "notifier.toml"
    p.write_text(textwrap.dedent(body).strip())
    return p


def _base(token_file: Path, **overrides: str) -> str:
    approval = overrides.get(
        "approval",
        "default_timeout_seconds = 300\nmax_timeout_seconds = 1800\nmax_pending_approvals = 50",
    )
    return f"""
        [server]
        listen_host = "0.0.0.0"
        listen_port = 18181
        log_level = "info"

        [approval]
        {approval}

        [transport]
        type = "telegram"

        [transport.telegram]
        bot_token_file = "{token_file}"
        authorized_chat_id = 987654321

        [instance]
        id = "h1"
    """


def test_valid_config_loads(tmp_path: Path, token_file: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(token_file)))
    assert cfg.server.listen_port == 18181
    assert cfg.approval.max_pending_approvals == 50
    assert cfg.transport.telegram is not None
    assert cfg.transport.telegram.authorized_chat_id == 987654321


def test_unknown_top_level_key_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file) + '\n[surprise]\nx = 1\n'
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_max_below_default_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file, approval="default_timeout_seconds = 600\nmax_timeout_seconds = 300")
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml")


def test_non_telegram_transport_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file).replace('type = "telegram"', 'type = "slack"')
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_token_read_from_file(tmp_path: Path, token_file: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(token_file)))
    assert cfg.transport.telegram is not None
    assert cfg.transport.telegram.read_token() == "12345:FAKE-TOKEN"


def test_empty_token_file_fails(tmp_path: Path) -> None:
    empty = tmp_path / "empty_token"
    empty.write_text("   ")
    cfg = load_config(_write(tmp_path, _base(empty)))
    assert cfg.transport.telegram is not None
    with pytest.raises(ValueError):
        cfg.transport.telegram.read_token()


def test_missing_token_file_fails(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(tmp_path / "absent")))
    assert cfg.transport.telegram is not None
    with pytest.raises(ValueError):
        cfg.transport.telegram.read_token()


# --- GrantsConfig (Addendum 001, TA003a) ------------------------------------
def test_grants_defaults(tmp_path: Path, token_file: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(token_file)))
    assert cfg.grants.enabled is True
    assert cfg.grants.default_ttl_seconds == 28800
    assert cfg.grants.max_grants == 100
    assert cfg.grants.allow_global_scope is True
    assert cfg.grants.digest_interval_seconds == 3600


def test_grants_block_parses(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file) + (
        '\n[grants]\nenabled = false\ndefault_ttl_seconds = 600\n'
        'max_grants = 5\nallow_global_scope = false\ndigest_interval_seconds = 0\n'
    )
    cfg = load_config(_write(tmp_path, body))
    assert cfg.grants.enabled is False
    assert cfg.grants.default_ttl_seconds == 600
    assert cfg.grants.max_grants == 5
    assert cfg.grants.allow_global_scope is False
    assert cfg.grants.digest_interval_seconds == 0  # disables digest


def test_grants_unknown_key_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file) + '\n[grants]\nsurprise = 1\n'
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_grants_bad_bounds_rejected(tmp_path: Path, token_file: Path) -> None:
    for line in ("default_ttl_seconds = 0", "max_grants = 0"):
        body = _base(token_file) + f"\n[grants]\n{line}\n"
        with pytest.raises(ValueError):
            load_config(_write(tmp_path, body))
