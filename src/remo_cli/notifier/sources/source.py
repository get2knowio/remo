"""Runtime registry objects: one ``Source`` per agentsh approval endpoint.

A ``Source`` is in-memory only and never serialized (spec 009 data-model). Its
``api_key`` is held in memory and **redacted** from ``repr`` so it can never leak
into a traceback or a structured log line.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from remo_cli.notifier.agentsh_client import AgentshClient


@dataclass
class PollHealth:
    """Per-source poll-health bookkeeping (FR-014/FR-015).

    ``poll_state`` is derived from ``consecutive_failures``: ``"polling"`` after a
    success, ``"backing_off"`` while failing. Never affects registration — only
    the presence connection does.
    """

    poll_state: str = "polling"
    consecutive_failures: int = 0
    current_backoff_seconds: float = 0.0
    last_success_at: datetime | None = None


@dataclass
class Source:
    """One registered agentsh endpoint and its live poll machinery."""

    source_id: str
    api_url: str
    api_key: str
    epoch: int
    client: AgentshClient
    labels: dict[str, str] = field(default_factory=dict)
    permanent: bool = False
    health: PollHealth = field(default_factory=PollHealth)
    task: asyncio.Task | None = None
    # Set when registered by an open presence connection; pollers await it to be
    # woken early (e.g. by a webhook "poll now" trigger).
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def __repr__(self) -> str:  # never expose api_key
        return (
            f"Source(source_id={self.source_id!r}, api_url={self.api_url!r}, "
            f"epoch={self.epoch}, permanent={self.permanent}, "
            f"poll_state={self.health.poll_state!r}, api_key=<redacted>)"
        )
