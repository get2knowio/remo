"""Notification transports for the remo notifier.

Each transport delivers an approval request to a human and reports their
decision back. Telegram is the only implementation in v1; additional backends
subclass :class:`~remo_cli.notifier.transports.base.NotificationTransport`.
"""

from __future__ import annotations
