"""Install record (``<REMO_HOME>/install.json``) and install-origin detection.

The record is written by ``install.sh`` (spec 031, contracts/install-record.md)
and read here so ``remo self-update`` and the update notice know what was
installed, from where, and on which channel. No provider names: plugins are
opaque install names.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any

from remo_cli.core.config import resolve_remo_home

RECORD_FORMAT = 1
_VALUE_RE = re.compile(r"^[A-Za-z0-9._:/@+=,~%-]+$")
_CHANNELS = ("stable", "prerelease")
_INSTALLERS = ("uv", "pipx")
_EXTRAS = ("web",)


@dataclass(frozen=True)
class InstallRecord:
    repo: str
    channel: str
    version: str
    installer: str
    extras: tuple[str, ...]
    plugins: tuple[str, ...]
    with_pkg: tuple[str, ...]
    with_url: tuple[str, ...]
    updated_at: str
    format: int = RECORD_FORMAT


class InstallOrigin(Enum):
    RECORDED = "recorded"
    SOURCE = "source"
    PYPI = "pypi"
    UNKNOWN = "unknown"


def record_path() -> Path:
    return resolve_remo_home() / "install.json"


def _ok(value: object) -> bool:
    return isinstance(value, str) and bool(_VALUE_RE.match(value))


def _str_list(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(_ok(v) for v in value):
        raise ValueError(f"'{key}' must be a list of plain values")
    return tuple(value)


def _parse(data: object) -> InstallRecord:
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if data.get("format") != RECORD_FORMAT:
        raise ValueError(f"unsupported format {data.get('format')!r}")
    for key in ("repo", "channel", "version", "installer", "updated_at"):
        if not _ok(data.get(key)):
            raise ValueError(f"'{key}' is missing or has disallowed characters")
    if data["channel"] not in _CHANNELS:
        raise ValueError(f"unknown channel {data['channel']!r}")
    if data["installer"] not in _INSTALLERS:
        raise ValueError(f"unknown installer {data['installer']!r}")
    extras = _str_list(data, "extras")
    if any(e not in _EXTRAS for e in extras):
        raise ValueError("unknown extra in 'extras'")
    return InstallRecord(
        repo=data["repo"],
        channel=data["channel"],
        version=data["version"],
        installer=data["installer"],
        extras=extras,
        plugins=_str_list(data, "plugins"),
        with_pkg=_str_list(data, "with_pkg"),
        with_url=_str_list(data, "with_url"),
        updated_at=data["updated_at"],
    )


def load_record() -> tuple[InstallRecord | None, str | None]:
    """``(record, None)`` when valid; ``(None, None)`` when no file exists;
    ``(None, reason)`` when a file exists but cannot be used."""
    path = record_path()
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"{path} is unreadable: {exc}"
    try:
        return _parse(data), None
    except ValueError as exc:
        return None, f"{path} is invalid: {exc}"


def _distribution() -> metadata.Distribution:
    return metadata.distribution("remo-cli")


def detect_origin() -> InstallOrigin:
    """Classify how this remo was installed (PEP 610 for the non-recorded cases)."""
    record, reason = load_record()
    if record is not None:
        return InstallOrigin.RECORDED
    if reason is not None:
        return InstallOrigin.UNKNOWN
    try:
        raw = _distribution().read_text("direct_url.json")
    except Exception:  # PackageNotFoundError or an unreadable dist-info
        return InstallOrigin.UNKNOWN
    if raw is None:
        return InstallOrigin.PYPI
    try:
        info = json.loads(raw)
    except ValueError:
        return InstallOrigin.UNKNOWN
    if isinstance(info, dict):
        if "dir_info" in info:
            return InstallOrigin.SOURCE
        if "url" not in info:
            return InstallOrigin.PYPI
    return InstallOrigin.UNKNOWN
