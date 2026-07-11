"""Remo Notifier — Telegram approval bridge for agentsh.

A long-running HTTP daemon that receives agentsh approval requests, delivers
them to a human via a pluggable notification transport (Telegram in v1), and
returns the human's decision synchronously — failing secure (deny) on timeout,
shutdown, send failure, or capacity exhaustion. No persistent state.
"""

from __future__ import annotations

# Notifier component version — surfaced in GET /v1/health and used as the
# container image tag. Intentionally independent of the remo-cli package
# version (see data-model.md, finding I2).
__version__ = "0.1.0"
