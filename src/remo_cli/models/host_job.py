"""Data models for detached ``remo-host`` jobs (clone / rebuild).

Produced by parsing the JSON payloads of ``remo-host projects clone``,
``projects rebuild`` (both return a job *reference*, 202-style) and
``remo-host jobs status --job ID --json`` (see
``specs/010-web-session-interface/contracts/remo-host-protocol.md``).

Parsing mirrors :class:`~remo_cli.models.capability.RemoteCapability`:
tolerant of unknown extra keys and of garbage in the descriptive fields,
but the one load-bearing field of each payload (``job_id`` for a ref,
``state`` for a status) raises :class:`ValueError` when missing or
unrecognized — a ref without an id cannot be polled, and an unknown state
silently coerced to "running" would poll forever.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobState(str, Enum):
    """Lifecycle state of a detached ``remo-host`` job."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class JobRef:
    """Reference to a detached job, returned by clone/rebuild (202-style)."""

    job_id: str
    kind: str = ""
    project: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> JobRef:
        """Parse a job-ref payload. Raises :class:`ValueError` on a missing
        or empty ``job_id``; ``kind``/``project`` degrade to ``""``."""
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(
                f"job ref payload has invalid 'job_id': expected a non-empty string, got {job_id!r}"
            )
        return cls(
            job_id=job_id,
            kind=str(data.get("kind", "")),
            project=str(data.get("project", "")),
        )


def _coerce_exit_code(value: object) -> int | None:
    """Coerce ``exit_code`` to an int, degrading to ``None`` (still running /
    unknown). bool is rejected explicitly (int subclass)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


@dataclass
class JobStatus:
    """Point-in-time status of a detached job, from ``jobs status --json``."""

    state: JobState
    exit_code: int | None = None
    started_at: str = ""
    finished_at: str = ""
    log_tail: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> JobStatus:
        """Parse a ``jobs status --json`` payload.

        Raises :class:`ValueError` when ``state`` is missing or not one of
        the known :class:`JobState` values (boundary validation of the
        load-bearing field); every other field degrades to a safe default.
        """
        raw_state = data.get("state")
        try:
            state = JobState(raw_state)
        except ValueError:
            raise ValueError(
                "job status payload has invalid 'state': expected one of "
                f"{[s.value for s in JobState]}, got {raw_state!r}"
            ) from None
        return cls(
            state=state,
            exit_code=_coerce_exit_code(data.get("exit_code")),
            started_at=str(data.get("started_at") or ""),
            finished_at=str(data.get("finished_at") or ""),
            log_tail=str(data.get("log_tail") or ""),
        )
