"""Tests for the notifier config loader (spec 008 generic transport + agentsh)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from remo_cli.notifier.config import load_config


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "notifier.toml"
    p.write_text(textwrap.dedent(body).strip())
    return p


def _base(token_file: Path, *, transport_type: str = "telegram", agentsh: bool = True, **overrides: str) -> str:
    approval = overrides.get(
        "approval",
        "default_timeout_seconds = 300\nmax_timeout_seconds = 1800\nmax_pending_approvals = 50",
    )
    agentsh_block = (
        '\n[agentsh]\napi_url = "http://172.17.0.1:8080"\napi_key_file = "/run/secrets/agentsh_api_key"\n'
        if agentsh
        else ""
    )
    return f"""
        [server]
        listen_host = "0.0.0.0"
        listen_port = 18181
        log_level = "info"

        [approval]
        {approval}

        [transport]
        type = "{transport_type}"

        [transport.{transport_type}]
        bot_token_file = "{token_file}"
        authorized_chat_id = 987654321
        {agentsh_block}
        [instance]
        id = "h1"
    """


def test_valid_config_loads(tmp_path: Path, token_file: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(token_file)))
    assert cfg.server.listen_port == 18181
    assert cfg.approval.max_pending_approvals == 50
    assert cfg.transport.type == "telegram"
    assert cfg.transport.settings()["authorized_chat_id"] == 987654321


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


def test_transport_subtable_required(tmp_path: Path, token_file: Path) -> None:
    # type names a channel with no matching [transport.<type>] sub-table.
    body = _base(token_file).replace('type = "telegram"', 'type = "slack"')
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_agentsh_section_required(tmp_path: Path, token_file: Path) -> None:
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, _base(token_file, agentsh=False)))


def test_agentsh_defaults(tmp_path: Path, token_file: Path) -> None:
    cfg = load_config(_write(tmp_path, _base(token_file)))
    assert cfg.agentsh.api_url == "http://172.17.0.1:8080"
    assert cfg.agentsh.poll_interval_seconds == 5
    assert cfg.agentsh.webhook_enabled is False


def test_agentsh_poll_interval_lower_bound(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file).replace(
        'api_key_file = "/run/secrets/agentsh_api_key"',
        'api_key_file = "/run/secrets/agentsh_api_key"\npoll_interval_seconds = 0',
    )
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_agentsh_unknown_key_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file).replace(
        'api_key_file = "/run/secrets/agentsh_api_key"',
        'api_key_file = "/run/secrets/agentsh_api_key"\nsurprise = 1',
    )
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_agentsh_read_api_key(tmp_path: Path, token_file: Path, agentsh_key_file: Path) -> None:
    body = _base(token_file).replace(
        'api_key_file = "/run/secrets/agentsh_api_key"',
        f'api_key_file = "{agentsh_key_file}"',
    )
    cfg = load_config(_write(tmp_path, body))
    assert cfg.agentsh.read_api_key() == "approver-key-abc"


# --- GrantsConfig (Addendum 001) --------------------------------------------
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
    assert cfg.grants.digest_interval_seconds == 0


def test_grants_unknown_key_rejected(tmp_path: Path, token_file: Path) -> None:
    body = _base(token_file) + '\n[grants]\nsurprise = 1\n'
    with pytest.raises(ValueError):
        load_config(_write(tmp_path, body))


def test_grants_bad_bounds_rejected(tmp_path: Path, token_file: Path) -> None:
    for line in ("default_ttl_seconds = 0", "max_grants = 0"):
        body = _base(token_file) + f"\n[grants]\n{line}\n"
        with pytest.raises(ValueError):
            load_config(_write(tmp_path, body))
