"""Data model for a live host statistics snapshot.

Produced by parsing the JSON payload of ``remo-host host stats --json`` (see
``specs/010-web-session-interface/contracts/remo-host-protocol.md``). This is
external/remote input from a shell script assembling numbers out of
``/proc``/``/sys``, so parsing is deliberately tolerant: a missing or garbage
field degrades to a safe default (zero / empty list) rather than failing the
whole snapshot — a host with one broken hwmon sensor must still report its
CPU and memory numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _as_int(value: object, default: int = 0) -> int:
    """Coerce *value* to a non-negative int, degrading to *default*.

    bool is rejected explicitly (it is an int subclass, but ``true`` is
    never a meaningful byte count).
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return default
    return default


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


@dataclass
class DiskUsage:
    """One ``disks[]`` entry: usage of a single mounted filesystem."""

    mount: str
    size_bytes: int = 0
    used_bytes: int = 0
    avail_bytes: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> DiskUsage:
        return cls(
            mount=str(data.get("mount", "")),
            size_bytes=_as_int(data.get("size_bytes")),
            used_bytes=_as_int(data.get("used_bytes")),
            avail_bytes=_as_int(data.get("avail_bytes")),
        )


@dataclass
class TempReading:
    """One ``temps[]`` entry: a single hardware temperature sensor reading."""

    name: str
    label: str
    celsius: float

    @classmethod
    def from_dict(cls, data: dict) -> TempReading:
        return cls(
            name=str(data.get("name", "")),
            label=str(data.get("label", "")),
            celsius=_as_float(data.get("celsius")),
        )


@dataclass
class HostStats:
    """A point-in-time host statistics snapshot (no time series, FR: live only).

    All fields default to zero/empty so a partially-broken host payload
    still yields a usable (if incomplete) snapshot; :meth:`from_dict`
    never raises on missing or garbage fields.
    """

    uptime_s: float = 0.0
    load_1: float = 0.0
    load_5: float = 0.0
    load_15: float = 0.0
    cpu_count: int = 0
    cpu_used_pct: float = 0.0
    mem_total: int = 0
    mem_used: int = 0
    mem_available: int = 0
    swap_total: int = 0
    swap_used: int = 0
    disks: list[DiskUsage] = field(default_factory=list)
    temps: list[TempReading] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> HostStats:
        """Parse a JSON-decoded ``host stats --json`` payload.

        Unknown extra keys are ignored (additive-compatible per R2);
        missing or garbage fields degrade to safe defaults. List entries
        that are not JSON objects are skipped; a disk entry without a
        usable ``mount`` and a temp entry without a numeric ``celsius``
        are skipped too (a phantom 0-degree reading would mislead more
        than an absent one).
        """
        raw_disks = data.get("disks")
        disks: list[DiskUsage] = []
        if isinstance(raw_disks, list):
            for raw in raw_disks:
                if not isinstance(raw, dict):
                    continue
                disk = DiskUsage.from_dict(raw)
                if disk.mount:
                    disks.append(disk)

        raw_temps = data.get("temps")
        temps: list[TempReading] = []
        if isinstance(raw_temps, list):
            for raw in raw_temps:
                if not isinstance(raw, dict):
                    continue
                celsius = raw.get("celsius")
                if isinstance(celsius, bool) or not isinstance(celsius, (int, float, str)):
                    continue
                if isinstance(celsius, str):
                    try:
                        float(celsius.strip())
                    except ValueError:
                        continue
                temps.append(TempReading.from_dict(raw))

        return cls(
            uptime_s=_as_float(data.get("uptime_s")),
            load_1=_as_float(data.get("load_1")),
            load_5=_as_float(data.get("load_5")),
            load_15=_as_float(data.get("load_15")),
            cpu_count=_as_int(data.get("cpu_count")),
            cpu_used_pct=_as_float(data.get("cpu_used_pct")),
            mem_total=_as_int(data.get("mem_total")),
            mem_used=_as_int(data.get("mem_used")),
            mem_available=_as_int(data.get("mem_available")),
            swap_total=_as_int(data.get("swap_total")),
            swap_used=_as_int(data.get("swap_used")),
            disks=disks,
            temps=temps,
        )
