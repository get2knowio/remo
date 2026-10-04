"""Decision logic for `remo resume` (spec 028).

``decide_resume`` is pure and covers every row of the decision table in
``specs/028-tab-resume/data-model.md``, in order, so each fallback is
table-testable. The two host-touching helpers (:func:`run_lookup`,
:func:`is_project_live`) are the only I/O, both bounded by a 5 s budget
(FR-015) and both degrading to "not live / failed" rather than raising —
resume must never be worse than ``remo shell``, and must never attach to a
session it has not seen live (FR-010: no blind attach, no new session).

No Click, no provider knowledge: the upgrade command a stale host needs is
passed in by the caller (``cli/shell.upgrade_command_hint``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from remo_cli.core.remo_host_client import (
    RemoHostClientError,
    SshTransportError,
    TabLookup,
    TabLookupUnsupported,
    ZellijState,
    list_sessions,
    lookup_tab,
)
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.core.tab_records import TabRecord
from remo_cli.models.host import KnownHost

LOOKUP_TIMEOUT_S = 5.0

#: ``unreachable`` is a lookup that failed at the SSH transport (exit 255,
#: timeout, spawn failure); ``failed`` is any other client error (malformed
#: reply, remo-host command error). They differ only in whether the rows 7-8
#: liveness call is worth making (#247).
LookupStatus = Literal["ok", "unsupported", "failed", "unreachable", "skipped"]


class ResumeReason(str, Enum):
    NO_IDENTITY = "no_identity"
    NO_RECORD = "no_record"
    HOST_GONE = "host_gone"
    NAME_MISMATCH = "name_mismatch"
    HOST_NOT_UPGRADED = "host_not_upgraded"
    SESSION_NOT_LIVE = "session_not_live"
    LOOKUP_FAILED = "lookup_failed"


@dataclass(frozen=True)
class ResumeDecision:
    """What `remo resume` does next.

    ``host_name`` is the registered host to connect to for attach/menu; for a
    ``shell`` decision it is the *recorded* host (kept only so the message can
    name it) and the caller connects via ``requested_name``.
    """

    action: Literal["attach", "menu", "shell"]
    host_name: str | None = None
    project: str | None = None
    reason: ResumeReason | None = None
    requested_name: str | None = None


def needs_project_liveness(
    *,
    lookup: TabLookup | None,
    lookup_status: LookupStatus,
    record: TabRecord | None,
) -> bool:
    """True when rows 7-8 apply, i.e. the host did not name the project itself.

    Keeps the row-5 fast path to a single extra round-trip (SC-004). A lookup
    that could not reach the host skips it: a second call over the same
    transport cannot succeed either, and would double the wait before the
    fallback (row 6a, #247).
    """
    if record is None or record.project is None:
        return False
    if lookup_status == "unreachable":
        return False
    host_named_project = (
        lookup_status == "ok" and lookup is not None and lookup.project is not None
    )
    return not host_named_project


def decide_resume(
    *,
    identity_present: bool,
    record: TabRecord | None,
    requested_name: str | None,
    host_registered: bool,
    lookup: TabLookup | None,
    lookup_status: LookupStatus,
    record_project_live: bool | None,
) -> ResumeDecision:
    # 1
    if not identity_present:
        return ResumeDecision("shell", requested_name=requested_name, reason=ResumeReason.NO_IDENTITY)
    # 2
    if record is None:
        return ResumeDecision("shell", requested_name=requested_name, reason=ResumeReason.NO_RECORD)
    # 3
    if requested_name is not None and requested_name != record.host:
        return ResumeDecision(
            "shell",
            host_name=record.host,
            requested_name=requested_name,
            reason=ResumeReason.NAME_MISMATCH,
        )
    # 4
    if not host_registered:
        return ResumeDecision(
            "shell",
            host_name=record.host,
            requested_name=requested_name,
            reason=ResumeReason.HOST_GONE,
        )

    host = record.host

    # 5-6: the host named a project for this tab.
    if lookup_status == "ok" and lookup is not None and lookup.project is not None:
        if lookup.zellij_state is ZellijState.ACTIVE:
            return ResumeDecision("attach", host_name=host, project=lookup.project)
        return ResumeDecision(
            "menu", host_name=host, project=lookup.project, reason=ResumeReason.SESSION_NOT_LIVE
        )

    # 6a: the host could not be reached, so the remembered project was not
    # checked; never attach to it unverified (FR-011), and don't claim it
    # stopped running — we only know the host didn't answer (#247).
    if lookup_status == "unreachable":
        return ResumeDecision("menu", host_name=host, reason=ResumeReason.LOOKUP_FAILED)

    # 7-8: no host-side answer; fall back to the workstation's remembered project.
    if record.project is not None:
        if record_project_live is True:
            return ResumeDecision("attach", host_name=host, project=record.project)
        # An unevaluated or failed liveness check counts as not live.
        return ResumeDecision(
            "menu", host_name=host, project=record.project, reason=ResumeReason.SESSION_NOT_LIVE
        )

    # 9-11: nothing to attach to.
    if lookup_status == "unsupported":
        return ResumeDecision("menu", host_name=host, reason=ResumeReason.HOST_NOT_UPGRADED)
    if lookup_status == "ok":
        return ResumeDecision("menu", host_name=host, reason=ResumeReason.NO_RECORD)
    return ResumeDecision("menu", host_name=host, reason=ResumeReason.LOOKUP_FAILED)


def resume_message(decision: ResumeDecision, *, upgrade_hint: str | None = None) -> str:
    """The one line printed before connecting (contracts/cli-resume.md)."""
    reason = decision.reason
    host = decision.host_name
    if reason is None:
        return f"Resuming {decision.project} on {host}"
    if reason is ResumeReason.NO_IDENTITY:
        return "This terminal exposes no tab identity — opening remo shell"
    if reason is ResumeReason.NO_RECORD:
        if decision.action == "menu":
            return f"Nothing recorded for this tab on {host} — opening its project menu"
        return "Nothing recorded for this tab — opening remo shell"
    if reason is ResumeReason.NAME_MISMATCH:
        return (
            f"This tab last used {host}, not {decision.requested_name} "
            f"— opening remo shell {decision.requested_name}"
        )
    if reason is ResumeReason.HOST_GONE:
        return f"{host} is no longer registered — opening remo shell"
    if reason is ResumeReason.HOST_NOT_UPGRADED:
        return (
            f"{host} can't resume a tab's project yet — run '{upgrade_hint}' "
            "— opening its project menu"
        )
    if reason is ResumeReason.SESSION_NOT_LIVE:
        return f"{decision.project} is no longer running on {host} — opening its project menu"
    return f"Couldn't ask {host} which project this tab used — opening its project menu"


def _ssh_prefix(host: KnownHost) -> list[str]:
    # Non-interactive (BatchMode, mirroring core/attach.py) and multiplexed, so
    # the interactive connection that follows reuses the master this opens.
    base = build_ssh_base_cmd(host, multiplex=True)
    return [base[0], "-o", "BatchMode=yes", *base[1:]]


def run_lookup(host: KnownHost, key: str) -> tuple[TabLookup | None, LookupStatus]:
    """Ask *host* which project this tab used, within the 5 s budget (FR-015)."""
    try:
        return lookup_tab(_ssh_prefix(host), key, timeout=LOOKUP_TIMEOUT_S), "ok"
    except TabLookupUnsupported:
        return None, "unsupported"
    except SshTransportError:
        return None, "unreachable"
    except RemoHostClientError:
        return None, "failed"


def is_project_live(host: KnownHost, project: str) -> bool:
    """True only if *host* lists *project* with an active zellij session."""
    try:
        entries = list_sessions(_ssh_prefix(host), timeout=LOOKUP_TIMEOUT_S)
    except RemoHostClientError:
        return False
    return any(e.name == project and e.zellij_state is ZellijState.ACTIVE for e in entries)
