"""Mirror-identity marker (`web-identity/mirror-meta.json`) accessor (023).

Extracted from `web/api/setup.py` so the registry-admin API (which mutates the
service registry from the browser) and the setup API (workstation pushes)
share one writer. The marker is ADVISORY: reads degrade to ``None`` and writes
are best-effort — a marker failure must never fail the registry mutation it
records (contracts/setup-status-marker.md "Failure & precedence").

File contract (additive over 017's ``{generation, last_push}``):

```json
{
  "generation": 9,
  "last_push":   {"at": "...", "workstation": "..."},
  "last_change": {"at": "...", "origin": "push" | "web", "workstation": "..." | null}
}
```

``last_push`` is written only by push-origin changes (setup-API PUTs) and
preserved verbatim otherwise, so pre-023 `/setup/status` consumers see
identical data. ``last_change`` is written on EVERY registry mutation and is
what `remo web sync` and the console's unsynced badge key off.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from remo_cli.web.config import WebSettings

logger = logging.getLogger("remo_cli.web.mirror_meta")


def read_mirror_meta(settings: WebSettings) -> dict[str, Any] | None:
    """Read the mirror-identity marker.

    Returns the parsed document, or ``None`` when the file is absent,
    unreadable, or corrupt (data-model.md §3: a missing/unreadable marker is a
    safe default).
    """
    try:
        raw = settings.mirror_meta_path.read_text()
    except OSError:
        return None
    try:
        doc = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    return doc


def record_change(
    settings: WebSettings,
    *,
    origin: Literal["web", "push"],
    workstation: str | None = None,
) -> int | None:
    """Record one registry mutation: bump the generation, stamp ``last_change``.

    ``origin="push"`` additionally rewrites ``last_push`` (the *workstation*
    label is untrusted display text, stored verbatim, never acted on); any
    other origin preserves the existing ``last_push`` verbatim. Best-effort:
    returns the new generation, or ``None`` when the write failed (the caller's
    registry mutation already succeeded — the marker is advisory).
    """
    existing = read_mirror_meta(settings)
    current_generation = 0
    if existing is not None and isinstance(existing.get("generation"), int):
        current_generation = existing["generation"]
    new_generation = current_generation + 1

    now = datetime.now(UTC).isoformat()
    doc: dict[str, Any] = {"generation": new_generation}
    if origin == "push":
        doc["last_push"] = {"at": now, "workstation": workstation or "unknown"}
    elif existing is not None and isinstance(existing.get("last_push"), dict):
        doc["last_push"] = existing["last_push"]
    doc["last_change"] = {"at": now, "origin": origin, "workstation": workstation}

    try:
        _write_doc(settings.mirror_meta_path, doc)
    except OSError as exc:
        logger.warning("mirror-meta write failed (registry mutation succeeded): %s", exc)
        return None
    return new_generation


def _write_doc(path: Path, doc: dict[str, Any]) -> None:
    """Atomic write via same-directory temp file + `os.replace`."""
    dir_ = path.parent
    dir_.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, prefix=".mirror_meta_tmp_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
