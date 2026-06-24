"""The channel catalog (spec 008, data-model.md).

A pure-data, import-light registry. Adding a channel appends one descriptor
here (the only permitted core touch — contracts/channel-extension.md). The
laptop CLI imports this module to list channels and run preflight without
pulling any channel delivery SDK or the service deps (FR-019).
"""

from __future__ import annotations

from remo_cli.notifier.channels.base import ChannelDescriptor
from remo_cli.notifier.channels.telegram.descriptor import TELEGRAM

CHANNELS: list[ChannelDescriptor] = [TELEGRAM]


def list_channels() -> list[ChannelDescriptor]:
    return list(CHANNELS)


def get(channel_id: str) -> ChannelDescriptor | None:
    for descriptor in CHANNELS:
        if descriptor.id == channel_id:
            return descriptor
    return None
