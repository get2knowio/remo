"""Env-first, file-fallback readers for broker settings written by `remo init`.

`remo init` persists the broker backend and admin SA fnox key into
`~/.config/remo/config.yml` (mode 0600), but every consumer historically read
only the environment. These helpers bridge that gap: env wins so power users
can override per-shell, otherwise we fall back to the on-disk config.
"""

from __future__ import annotations

import os
from typing import Any

import yaml

from remo_cli.core.config import get_remo_home


def _read_broker_section() -> dict[str, Any]:
    try:
        path = get_remo_home() / "config.yml"
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    broker = data.get("broker")
    if not isinstance(broker, dict):
        return {}
    return broker


def get_backend() -> str:
    env = os.environ.get("REMO_BROKER_BACKEND")
    if env:
        return env
    value = _read_broker_section().get("backend")
    if isinstance(value, str) and value:
        return value
    return ""


def get_admin_sa_fnox_key() -> str | None:
    env = os.environ.get("REMO_BROKER_ADMIN_SA_KEY")
    if env:
        return env
    value = _read_broker_section().get("admin_sa_fnox_key")
    if isinstance(value, str) and value:
        return value
    return None
