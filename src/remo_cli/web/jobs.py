"""In-service CLI job runner (023): the web service running its own `remo` CLI.

The Docker image ships the full CLI (wheel + ansible-core + playbooks +
openssh-client), so host management from the console shells out to `remo
add/remove/configure` instead of reimplementing any of it. Short calls run
inline in the route; anything long (a configure play is minutes) runs through
:class:`CliJobRunner` as a **detached, restart-surviving, poll-driven job** —
the in-process analogue of remo-host's nohup+setsid+state-file idiom.

Design points (plan §S5):

* Detach: ``start_new_session=True`` puts the child in its own session, so a
  service restart neither kills it nor delivers it uvicorn's SIGTERM.
* The CHILD records its own exit code: argv is wrapped as
  ``sh -c '"$@"; rc=$?; printf %s "$rc" > "$REMO_JOB_EXIT_FILE.tmp" && mv -f
  "$REMO_JOB_EXIT_FILE.tmp" "$REMO_JOB_EXIT_FILE"' remo-web-job <argv…>``
  (argv passed positionally — nothing is ever interpolated into the shell
  string). The tmp-then-rename makes the exit file appear atomically, so a
  poll can never observe it created-but-empty. This is what makes restart
  recovery correct: a poll after a service restart finalizes from the exit
  file, not from a parent that no longer exists.
* Poll-driven, no watcher thread: a status read finalizes a running job when
  its exit file exists, and declares it failed when its pid is gone without
  one ("process died without recording an exit").
* All state lives on disk under ``<REMO_HOME>/web-jobs/`` (0700): per job a
  ``<job_id>.json`` record, ``<job_id>.log`` (stdout+stderr merged), and
  ``<job_id>.exit``. A fresh runner over the same directory (service restart)
  sees every job.
* One running job per ``(instance_id, kind)``; finished jobs pruned (keep 20,
  drop >7 days) at spawn time.

``kind`` is an open string and argv is caller-supplied, so provider
create/destroy jobs can reuse this runner unchanged later.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from remo_cli.web.config import WebSettings

logger = logging.getLogger("remo_cli.web.jobs")

_LOG_TAIL_CHARS = 4000
#: Bytes read from the end of the log per poll — enough margin over
#: _LOG_TAIL_CHARS to survive stripped ANSI sequences and multibyte chars.
_LOG_TAIL_READ_BYTES = 64 * 1024
_KEEP_FINISHED = 20
_KEEP_DAYS = 7

#: CSI/OSC escape sequences (ANSI colors, cursor movement) — stripped from
#: log tails server-side so the console renders plain text.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")

#: The child shell wrapper: run argv, then record the exit code where the
#: poll path can find it. Argv is passed positionally ("$@") — no caller
#: value is ever interpolated into this string. The exit code is written to
#: a sidecar and renamed into place so the exit file appears ATOMICALLY: a
#: plain `> file` redirection creates/truncates the file before printf
#: writes, and a poll landing in that window would read an empty file.
_WRAPPER = (
    '"$@"; rc=$?; printf %s "$rc" > "$REMO_JOB_EXIT_FILE.tmp"'
    ' && mv -f "$REMO_JOB_EXIT_FILE.tmp" "$REMO_JOB_EXIT_FILE"'
)


def _proc_start_ticks(pid: int) -> int | None:
    """The process's start time in clock ticks since boot (``/proc/<pid>/stat``
    field 22), or ``None`` when unreadable (process gone, or not Linux).

    Recorded at spawn and compared at liveness checks so a RECYCLED pid — the
    PID namespace resets on a container restart, and low pids are reused —
    is not mistaken for the still-running job (#023 review)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    try:
        # comm (field 2) may contain spaces/parens; fields resume after the
        # LAST ')' with field 3, so starttime (field 22) is token 19 there.
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (IndexError, ValueError):
        return None


class DuplicateJobError(Exception):
    """A job of this kind is already running for this instance."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id} is already running")
        self.job_id = job_id


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class CliJobRunner:
    """Detached CLI subprocess jobs with on-disk state (see module docstring)."""

    def __init__(self, settings: WebSettings) -> None:
        self._settings = settings
        self._dir = settings.web_jobs_dir
        # Serializes the duplicate check with the spawn: routes run in the
        # threadpool, so two concurrent POSTs could otherwise both pass
        # _running_job and start two jobs for the same (instance, kind).
        self._start_lock = threading.Lock()

    # -- spawn --------------------------------------------------------------

    def start(
        self,
        *,
        kind: str,
        instance_id: str,
        instance_name: str,
        argv: list[str],
    ) -> dict[str, Any]:
        """Spawn *argv* detached; returns the new job record.

        Raises :class:`DuplicateJobError` (carrying the existing job id) when
        a job of this *kind* is already running for *instance_id*.
        """
        with self._start_lock:
            existing = self._running_job(instance_id, kind)
            if existing is not None:
                raise DuplicateJobError(existing["job_id"])

            self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._prune()

            import uuid

            job_id = f"{kind}-{uuid.uuid4().hex[:12]}"
            exit_path = self._dir / f"{job_id}.exit"
            log_path = self._dir / f"{job_id}.log"

            child_env = dict(os.environ)
            # Ansible's filtered/pretty output is for humans at a TTY; a log
            # file wants plain lines (NOCOLOR) — the \r-emitting progress
            # rendering is avoided by the caller passing -v for configure runs.
            child_env["ANSIBLE_NOCOLOR"] = "1"
            child_env["REMO_JOB_EXIT_FILE"] = str(exit_path)
            identity = self._settings.ssh_identity_file
            if identity is not None:
                # Adopted mode: `remo` subprocesses authenticate with the
                # service key when the entry stores no identity (core/ssh.py
                # env seam).
                child_env["REMO_SSH_IDENTITY_FILE"] = identity

            log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                proc = subprocess.Popen(
                    ["sh", "-c", _WRAPPER, "remo-web-job", *argv],
                    stdout=log_fd,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                    env=child_env,
                )
            finally:
                os.close(log_fd)

            record: dict[str, Any] = {
                "job_id": job_id,
                "kind": kind,
                "instance_id": instance_id,
                "instance_name": instance_name,
                "argv": argv,
                "pid": proc.pid,
                "pid_start": _proc_start_ticks(proc.pid),
                "state": "running",
                "exit_code": None,
                "started_at": _now(),
                "finished_at": "",
            }
            self._write_record(record)
        logger.info("started %s job %s for %s (pid %d)", kind, job_id, instance_name, proc.pid)
        return record

    # -- read ---------------------------------------------------------------

    def status(self, job_id: str) -> dict[str, Any] | None:
        """The job record (finalized if its process has finished) + log_tail.

        Returns ``None`` for an unknown job id.
        """
        record = self._read_record(self._dir / f"{job_id}.json")
        if record is None:
            return None
        record = self._finalize_if_done(record)
        record["log_tail"] = self._log_tail(job_id)
        return record

    def list_jobs(self, instance_id: str) -> list[dict[str, Any]]:
        """All of *instance_id*'s jobs, newest-first (no log tails)."""
        jobs = [
            self._finalize_if_done(record)
            for record in self._all_records()
            if record.get("instance_id") == instance_id
        ]
        jobs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return jobs

    # -- internals ----------------------------------------------------------

    def _running_job(self, instance_id: str, kind: str) -> dict[str, Any] | None:
        for record in self._all_records():
            if record.get("instance_id") != instance_id or record.get("kind") != kind:
                continue
            if self._finalize_if_done(record).get("state") == "running":
                return record
        return None

    def _all_records(self) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        records = []
        for path in self._dir.glob("*.json"):
            record = self._read_record(path)
            if record is not None:
                records.append(record)
        return records

    def _read_record(self, path: Path) -> dict[str, Any] | None:
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        if not isinstance(doc, dict) or not isinstance(doc.get("job_id"), str):
            return None
        return doc

    def _write_record(self, record: dict[str, Any]) -> None:
        path = self._dir / f"{record['job_id']}.json"
        fd, tmp_str = tempfile.mkstemp(dir=self._dir, prefix=".job_tmp_")
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(record, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _finalize_if_done(self, record: dict[str, Any]) -> dict[str, Any]:
        """Poll-path finalization: exit file wins; a gone pid without one fails."""
        if record.get("state") != "running":
            return record
        job_id = record["job_id"]
        exit_path = self._dir / f"{job_id}.exit"
        if exit_path.exists():
            raw = ""
            try:
                raw = exit_path.read_text().strip()
            except OSError:
                pass
            if not raw:
                # The wrapper renames the exit file into place fully written,
                # so an empty one should not occur — but if it ever does,
                # keep polling rather than misreporting a succeeded job as
                # failed; the pid check below still catches a dead child.
                pass
            else:
                try:
                    exit_code: int | None = int(raw)
                except ValueError:
                    exit_code = None
                record["exit_code"] = exit_code
                record["state"] = "succeeded" if exit_code == 0 else "failed"
                record["finished_at"] = _now()
                self._write_record(record)
                return record
        pid = record.get("pid")
        if isinstance(pid, int) and self._pid_alive(pid, record.get("pid_start")):
            return record  # still running
        # No exit file and no process: it died before recording an exit
        # (e.g. SIGKILL, or a host reboot took the whole session).
        record["state"] = "failed"
        record["exit_code"] = None
        record["finished_at"] = _now()
        self._write_record(record)
        try:
            with open(self._dir / f"{job_id}.log", "a") as fh:
                fh.write("\n[remo-web] process died without recording an exit\n")
        except OSError:
            pass
        return record

    def _pid_alive(self, pid: int, recorded_start: Any) -> bool:
        """Is *pid* still the job's own live process?

        Three hazards beyond a bare ``kill(pid, 0)``: a dead child we never
        ``wait()``ed stays a ZOMBIE that still answers signal 0 (reap it); a
        recycled pid after a container restart answers signal 0 forever
        (compare the recorded /proc start time); an EPERM pid belongs to
        another user and is not our child either way.
        """
        try:
            waited, _status = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return False  # our dead child, now reaped
        except (ChildProcessError, OSError):
            pass  # not our child (e.g. after a service restart)
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        current = _proc_start_ticks(pid)
        if isinstance(recorded_start, int) and current is not None and current != recorded_start:
            return False  # recycled pid, not the job
        return True

    def _log_tail(self, job_id: str) -> str:
        path = self._dir / f"{job_id}.log"
        try:
            # Read only the file's tail: a -v ansible log grows to many MB
            # and this runs on every 2s status poll.
            with open(path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - _LOG_TAIL_READ_BYTES))
                raw = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        return _strip_ansi(raw)[-_LOG_TAIL_CHARS:]

    def _prune(self) -> None:
        """Drop finished jobs beyond the newest ``_KEEP_FINISHED`` or older
        than ``_KEEP_DAYS`` days. Best-effort; running jobs are never touched."""
        finished = [
            record
            for record in (self._finalize_if_done(r) for r in self._all_records())
            if record.get("state") != "running"
        ]
        finished.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        cutoff = datetime.now(UTC) - timedelta(days=_KEEP_DAYS)
        for index, record in enumerate(finished):
            too_old = False
            try:
                started = datetime.fromisoformat(record.get("started_at", ""))
                too_old = started < cutoff
            except ValueError:
                too_old = True
            if index >= _KEEP_FINISHED or too_old:
                for suffix in (".json", ".log", ".exit"):
                    (self._dir / f"{record['job_id']}{suffix}").unlink(missing_ok=True)
