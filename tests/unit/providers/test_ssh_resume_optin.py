"""``remo local setup --allow-ssh-resume`` (spec 030, contracts/ssh-resume-optin.md).

Every subprocess goes through an injected fake runner and ``/etc`` is a temp
directory, so no test ever runs sudo or touches the real SSH configuration.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from remo_cli.core.errors import OperationFailedError
from remo_cli.providers import local

CONTENT = local.SSH_DROPIN_CONTENT


class FakeRunner:
    """Records argv and applies `install` / `rm` to the temp /etc."""

    def __init__(
        self,
        sshd_rc: int = 0,
        reload_rcs: tuple[int, ...] = (0, 0),
        sshd: str = local.SSHD_BINARY,
    ) -> None:
        self.calls: list[list[str]] = []
        self.sshd = sshd
        self.sshd_rc = sshd_rc
        self.reload_rcs = list(reload_rcs)

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert argv[0] == "sudo", "only sudo may be invoked"
        rest = argv[1:]
        if rest[0] == "install":
            shutil.copyfile(rest[3], rest[4])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if rest[0] == "rm":
            Path(rest[2]).unlink(missing_ok=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if rest[0] == self.sshd:
            return subprocess.CompletedProcess(argv, self.sshd_rc, "", "bad directive" if self.sshd_rc else "")
        if rest[0] == "systemctl":
            return subprocess.CompletedProcess(argv, self.reload_rcs.pop(0), "", "")
        raise AssertionError(argv)


@pytest.fixture
def etc(tmp_path):
    root = tmp_path / "etc"
    (root / "ssh" / "sshd_config.d").mkdir(parents=True)
    (root / "ssh" / "sshd_config").write_text("# main\nInclude /etc/ssh/sshd_config.d/*.conf\n")
    return root


@pytest.fixture(autouse=True)
def _linux(monkeypatch):
    monkeypatch.setattr(local.platform, "system", lambda: "Linux")


def _dropin(etc: Path) -> Path:
    return etc / "ssh" / "sshd_config.d" / local.SSH_DROPIN_NAME


def _call(etc, runner, enable=True, **kw):
    kw.setdefault("interactive", True)
    kw.setdefault("assume_yes", True)
    kw.setdefault("locate_sshd", lambda: local.SSHD_BINARY)
    local.configure_ssh_resume(enable, runner=runner, etc_root=etc, **kw)


def test_unsupported_config_prints_manual_text_and_runs_nothing(etc, capsys):
    (etc / "ssh" / "sshd_config").write_text("Port 22\n")
    runner = FakeRunner()
    _call(etc, runner)
    out = capsys.readouterr().out
    assert runner.calls == []
    assert "AcceptEnv TZ REMO_TAB_KEY" in out and "sshd_config.d" in out
    assert not _dropin(etc).exists()


def test_missing_sshd_config_is_unsupported(etc):
    (etc / "ssh" / "sshd_config").unlink()
    runner = FakeRunner()
    _call(etc, runner)
    assert runner.calls == []


def test_commented_include_is_not_support(etc):
    (etc / "ssh" / "sshd_config").write_text("#Include /etc/ssh/sshd_config.d/*.conf\n")
    runner = FakeRunner()
    _call(etc, runner)
    assert runner.calls == []


def test_sshd_at_a_non_default_path_is_used_for_validation(etc):
    brewed = "/opt/homebrew/sbin/sshd"
    runner = FakeRunner(sshd=brewed)
    _call(etc, runner, locate_sshd=lambda: brewed)
    assert runner.calls[1] == ["sudo", brewed, "-t"]
    assert _dropin(etc).read_text() == CONTENT


@pytest.mark.parametrize("enable", [True, False])
def test_no_sshd_changes_nothing_and_prints_manual_text(etc, capsys, enable):
    if not enable:
        _dropin(etc).write_text(CONTENT)
    runner = FakeRunner()
    _call(etc, runner, enable=enable, locate_sshd=lambda: None)
    assert runner.calls == []
    out = capsys.readouterr().out
    assert "no sshd found" in out
    expected = local._manual_ssh_resume_text(_dropin(etc), enable)
    assert all(line in out for line in expected.splitlines())
    assert (_dropin(etc).exists()) is (not enable)


def test_find_sshd_prefers_usr_sbin():
    assert local.find_sshd(which=lambda _: "/elsewhere/sshd", is_executable=lambda p: True) == (
        local.SSHD_BINARY
    )


def test_find_sshd_falls_back_to_path():
    found = local.find_sshd(which=lambda _: "/nix/bin/sshd", is_executable=lambda p: False)
    assert found == "/nix/bin/sshd"


@pytest.mark.parametrize("candidate", local.SSHD_FALLBACKS)
def test_find_sshd_checks_known_locations(candidate):
    found = local.find_sshd(which=lambda _: None, is_executable=lambda p: p == candidate)
    assert found == candidate


def test_find_sshd_returns_none_when_absent():
    assert local.find_sshd(which=lambda _: None, is_executable=lambda p: False) is None


def test_enable_fresh_installs_validates_and_reloads_on_linux(etc):
    runner = FakeRunner()
    _call(etc, runner)
    assert _dropin(etc).read_text() == CONTENT
    verbs = [c[1] for c in runner.calls]
    assert verbs == ["install", local.SSHD_BINARY, "systemctl"]
    assert runner.calls[1] == ["sudo", local.SSHD_BINARY, "-t"]
    assert runner.calls[2] == ["sudo", "systemctl", "reload", "ssh"]


def test_reload_falls_back_to_sshd_unit(etc):
    runner = FakeRunner(reload_rcs=(1, 0))
    _call(etc, runner)
    assert runner.calls[-1] == ["sudo", "systemctl", "reload", "sshd"]


def test_reload_failure_only_warns(etc, capsys):
    _call(etc, FakeRunner(reload_rcs=(1, 1)))
    assert "Could not reload" in capsys.readouterr().out
    assert _dropin(etc).read_text() == CONTENT


def test_enable_on_macos_does_not_reload(etc, monkeypatch):
    monkeypatch.setattr(local.platform, "system", lambda: "Darwin")
    runner = FakeRunner()
    _call(etc, runner)
    assert [c[1] for c in runner.calls] == ["install", local.SSHD_BINARY]


def test_enable_rerun_makes_no_sudo_calls(etc, capsys):
    _dropin(etc).write_text(CONTENT)
    runner = FakeRunner()
    _call(etc, runner)
    assert runner.calls == []
    assert "already enabled" in capsys.readouterr().out


def test_disable_present_removes_and_validates(etc):
    _dropin(etc).write_text(CONTENT)
    runner = FakeRunner()
    _call(etc, runner, enable=False)
    assert not _dropin(etc).exists()
    assert [c[1] for c in runner.calls] == ["rm", local.SSHD_BINARY, "systemctl"]


def test_disable_absent_makes_no_sudo_calls(etc, capsys):
    runner = FakeRunner()
    _call(etc, runner, enable=False)
    assert runner.calls == []
    assert "already disabled" in capsys.readouterr().out


def test_declined_prompt_changes_nothing(etc, mocker, capsys):
    mocker.patch.object(local, "confirm", return_value=False)
    runner = FakeRunner()
    _call(etc, runner, assume_yes=False)
    assert runner.calls == []
    assert not _dropin(etc).exists()
    assert "Left SSH configuration unchanged" in capsys.readouterr().out


def test_consent_prompt_names_file_line_and_sudo(etc, mocker, capsys):
    mocker.patch.object(local, "confirm", return_value=True)
    runner = FakeRunner()
    _call(etc, runner, assume_yes=False)
    out = capsys.readouterr().out
    assert str(_dropin(etc)) in out and "AcceptEnv TZ REMO_TAB_KEY" in out and "sudo" in out
    assert _dropin(etc).read_text() == CONTENT


def test_non_interactive_without_yes_prints_manual_steps(etc, capsys):
    runner = FakeRunner()
    _call(etc, runner, interactive=False, assume_yes=False)
    assert runner.calls == []
    out = capsys.readouterr().out
    assert "needs your consent" in out and "AcceptEnv TZ REMO_TAB_KEY" in out


def test_non_interactive_with_yes_proceeds(etc):
    runner = FakeRunner()
    _call(etc, runner, interactive=False, assume_yes=True)
    assert _dropin(etc).read_text() == CONTENT


def test_yes_skips_the_prompt(etc, mocker):
    prompt = mocker.patch.object(local, "confirm")
    _call(etc, FakeRunner(), assume_yes=True)
    prompt.assert_not_called()


def test_sshd_t_failure_on_enable_reverts_and_raises(etc):
    runner = FakeRunner(sshd_rc=1)
    with pytest.raises(OperationFailedError, match="bad directive"):
        _call(etc, runner)
    assert not _dropin(etc).exists()
    assert [c[1] for c in runner.calls] == ["install", local.SSHD_BINARY, "rm"]


def test_sshd_t_failure_on_disable_restores_previous_content(etc):
    previous = "# something else\nAcceptEnv TZ\n"
    _dropin(etc).write_text(previous)
    with pytest.raises(OperationFailedError):
        _call(etc, FakeRunner(sshd_rc=1), enable=False)
    assert _dropin(etc).read_text() == previous


def test_sshd_t_failure_over_a_different_dropin_restores_it(etc):
    previous = "AcceptEnv LANG\n"
    _dropin(etc).write_text(previous)
    with pytest.raises(OperationFailedError):
        _call(etc, FakeRunner(sshd_rc=1))
    assert _dropin(etc).read_text() == previous


def test_install_failure_raises_without_validating(etc):
    class Failing(FakeRunner):
        def __call__(self, argv):
            self.calls.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "sudo: a password is required")

    runner = Failing()
    with pytest.raises(OperationFailedError, match="password is required"):
        _call(etc, runner)
    assert len(runner.calls) == 1


def test_setup_without_the_flag_never_touches_ssh(mocker, tmp_path, monkeypatch, tmp_config_dir):
    monkeypatch.setenv("HOME", str(tmp_path))
    mocker.patch.object(local, "check_prerequisites", return_value=[])
    mocker.patch.object(local, "find_bash", return_value=("/b/bash", "5.2"))
    mocker.patch.object(local, "detect_devcontainer_runtime", return_value=None)
    mocker.patch.object(local, "run_playbook", return_value=0)
    spy = mocker.patch.object(local, "configure_ssh_resume")
    local.setup()
    spy.assert_not_called()
    local.setup(ssh_resume=True, assume_yes=True)
    spy.assert_called_once_with(True, assume_yes=True)
    local.setup(ssh_resume=False)
    assert spy.call_args.args == (False,)
