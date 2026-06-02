"""Tests for the channel catalog (spec 008, T020)."""

from __future__ import annotations

from remo_cli.notifier.channels import catalog
from remo_cli.notifier.channels.base import ChannelDescriptor, RequiredEnv


def test_list_channels_contains_telegram():
    ids = [c.id for c in catalog.list_channels()]
    assert "telegram" in ids


def test_get_known_channel():
    d = catalog.get("telegram")
    assert d is not None
    assert d.label == "Telegram"
    assert d.image_name == "remo-notifier-telegram"


def test_get_unknown_channel_returns_none():
    assert catalog.get("does-not-exist") is None


def test_telegram_descriptor_shape():
    d = catalog.get("telegram")
    assert isinstance(d, ChannelDescriptor)
    secret = d.secret_env()
    assert isinstance(secret, RequiredEnv)
    assert secret.name == "REMO_NOTIFIER_TELEGRAM_BOT_TOKEN"
    assert secret.secret is True
    # Every required env follows the REMO_NOTIFIER_<CHANNEL>_ convention.
    for e in d.required_env:
        assert e.name.startswith("REMO_NOTIFIER_TELEGRAM_")


def test_telegram_secret_filename_matches_mount():
    d = catalog.get("telegram")
    assert d.secret_mount == "/run/secrets/telegram_bot_token"
    assert d.secret_filename() == "telegram_bot_token"
