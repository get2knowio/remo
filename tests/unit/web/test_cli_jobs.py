"""Unit tests for `web/jobs.py` (023): the in-service CLI job runner.

These use REAL subprocesses (trivial `sh` scripts) — the runner's whole value
is its detach/exit-file/restart mechanics, which mocks cannot exercise.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from remo_cli.web.jobs import CliJobRunner, DuplicateJobError


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def runner(state_dir):
    return CliJobRunner(state_dir.settings())


class TestLifecycle:
    def test_success_finalizes_from_exit_file(self, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "echo hello-log"],
        )
        job_id = record["job_id"]
        assert record["state"] == "running"
        assert job_id.startswith("configure-")

        assert _wait(lambda: runner.status(job_id)["state"] != "running")
        status = runner.status(job_id)
        assert status["state"] == "succeeded"
        assert status["exit_code"] == 0
        assert "hello-log" in status["log_tail"]
        assert status["finished_at"]

    def test_failure_records_nonzero_exit(self, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "echo boom >&2; exit 3"],
        )
        assert _wait(lambda: runner.status(record["job_id"])["state"] != "running")
        status = runner.status(record["job_id"])
        assert status["state"] == "failed"
        assert status["exit_code"] == 3
        # stderr is merged into the log.
        assert "boom" in status["log_tail"]

    def test_unknown_job_is_none(self, runner):
        assert runner.status("configure-doesnotexist") is None

    def test_log_tail_is_truncated_and_ansi_stripped(self, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", r"printf '\033[31mred\033[0m\n'; yes filler | head -n 500"],
        )
        assert _wait(lambda: runner.status(record["job_id"])["state"] != "running")
        tail = runner.status(record["job_id"])["log_tail"]
        assert "\x1b" not in tail
        assert len(tail) <= 4000


class TestRestartRecovery:
    def test_fresh_runner_over_same_dir_sees_finished_job(self, state_dir, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "echo survived"],
        )
        assert _wait(lambda: (state_dir.settings().web_jobs_dir / f"{record['job_id']}.exit").exists())

        rehydrated = CliJobRunner(state_dir.settings())
        status = rehydrated.status(record["job_id"])
        assert status["state"] == "succeeded"
        assert "survived" in status["log_tail"]

    def test_dead_pid_without_exit_file_fails(self, state_dir, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "sleep 30"],
        )
        job_id = record["job_id"]
        jobs_dir = state_dir.settings().web_jobs_dir
        # Simulate a kill -9 of the whole session: no exit file ever appears.
        os.kill(record["pid"], 9)
        assert _wait(lambda: not _pid_alive(record["pid"]))
        (jobs_dir / f"{job_id}.exit").unlink(missing_ok=True)

        status = CliJobRunner(state_dir.settings()).status(job_id)
        assert status["state"] == "failed"
        assert status["exit_code"] is None
        assert "died without recording an exit" in status["log_tail"]


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie still answers signal 0; reap children we own.
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


class TestConcurrencyAndListing:
    def test_duplicate_running_job_is_refused_with_existing_id(self, runner):
        first = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "sleep 5"],
        )
        with pytest.raises(DuplicateJobError) as exc:
            runner.start(
                kind="configure", instance_id="i1", instance_name="dev",
                argv=["sh", "-c", "true"],
            )
        assert exc.value.job_id == first["job_id"]
        # A different kind or instance is fine.
        runner.start(kind="other", instance_id="i1", instance_name="dev", argv=["true"])
        runner.start(kind="configure", instance_id="i2", instance_name="dev2", argv=["true"])
        os.kill(first["pid"], 9)

    def test_list_jobs_is_per_instance_newest_first(self, runner):
        a = runner.start(kind="configure", instance_id="i1", instance_name="dev", argv=["true"])
        assert _wait(lambda: runner.status(a["job_id"])["state"] != "running")
        b = runner.start(kind="configure", instance_id="i1", instance_name="dev", argv=["true"])
        runner.start(kind="configure", instance_id="i2", instance_name="dev2", argv=["true"])

        jobs = runner.list_jobs("i1")
        assert [j["job_id"] for j in jobs] == [b["job_id"], a["job_id"]]
        assert all("log_tail" not in j for j in jobs)


class TestPrune:
    def test_old_finished_jobs_are_pruned_at_spawn(self, state_dir, runner):
        jobs_dir = state_dir.settings().web_jobs_dir
        jobs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        stale = {
            "job_id": "configure-stale0000000",
            "kind": "configure",
            "instance_id": "iX",
            "instance_name": "old",
            "argv": ["true"],
            "pid": 1,
            "state": "succeeded",
            "exit_code": 0,
            "started_at": "2020-01-01T00:00:00+00:00",
            "finished_at": "2020-01-01T00:01:00+00:00",
        }
        (jobs_dir / "configure-stale0000000.json").write_text(json.dumps(stale))
        (jobs_dir / "configure-stale0000000.log").write_text("old log")

        record = runner.start(kind="configure", instance_id="i1", instance_name="dev", argv=["true"])
        assert not (jobs_dir / "configure-stale0000000.json").exists()
        assert not (jobs_dir / "configure-stale0000000.log").exists()
        assert _wait(lambda: runner.status(record["job_id"])["state"] != "running")

    def test_running_jobs_are_never_pruned(self, state_dir, runner):
        record = runner.start(
            kind="configure", instance_id="i1", instance_name="dev",
            argv=["sh", "-c", "sleep 5"],
        )
        runner.start(kind="other", instance_id="i2", instance_name="dev2", argv=["true"])
        jobs_dir = state_dir.settings().web_jobs_dir
        assert (jobs_dir / f"{record['job_id']}.json").exists()
        os.kill(record["pid"], 9)
