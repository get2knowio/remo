"""Unit + CLI tests for `remo web check` (T050, FR-046).

Covers US4 scenario 2 from `specs/010-web-session-interface/tasks.md`:
registry readability, SSH identity availability, runtime-dir writability,
required executables, and per-instance reachability/protocol compatibility
-- both the all-PASS path and at least one FAIL path (no SSH identity
present).

`subprocess.run` is mocked at the module level (never a real `ssh`
invocation), which also lets these tests assert the check command NEVER
invokes an interactive-session verb: every captured argv must carry the
`capabilities` verb and never `attach` / `project-launch` (FR-046: "without
opening an interactive session").
"""

from __future__ import annotations

import json
import subprocess

import pytest
from click.testing import CliRunner

from remo_cli.cli.main import cli
from remo_cli.web import check as check_module
from remo_cli.web.check import all_passed, format_results, run_checks
from remo_cli.web.config import WebSettings

pytestmark = pytest.mark.usefixtures("tmp_config_dir")


def _capabilities_payload() -> bytes:
    return json.dumps(
        {
            "protocol_version": 1,
            "host_tools_version": "2.1.0",
            "projects_root": "/home/remo/projects",
            "operations": ["capabilities", "sessions list", "sessions attach"],
            "zellij": True,
            "docker": False,
        }
    ).encode()


def _write_registry(tmp_config_dir, lines: list[str]) -> None:
    (tmp_config_dir / "known_hosts").write_text("\n".join(lines) + "\n")


def _fake_key_file(tmp_path):
    key = tmp_path / "id_ed25519"
    key.write_text("not a real key, just needs to exist and be readable\n")
    return key


def _stub_subprocess_run(ssh_handler):
    """Build a `subprocess.run` stand-in that only fakes `ssh ...` invocations.

    `core.ssh.build_ssh_opts` also shells out to `timedatectl`/`systemsetup`
    for timezone detection (unrelated to the check logic under test); those
    must raise `FileNotFoundError` (exactly like a real sandbox missing
    those binaries) rather than being silently mishandled by the capabilities
    stub, so `detect_timezone()` falls through to its file-based checks.
    """

    def _run(argv, **kwargs):
        if argv and argv[0] in ("timedatectl", "systemsetup"):
            raise FileNotFoundError(f"{argv[0]} not found (test stub)")
        if argv and argv[0] == "ssh":
            return ssh_handler(argv, kwargs)
        raise AssertionError(f"unexpected subprocess.run invocation in test: {argv!r}")

    return _run


def _assert_no_interactive_argv(mock_run) -> None:
    """No captured subprocess call may carry an interactive-session verb."""
    for call in mock_run.call_args_list:
        argv = call.args[0]
        joined = " ".join(argv)
        assert "attach" not in joined, f"interactive verb leaked into argv: {argv!r}"
        assert "project-launch" not in joined, f"interactive verb leaked into argv: {argv!r}"


# ---------------------------------------------------------------------------
# All-PASS path
# ---------------------------------------------------------------------------


class TestAllPass:
    def test_run_checks_all_pass(self, tmp_config_dir, tmp_path, monkeypatch, mocker):
        _write_registry(tmp_config_dir, ["incus:dev:127.0.0.1:remo"])
        monkeypatch.setenv("REMO_WEB_SSH_IDENTITY_FILE", str(_fake_key_file(tmp_path)))
        monkeypatch.setattr(
            check_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ssh" else None
        )

        mock_run = mocker.patch(
            "subprocess.run",
            side_effect=_stub_subprocess_run(
                lambda argv, kwargs: subprocess.CompletedProcess(
                    args=argv, returncode=0, stdout=_capabilities_payload(), stderr=b""
                )
            ),
        )

        settings = WebSettings(ssh_control_dir=str(tmp_path / "ssh-ctrl"))
        results = run_checks(settings)

        assert all_passed(results) is True
        names = {r.name for r in results}
        assert {"registry", "ssh_identity", "runtime_dir", "ssh", "instance incus/dev"} <= names
        assert all(r.passed for r in results)

        report = format_results(results)
        assert "remo web check" in report
        assert "[PASS] registry:" in report
        assert "[PASS] ssh_identity:" in report
        assert "[PASS] runtime_dir:" in report
        assert "[PASS] ssh:" in report
        assert "[PASS] instance incus/dev: capabilities OK (protocol_version=1)" in report

        _assert_no_interactive_argv(mock_run)
        ssh_calls = [call.args[0] for call in mock_run.call_args_list if call.args[0][0] == "ssh"]
        assert ssh_calls, "expected at least one ssh invocation"
        for argv in ssh_calls:
            assert "capabilities" in argv

    def test_cli_check_all_pass_exits_zero(self, tmp_config_dir, tmp_path, monkeypatch, mocker):
        _write_registry(tmp_config_dir, ["incus:dev:127.0.0.1:remo"])
        monkeypatch.setenv("REMO_WEB_SSH_IDENTITY_FILE", str(_fake_key_file(tmp_path)))
        monkeypatch.setenv("REMO_WEB_SSH_CONTROL_DIR", str(tmp_path / "ssh-ctrl"))
        monkeypatch.setattr(
            check_module.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ssh" else None
        )
        mock_run = mocker.patch(
            "subprocess.run",
            side_effect=_stub_subprocess_run(
                lambda argv, kwargs: subprocess.CompletedProcess(
                    args=argv, returncode=0, stdout=_capabilities_payload(), stderr=b""
                )
            ),
        )

        result = CliRunner().invoke(cli, ["web", "check"])

        assert result.exit_code == 0, result.output
        assert "[PASS] instance incus/dev" in result.output
        _assert_no_interactive_argv(mock_run)


# ---------------------------------------------------------------------------
# FAIL path: no SSH identity present (US4 scenario 2)
# ---------------------------------------------------------------------------


class TestNoSshIdentity:
    def test_run_checks_flags_missing_identity_with_registry_not_auth_message(
        self, tmp_config_dir, tmp_path, monkeypatch
    ):
        # Registry present and readable; no hosts registered (no per-instance
        # checks to worry about), no SSH identity anywhere findable.
        _write_registry(tmp_config_dir, [])
        monkeypatch.delenv("REMO_WEB_SSH_IDENTITY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "fake_home_no_ssh_dir"))

        settings = WebSettings(ssh_control_dir=str(tmp_path / "ssh-ctrl"))
        results = run_checks(settings)

        assert all_passed(results) is False
        registry_result = next(r for r in results if r.name == "registry")
        assert registry_result.passed is True

        identity_result = next(r for r in results if r.name == "ssh_identity")
        assert identity_result.passed is False
        assert identity_result.remediation is not None
        assert "not authentication material" in identity_result.remediation

    def test_cli_check_no_identity_exits_nonzero_with_actionable_message(
        self, tmp_config_dir, tmp_path, monkeypatch
    ):
        _write_registry(tmp_config_dir, [])
        monkeypatch.delenv("REMO_WEB_SSH_IDENTITY_FILE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path / "fake_home_no_ssh_dir"))
        monkeypatch.setenv("REMO_WEB_SSH_CONTROL_DIR", str(tmp_path / "ssh-ctrl"))

        result = CliRunner().invoke(cli, ["web", "check"])

        assert result.exit_code != 0
        assert "[FAIL] ssh_identity:" in result.output
        assert "not authentication material" in result.output


# ---------------------------------------------------------------------------
# Per-instance reachability FAIL path
# ---------------------------------------------------------------------------


class TestInstanceUnreachable:
    def test_unreachable_instance_reported_as_fail_others_still_run(
        self, tmp_config_dir, tmp_path, monkeypatch, mocker
    ):
        _write_registry(
            tmp_config_dir,
            ["incus:good:127.0.0.1:remo", "aws:devbox:127.0.0.2:remo:i-0abc:ssm:us-west-2"],
        )
        monkeypatch.setenv("REMO_WEB_SSH_IDENTITY_FILE", str(_fake_key_file(tmp_path)))
        monkeypatch.setattr(check_module.shutil, "which", lambda name: f"/usr/bin/{name}")

        def _ssh_handler(argv, kwargs):
            if "127.0.0.2" in " ".join(argv) or "i-0abc" in " ".join(argv):
                raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 5.0))
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout=_capabilities_payload(), stderr=b""
            )

        mock_run = mocker.patch("subprocess.run", side_effect=_stub_subprocess_run(_ssh_handler))

        settings = WebSettings(ssh_control_dir=str(tmp_path / "ssh-ctrl"))
        results = run_checks(settings)

        assert all_passed(results) is False
        good = next(r for r in results if r.name == "instance incus/good")
        assert good.passed is True

        bad = next(r for r in results if r.name == "instance aws/devbox")
        assert bad.passed is False
        assert bad.remediation is not None

        # aws_cli/ssm_plugin checks are gated on an SSM host being registered.
        names = {r.name for r in results}
        assert "aws_cli" in names
        assert "ssm_plugin" in names

        _assert_no_interactive_argv(mock_run)
