"""Audit log fetch + render for `remo audit <instance>`.

The broker writes JSON-lines to /var/log/remo-broker/audit.log on the instance
(per research R7). This module SSHes to the instance and renders the log as a
table (default) or raw JSON (`--json`).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class AuditLine:
    ts: str
    project: str | None
    secret: str | None
    decision: str
    reason: str
    cache: str | None
    raw: dict[str, Any]


class AuditError(RuntimeError):
    """Raised when the audit log can't be fetched or parsed."""


def fetch(
    instance_host: str,
    instance_user: str,
    tail: int | None,
    since: timedelta | None,
) -> list[AuditLine]:
    """SSH to the instance and pull the audit log lines.

    Returns parsed AuditLine entries. Raises AuditError if the broker isn't
    installed (audit log missing) or the SSH call fails.
    """
    log_path = "/var/log/remo-broker/audit.log"
    if tail is not None and tail > 0:
        remote_cmd = f"sudo tail -n {int(tail)} {log_path}"
    else:
        remote_cmd = f"sudo cat {log_path}"

    target = f"{instance_user}@{instance_host}" if instance_user else instance_host
    proc = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=10", target, remote_cmd],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "No such file" in stderr or "cannot access" in stderr:
            raise AuditError(
                f"audit log not found at {log_path} on {instance_host}; "
                "is the broker installed?"
            )
        raise AuditError(f"failed to read audit log: {stderr or '(no stderr)'}")

    lines = _parse_lines(proc.stdout)
    if since is not None:
        cutoff = datetime.now(timezone.utc) - since
        lines = [ln for ln in lines if _parse_ts(ln.ts) >= cutoff]
    return lines


def _parse_lines(text: str) -> list[AuditLine]:
    out: list[AuditLine] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        out.append(
            AuditLine(
                ts=str(data.get("ts", "")),
                project=data.get("project"),
                secret=data.get("secret"),
                decision=str(data.get("decision", "unknown")),
                reason=str(data.get("reason", "")),
                cache=data.get("cache"),
                raw=data,
            )
        )
    return out


def _parse_ts(ts: str) -> datetime:
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        result = datetime.fromisoformat(ts)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if result.tzinfo is None:
        # Bare-ISO timestamps without an offset are interpreted as UTC so the
        # `_parse_ts(ln.ts) >= cutoff` comparison in `fetch()` doesn't raise
        # "can't compare offset-naive and offset-aware datetimes".
        result = result.replace(tzinfo=timezone.utc)
    return result


_DUR_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)
_DUR_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


def parse_duration(spec: str) -> timedelta:
    """Parse `5m` / `2h` / `7d` / `30s` into a timedelta."""
    m = _DUR_RE.match(spec.strip())
    if not m:
        raise ValueError(f"invalid duration {spec!r}; expected forms like 5m, 2h, 7d, 30s")
    value, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(**{_DUR_UNITS[unit]: value})


def render_table(lines: list[AuditLine]) -> str:
    """Render audit lines as a fixed-width grouped-by-project table."""
    if not lines:
        return "(no audit records)"

    cols = ("ts", "project", "secret", "decision", "reason", "cache")
    rows = [
        (
            ln.ts,
            ln.project or "-",
            ln.secret or "-",
            ln.decision,
            ln.reason or "-",
            ln.cache or "-",
        )
        for ln in lines
    ]
    widths = [max(len(str(r[i])) for r in (rows + [cols])) for i in range(len(cols))]

    def fmt_row(r: tuple[str, ...]) -> str:
        return "  ".join(str(r[i]).ljust(widths[i]) for i in range(len(cols)))

    out_lines = [fmt_row(cols), fmt_row(tuple("-" * w for w in widths))]
    for r in rows:
        out_lines.append(fmt_row(r))
    return "\n".join(out_lines)
