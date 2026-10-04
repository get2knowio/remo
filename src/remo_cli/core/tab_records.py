"""Workstation-side store for per-tab resume records and the tab secret.

Spec 028 R5. Everything lives under ``REMO_HOME``: ``tab-secret`` (the HMAC
secret, 0600), ``tab-records.json`` and a ``tab-records.lock`` sidecar. A
missing or malformed records file reads as empty — resume must never be worse
than ``remo shell`` — while write failures surface as :class:`TabRecordError`
so callers can warn and carry on connecting.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from remo_cli.core.atomic_file import advisory_lock, atomic_write_json, atomic_write_text
from remo_cli.core.config import get_remo_home

RETENTION_DAYS = 30
MAX_RECORDS = 500
_FORMAT_VERSION = 1
_LOCK_TIMEOUT_S = 5.0


class TabRecordError(Exception):
    """The tab store could not be read-modify-written (never an OSError)."""


@dataclass(frozen=True)
class TabRecord:
    host: str
    project: str | None
    recorded_at: datetime


def _records_path() -> Path:
    return get_remo_home() / "tab-records.json"


def _secret_path() -> Path:
    return get_remo_home() / "tab-secret"


def _lock_path() -> Path:
    return get_remo_home() / "tab-records.lock"


@contextmanager
def _lock() -> Iterator[None]:
    # Only a failure to open the sidecar gets the "lock" message; OSErrors from
    # the guarded block keep flowing to the caller's own translation.
    with ExitStack() as stack:
        try:
            stack.enter_context(
                advisory_lock(
                    _lock_path(),
                    timeout_s=_LOCK_TIMEOUT_S,
                    file_mode=0o600,
                    busy_error=lambda: TabRecordError(
                        "tab records are busy — another remo process is writing"
                    ),
                )
            )
        except OSError as e:
            raise TabRecordError(f"cannot open the tab record lock: {e}") from e
        yield


def get_secret() -> bytes:
    """Return the workstation secret, creating it (0600) on first use."""
    try:
        path = _secret_path()
        try:
            text = path.read_text(encoding="utf-8").strip()
            if len(text) == 64:
                return bytes.fromhex(text)
        except (OSError, ValueError):
            pass
        with _lock():
            # Re-check under the lock: another tab may have just created it.
            try:
                text = path.read_text(encoding="utf-8").strip()
                if len(text) == 64:
                    return bytes.fromhex(text)
            except (OSError, ValueError):
                pass
            return _write_new_secret()
    except OSError as e:
        raise TabRecordError(f"cannot create the tab secret: {e}") from e


def _write_new_secret() -> bytes:
    secret = secrets.token_bytes(32)
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, secret.hex(), mode=0o600)
    return secret


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_entry(raw: object) -> TabRecord | None:
    if not isinstance(raw, dict):
        return None
    host = raw.get("host")
    project = raw.get("project")
    when = _parse_time(raw.get("recorded_at"))
    if not isinstance(host, str) or not host or when is None:
        return None
    if project is not None and not isinstance(project, str):
        return None
    return TabRecord(host=host, project=project, recorded_at=when)


def _read_all() -> dict[str, TabRecord]:
    try:
        data = json.loads(_records_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _FORMAT_VERSION:
        return {}
    raw_records = data.get("records")
    if not isinstance(raw_records, dict):
        return {}
    out: dict[str, TabRecord] = {}
    for key, raw in raw_records.items():
        entry = _parse_entry(raw)
        if isinstance(key, str) and entry is not None:
            out[key] = entry
    return out


def _write_all(records: dict[str, TabRecord]) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=RETENTION_DAYS)
    kept = {k: r for k, r in records.items() if r.recorded_at >= cutoff}
    newest = sorted(kept.items(), key=lambda kv: kv[1].recorded_at, reverse=True)
    kept = dict(newest[:MAX_RECORDS])
    payload = {
        "version": _FORMAT_VERSION,
        "records": {
            k: {
                "host": r.host,
                "project": r.project,
                "recorded_at": r.recorded_at.isoformat(),
            }
            for k, r in kept.items()
        },
    }
    path = _records_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload, indent=2, trailing_newline=True)


def load(key: str) -> TabRecord | None:
    return _read_all().get(key)


def record(key: str, host: str, project: str | None) -> None:
    try:
        with _lock():
            records = _read_all()
            records[key] = TabRecord(host=host, project=project, recorded_at=datetime.now(UTC))
            _write_all(records)
    except OSError as e:
        raise TabRecordError(f"cannot write tab records: {e}") from e


def forget(key: str) -> bool:
    try:
        with _lock():
            records = _read_all()
            existed = key in records
            if existed:
                del records[key]
                _write_all(records)
            return existed
    except OSError as e:
        raise TabRecordError(f"cannot write tab records: {e}") from e


def forget_all() -> None:
    """Empty the records and rotate the secret so old host-side keys orphan."""
    try:
        with _lock():
            _write_all({})
            _write_new_secret()
    except OSError as e:
        raise TabRecordError(f"cannot write tab records: {e}") from e
