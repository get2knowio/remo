"""Tests for remo_cli.providers.self_update (spec 031, contracts/self-update-cli.md)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError, UserAbortedError
from remo_cli.core.install_record import InstallOrigin, InstallRecord
from remo_cli.core.version import ReleaseLookupError
from remo_cli.providers import self_update as su

SWITCH = "curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash"


def _record(channel="stable", **kw):
    base = dict(
        repo="o/r", channel=channel, version="1.0.0", installer="uv",
        extras=("web",), plugins=("ec2",), with_pkg=("foo==1.0",),
        with_url=("https://example.com/x.whl",), updated_at="2026-01-01T00:00:00Z",
    )
    base.update(kw)
    return InstallRecord(**base)


class Harness:
    def __init__(self, monkeypatch, origin, record=None, reason=None, current="1.0.0"):
        monkeypatch.setattr(su, "detect_origin", lambda: origin)
        monkeypatch.setattr(su, "load_record", lambda: (record, reason))
        monkeypatch.setattr(su, "get_current_version", lambda: current)
        self.ran: list[tuple[list[str], dict]] = []
        self.fetched: list[str] = []
        self.prompts: list[str] = []
        self.answer = True
        self.rc = 0
        self.newest = "2.0.0"
        self.lookup_calls: list[tuple[str, str]] = []

    def runner(self, argv, **kw):
        self.ran.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, self.rc)

    def fetch(self, url):
        self.fetched.append(url)
        return "#!/bin/bash\n"

    def confirm(self, prompt, default=False):
        self.prompts.append(prompt)
        return self.answer

    def lookup(self, repo, channel):
        self.lookup_calls.append((repo, channel))
        if isinstance(self.newest, Exception):
            raise self.newest
        return self.newest

    def run(self, **kw):
        kw.setdefault("check", False)
        kw.setdefault("assume_yes", False)
        return su.run_self_update(
            confirm=self.confirm, runner=self.runner, fetch=self.fetch, lookup=self.lookup, **kw
        )


def test_recorded_stable_runs_installer_with_recorded_set(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.run(assume_yes=True)
    assert h.fetched == ["https://raw.githubusercontent.com/o/r/v2.0.0/install.sh"]
    argv, kw = h.ran[0]
    assert argv[0] == "bash"
    assert argv[2:] == [
        "--version", "2.0.0", "--with", "web,ec2",
        "--with-pkg", "foo==1.0", "--with-url", "https://example.com/x.whl",
    ]
    assert kw["env"]["REMO_REPO_SLUG"] == "o/r"
    assert h.lookup_calls == [("o/r", "stable")]
    assert h.prompts == []


def test_recorded_prerelease_uses_rc_tag_and_flag(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record("prerelease", extras=(), plugins=(), with_pkg=(), with_url=()), current="1.0.0rc1")
    h.newest = "1.0.0rc2"
    h.run(assume_yes=True)
    assert h.fetched == ["https://raw.githubusercontent.com/o/r/rc-1.0.0rc2/install.sh"]
    assert h.ran[0][0][2:] == ["--prerelease", "1.0.0rc2"]


def test_prompt_shows_command_and_declining_aborts(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.answer = False
    with pytest.raises(UserAbortedError):
        h.run()
    assert "--version 2.0.0" in h.prompts[0]
    assert h.ran == [] and h.fetched == []


def test_accepting_prompt_runs(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.run()
    assert len(h.ran) == 1


def test_up_to_date_runs_nothing(monkeypatch, capsys):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.newest = "1.0.0"
    h.run(assume_yes=True)
    assert h.ran == [] and h.fetched == []
    assert "remo 1.0.0 is the newest stable release" in capsys.readouterr().out


def test_check_reports_without_running(monkeypatch, capsys):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.run(check=True)
    assert h.ran == [] and h.fetched == [] and h.prompts == []
    assert "remo 1.0.0 → 2.0.0 (stable)" in capsys.readouterr().out


def test_lookup_failure_is_operation_failed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.newest = ReleaseLookupError("GitHub rate limit exceeded")
    with pytest.raises(OperationFailedError, match="rate limit"):
        h.run(assume_yes=True)


def test_download_failure_is_operation_failed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())

    def boom(url):
        raise OperationFailedError("could not download installer")

    with pytest.raises(OperationFailedError):
        su.run_self_update(
            check=False, assume_yes=True, confirm=h.confirm, runner=h.runner, fetch=boom, lookup=h.lookup
        )
    assert h.ran == []


def test_installer_nonzero_exit_is_operation_failed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.rc = 1
    with pytest.raises(OperationFailedError, match="exit"):
        h.run(assume_yes=True)


def test_installer_temp_file_removed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.run(assume_yes=True)
    assert not Path(h.ran[0][0][1]).exists()


def test_pypi_guidance_derives_with(monkeypatch, capsys):
    h = Harness(monkeypatch, InstallOrigin.PYPI)
    monkeypatch.setattr(su, "detect_with_names", lambda: ["ec2", "web"])
    h.run(assume_yes=True)
    out = capsys.readouterr().out
    assert "PyPI no longer receives remo releases" in out
    assert f"{SWITCH.replace('| bash', '| bash -s -- --with ec2,web')}" in out
    assert h.ran == [] and h.lookup_calls == []


def test_pypi_guidance_omits_with_when_empty(monkeypatch, capsys):
    h = Harness(monkeypatch, InstallOrigin.PYPI)
    monkeypatch.setattr(su, "detect_with_names", lambda: [])
    h.run(check=True)
    out = capsys.readouterr().out
    assert SWITCH in out and "--with" not in out


def test_source_guidance(monkeypatch, capsys):
    h = Harness(monkeypatch, InstallOrigin.SOURCE)
    h.run()
    assert "git pull && uv sync" in capsys.readouterr().out
    assert h.ran == []


@pytest.mark.parametrize("reason", ["bad json", None])
def test_unknown_is_precondition_error(monkeypatch, reason):
    h = Harness(monkeypatch, InstallOrigin.UNKNOWN, reason=reason)
    with pytest.raises(PreconditionError) as ei:
        h.run()
    msg = str(ei.value)
    assert SWITCH in msg and "git pull && uv sync" in msg
    if reason:
        assert reason in msg


def test_detect_with_names(monkeypatch):
    class D:
        def __init__(self, n):
            self.metadata = {"Name": n}

    monkeypatch.setattr(
        su.metadata, "distributions",
        lambda: [D("remo-provider-hetzner"), D("remo-provider-ec2"), D("requests")],
    )
    monkeypatch.setattr(su.importlib.util, "find_spec", lambda n: object() if n == "fastapi" else None)
    assert su.detect_with_names() == ["ec2", "hetzner", "web"]
    monkeypatch.setattr(su.importlib.util, "find_spec", lambda n: None)
    assert su.detect_with_names() == ["ec2", "hetzner"]


def test_default_fetch_error_and_success(monkeypatch):
    import io
    import urllib.error

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(su.urllib.request, "urlopen", lambda *a, **k: R(b"echo hi"))
    assert su._fetch_installer("https://x") == "echo hi"

    def bad(*a, **k):
        raise urllib.error.URLError("nope")

    monkeypatch.setattr(su.urllib.request, "urlopen", bad)
    with pytest.raises(OperationFailedError, match="nope"):
        su._fetch_installer("https://x")


def test_installer_env_twins_are_not_forwarded(monkeypatch):
    """An exported REMO_PRERELEASE=true would turn a stable `--version` into a
    refusal, and REMO_WITHOUT would strip recorded plugins on every update."""
    for k, v in {"REMO_PRERELEASE": "true", "REMO_WITHOUT": "ec2", "REMO_WITH": "x",
                 "REMO_VERSION": "0.1.0", "REMO_HOME": "/keep/me"}.items():
        monkeypatch.setenv(k, v)
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    h.run(assume_yes=True)
    env = h.ran[0][1]["env"]
    for k in ("REMO_PRERELEASE", "REMO_WITHOUT", "REMO_WITH", "REMO_VERSION"):
        assert k not in env
    assert env["REMO_HOME"] == "/keep/me"
    assert env["REMO_REPO_SLUG"] == "o/r"


def test_runner_oserror_is_operation_failed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())

    def no_bash(argv, **kw):
        h.ran.append((list(argv), kw))
        raise FileNotFoundError("bash")

    with pytest.raises(OperationFailedError, match="Could not run the installer"):
        su.run_self_update(
            check=False, assume_yes=True, confirm=h.confirm, runner=no_bash,
            fetch=h.fetch, lookup=h.lookup,
        )
    assert not Path(h.ran[0][0][1]).exists()


def test_unwritable_tmpdir_is_operation_failed(monkeypatch):
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())

    def boom(**kw):
        raise PermissionError("read-only")

    monkeypatch.setattr(su.tempfile, "mkstemp", boom)
    with pytest.raises(OperationFailedError, match="read-only"):
        h.run(assume_yes=True)
    assert h.ran == []


def test_output_is_flushed_before_the_installer_runs(monkeypatch):
    """A piped stdout is block-buffered: unflushed, the "remo X → Y" line
    surfaced after the installer's own output and read like a second report."""
    import io

    class Tracking(io.StringIO):
        flushes = 0

        def flush(self):
            Tracking.flushes += 1
            super().flush()

    out, err = Tracking(), Tracking()
    monkeypatch.setattr(su.sys, "stdout", out)
    monkeypatch.setattr(su.sys, "stderr", err)
    h = Harness(monkeypatch, InstallOrigin.RECORDED, _record())
    seen_at_run: list[tuple[str, int]] = []

    def runner(argv, **kw):
        seen_at_run.append((out.getvalue(), Tracking.flushes))
        return subprocess.CompletedProcess(argv, 0)

    h.runner = runner
    h.run(assume_yes=True)
    text, flushes = seen_at_run[0]
    assert "→ 2.0.0" in text
    assert flushes >= 2  # stdout and stderr, before the hand-over
