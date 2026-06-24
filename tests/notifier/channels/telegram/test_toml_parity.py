"""TOML wire-stability for the Telegram channel (spec 008, T021 / FR-017).

The descriptor-rendered ``[transport.telegram]`` block must stay byte-identical
to spec 007's notifier.toml.
"""

from __future__ import annotations

from remo_cli.notifier.channels.catalog import get

# Exactly the block spec 007 wrote (with the standard chat id / parse mode).
_SPEC_007_FRAGMENT = (
    '[transport]\n'
    'type = "telegram"\n'
    "\n"
    "[transport.telegram]\n"
    'bot_token_file = "/run/secrets/telegram_bot_token"\n'
    "authorized_chat_id = 987654321\n"
    'message_parse_mode = "MarkdownV2"\n'
)


def test_descriptor_renders_007_byte_identical() -> None:
    d = get("telegram")
    rendered = d.render_transport_toml({"REMO_NOTIFIER_TELEGRAM_CHAT_ID": "987654321"})
    assert rendered == _SPEC_007_FRAGMENT


def test_render_respects_optional_parse_mode() -> None:
    d = get("telegram")
    rendered = d.render_transport_toml(
        {"REMO_NOTIFIER_TELEGRAM_CHAT_ID": "55", "REMO_NOTIFIER_TELEGRAM_PARSE_MODE": "HTML"}
    )
    assert 'message_parse_mode = "HTML"' in rendered
    assert "authorized_chat_id = 55" in rendered


def test_rendered_fragment_loads_under_generic_config() -> None:
    # The rendered fragment + an [agentsh]/[instance]/secret must validate and
    # the telegram channel must accept its own sub-table (round-trip).
    import textwrap
    from pathlib import Path
    import tempfile

    from remo_cli.notifier.channels.telegram.config import TelegramConfig
    from remo_cli.notifier.config import load_config

    d = get("telegram")
    fragment = d.render_transport_toml({"REMO_NOTIFIER_TELEGRAM_CHAT_ID": "987654321"})
    with tempfile.TemporaryDirectory() as tmp:
        cfg_path = Path(tmp) / "notifier.toml"
        cfg_path.write_text(
            textwrap.dedent(
                """
                [server]
                listen_host = "0.0.0.0"
                listen_port = 18181

                [approval]
                default_timeout_seconds = 300
                max_timeout_seconds = 1800
                max_pending_approvals = 50

                """
            ).lstrip()
            + fragment
            + '\n[agentsh]\napi_url = "http://x:8080"\n\n[instance]\nid = "h1"\n'
        )
        cfg = load_config(cfg_path)
        assert cfg.transport.type == "telegram"
        tg = TelegramConfig.model_validate(cfg.transport.settings())
        assert tg.authorized_chat_id == 987654321
