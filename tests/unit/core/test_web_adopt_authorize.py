"""Unit tests for web_adopt authorized_keys management (T024, research R7).

Covers:

* ``build_authorize_command`` — POSIX-sh command construction: marker
  filtering, temp-file + ``mv`` write, permission handling, missing-file
  tolerance, and safe quoting of the public key.
* Real semantics — the generated command is executed locally (``sh -c``)
  against a temp ``$HOME/.ssh/authorized_keys`` to prove install,
  idempotence, rotation, and fresh-file behavior byte-for-byte.
* ``authorize_service_key`` — SSH execution is mocked; asserts ambient SSH
  access (no IdentityFile override), BatchMode, bounded timeouts, and that
  the remote command is exactly the one ``build_authorize_command`` built.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from remo_cli.core.web_adopt import (
    AUTHORIZED_KEYS_MARKER,
    authorize_service_key,
    build_authorize_command,
)
from remo_cli.models.host import KnownHost

# A realistic single-line OpenSSH ed25519 public key with the remo-web
# comment shape from data-model.md (InstanceAuthorizationEntry).
SERVICE_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKk4mCBB2AVDBWvIRtRZlc2VydmljZWtleQ "
    "remo-web@dep-1234abcd"
)
ROTATED_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ9r0tYXRlZGtleW1hdGVyaWFsZm9ydGVz "
    "remo-web@dep-5678efgh"
)


@pytest.fixture
def direct_host() -> KnownHost:
    return KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="remo")


@pytest.fixture
def _suppress_tz(monkeypatch):
    """Keep build_ssh_base_cmd from appending SendEnv=TZ based on the env."""
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: "")


# ---------------------------------------------------------------------------
# build_authorize_command — construction
# ---------------------------------------------------------------------------


class TestBuildAuthorizeCommand:
    def test_filters_on_marker_not_full_key(self):
        cmd = build_authorize_command(SERVICE_KEY)
        # Rotation only works if the filter is the marker, not the key line.
        assert f"grep -vF {shlex.quote(AUTHORIZED_KEYS_MARKER)}" in cmd
        assert AUTHORIZED_KEYS_MARKER == " remo-web@"

    def test_writes_via_temp_file_and_mv(self):
        cmd = build_authorize_command(SERVICE_KEY)
        assert "mktemp ~/.ssh/.authorized_keys.remo.XXXXXX" in cmd
        assert 'mv "$tmp" ~/.ssh/authorized_keys' in cmd

    def test_permission_handling(self):
        cmd = build_authorize_command(SERVICE_KEY)
        assert "umask 077" in cmd
        assert "chmod 700 ~/.ssh" in cmd
        assert 'chmod 600 "$tmp"' in cmd

    def test_tolerates_missing_authorized_keys(self):
        cmd = build_authorize_command(SERVICE_KEY)
        assert "mkdir -p ~/.ssh" in cmd
        assert "touch ~/.ssh/authorized_keys" in cmd
        # grep on an empty file exits 1; the pipeline must not abort under set -e.
        assert "set -e" in cmd
        assert '> "$tmp" || true' in cmd

    def test_public_key_is_shell_quoted(self):
        cmd = build_authorize_command(SERVICE_KEY)
        assert shlex.quote(SERVICE_KEY) in cmd
        # The raw (unquoted) key line must not appear outside the quoting.
        assert f"printf '%s\\n' {shlex.quote(SERVICE_KEY)}" in cmd

    def test_strips_surrounding_whitespace(self):
        assert build_authorize_command(f"  {SERVICE_KEY}\n") == build_authorize_command(
            SERVICE_KEY
        )

    @pytest.mark.parametrize(
        "bad_key",
        [
            "",
            "   \n",
            f"{SERVICE_KEY}\nssh-ed25519 AAAA second-line",
            "ssh-ed25519\rAAAA remo-web@dep",
        ],
    )
    def test_rejects_empty_or_multiline_keys(self, bad_key):
        with pytest.raises(ValueError):
            build_authorize_command(bad_key)

    @pytest.mark.parametrize(
        "bad_key",
        [
            "ssh-ed25519",  # single field
            "not-a-key AAAA remo-web@dep",  # unknown prefix
            "garbage",
        ],
    )
    def test_rejects_non_openssh_shapes(self, bad_key):
        with pytest.raises(ValueError):
            build_authorize_command(bad_key)


# ---------------------------------------------------------------------------
# build_authorize_command — real semantics (run locally under a temp HOME)
# ---------------------------------------------------------------------------

USER_KEY_1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIUserKeyOne user@laptop"
USER_KEY_2 = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABUserKeyTwo user@desktop"


def _run_authorize(cmd: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    return subprocess.run(
        ["sh", "-c", cmd], env=env, capture_output=True, text=True, timeout=30
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestAuthorizeCommandSemantics:
    def test_fresh_install_appends_one_line_preserving_user_keys(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        auth = ssh_dir / "authorized_keys"
        auth.write_text(f"{USER_KEY_1}\n{USER_KEY_2}\n")
        auth.chmod(0o600)

        result = _run_authorize(build_authorize_command(SERVICE_KEY), tmp_path)
        assert result.returncode == 0, result.stderr

        lines = auth.read_text().splitlines()
        assert lines == [USER_KEY_1, USER_KEY_2, SERVICE_KEY]
        assert sum(1 for line in lines if AUTHORIZED_KEYS_MARKER in line) == 1
        assert _mode(auth) == 0o600
        assert _mode(ssh_dir) == 0o700

    def test_rerun_with_same_key_is_byte_identical(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        auth = ssh_dir / "authorized_keys"
        auth.write_text(f"{USER_KEY_1}\n")
        auth.chmod(0o600)

        cmd = build_authorize_command(SERVICE_KEY)
        assert _run_authorize(cmd, tmp_path).returncode == 0
        first = auth.read_bytes()
        assert _run_authorize(cmd, tmp_path).returncode == 0
        assert auth.read_bytes() == first
        assert _mode(auth) == 0o600

    def test_rotation_replaces_stale_deployment_line(self, tmp_path):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir(mode=0o700)
        auth = ssh_dir / "authorized_keys"
        # SERVICE_KEY is the "old deployment" entry already installed.
        auth.write_text(f"{USER_KEY_1}\n{SERVICE_KEY}\n")
        auth.chmod(0o600)

        result = _run_authorize(build_authorize_command(ROTATED_KEY), tmp_path)
        assert result.returncode == 0, result.stderr

        lines = auth.read_text().splitlines()
        assert lines == [USER_KEY_1, ROTATED_KEY]
        # Never two remo-web@ lines.
        assert sum(1 for line in lines if AUTHORIZED_KEYS_MARKER in line) == 1
        assert SERVICE_KEY not in lines

    def test_missing_authorized_keys_and_ssh_dir_created(self, tmp_path):
        assert not (tmp_path / ".ssh").exists()

        result = _run_authorize(build_authorize_command(SERVICE_KEY), tmp_path)
        assert result.returncode == 0, result.stderr

        ssh_dir = tmp_path / ".ssh"
        auth = ssh_dir / "authorized_keys"
        assert auth.read_text() == f"{SERVICE_KEY}\n"
        assert _mode(auth) == 0o600
        assert _mode(ssh_dir) == 0o700
        # No leftover temp files from the mktemp+mv dance.
        assert [p.name for p in ssh_dir.iterdir()] == ["authorized_keys"]

    def test_hostile_comment_is_not_shell_interpreted(self, tmp_path):
        hostile = (
            "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHostile "
            'remo-web@$(touch ~/pwned);`touch ~/pwned2`;"x";rm -rf ~'
        )
        result = _run_authorize(build_authorize_command(hostile), tmp_path)
        assert result.returncode == 0, result.stderr

        auth = tmp_path / ".ssh" / "authorized_keys"
        assert auth.read_text() == f"{hostile}\n"
        assert not (tmp_path / "pwned").exists()
        assert not (tmp_path / "pwned2").exists()


# ---------------------------------------------------------------------------
# authorize_service_key — mocked SSH execution layer
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_suppress_tz")
class TestAuthorizeServiceKey:
    def _completed(self, returncode: int = 0, stderr: str = "") -> MagicMock:
        return MagicMock(returncode=returncode, stderr=stderr, stdout="")

    def test_argv_uses_ambient_ssh_batchmode_and_remote_command(
        self, direct_host, monkeypatch
    ):
        runs: list[tuple[list[str], dict]] = []

        def fake_run(cmd, **kwargs):
            runs.append((cmd, kwargs))
            return self._completed(0)

        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", fake_run)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is True
        assert detail == ""

        assert len(runs) == 1
        argv, kwargs = runs[0]
        assert argv[0] == "ssh"
        # Ambient SSH access: NO IdentityFile/IdentitiesOnly override.
        assert not any("IdentityFile" in arg for arg in argv)
        assert "IdentitiesOnly=yes" not in argv
        # BatchMode + bounded connect timeout.
        assert "BatchMode=yes" in argv
        assert "ConnectTimeout=10" in argv
        # Target and remote command (exactly what build_authorize_command built).
        assert "remo@5.6.7.8" in argv
        assert argv[-1] == build_authorize_command(SERVICE_KEY)
        # Bounded overall subprocess timeout (default 30s).
        assert kwargs["timeout"] == 30.0

    def test_delegates_to_build_ssh_base_cmd_without_identity_override(
        self, direct_host, monkeypatch
    ):
        base_cmd_calls: list[tuple[tuple, dict]] = []

        def fake_base_cmd(host, *args, **kwargs):
            base_cmd_calls.append(((host, *args), kwargs))
            return ["ssh", "stub-opt", "remo@5.6.7.8"]

        monkeypatch.setattr(
            "remo_cli.core.web_adopt.build_ssh_base_cmd", fake_base_cmd
        )
        run_mock = MagicMock(return_value=self._completed(0))
        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", run_mock)

        ok, _ = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is True

        assert len(base_cmd_calls) == 1
        (positional, kwargs) = base_cmd_calls[0]
        assert positional == (direct_host,)
        assert kwargs == {
            "extra_opts": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        }
        assert "identity_file" not in kwargs

        argv = run_mock.call_args.args[0]
        assert argv == [
            "ssh",
            "stub-opt",
            "remo@5.6.7.8",
            build_authorize_command(SERVICE_KEY),
        ]

    def test_custom_timeout_is_forwarded(self, direct_host, monkeypatch):
        run_mock = MagicMock(return_value=self._completed(0))
        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", run_mock)

        authorize_service_key(direct_host, SERVICE_KEY, timeout=7.0)
        assert run_mock.call_args.kwargs["timeout"] == 7.0

    def test_timeout_expired_returns_false_never_raises(self, direct_host, monkeypatch):
        def raise_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", raise_timeout)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is False
        assert detail == "SSH timed out after 30s"

    def test_oserror_returns_false_never_raises(self, direct_host, monkeypatch):
        def raise_oserror(cmd, **kwargs):
            raise OSError("ssh binary not found")

        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", raise_oserror)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is False
        assert detail == "SSH failed: ssh binary not found"

    def test_exit_255_reports_connection_failure(self, direct_host, monkeypatch):
        run_mock = MagicMock(
            return_value=self._completed(255, stderr="Permission denied (publickey).")
        )
        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", run_mock)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is False
        assert detail == "Permission denied (publickey)."

    def test_exit_255_without_stderr_uses_default_detail(self, direct_host, monkeypatch):
        run_mock = MagicMock(return_value=self._completed(255, stderr=""))
        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", run_mock)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is False
        assert detail == "SSH connection failed (exit code 255)"

    def test_nonzero_remote_exit_reports_remote_failure(self, direct_host, monkeypatch):
        run_mock = MagicMock(return_value=self._completed(1, stderr="mktemp: failed"))
        monkeypatch.setattr("remo_cli.core.web_adopt.subprocess.run", run_mock)

        ok, detail = authorize_service_key(direct_host, SERVICE_KEY)
        assert ok is False
        assert detail == "remote command failed (exit 1): mktemp: failed"
