"""US1 T041: assert the bootstrap token is pushed via SSH stdin (never argv)."""

import json
import subprocess

import pytest

from remo_cli.providers import hetzner


def test_push_pipes_token_on_stdin(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch("subprocess.run", return_value=completed)

    hetzner._push_bootstrap_token("1.2.3.4", "SECRET_TOKEN_VALUE")  # noqa: SLF001

    run_mock.assert_called_once()
    call_args = run_mock.call_args
    ssh_argv = call_args[0][0]
    # Token must be on stdin, not in argv
    assert "SECRET_TOKEN_VALUE" not in " ".join(ssh_argv)
    assert call_args.kwargs.get("input") == "SECRET_TOKEN_VALUE"
    # The remote command must use stdin install
    remote_cmd = ssh_argv[-1]
    assert "install" in remote_cmd
    assert "-m 0400" in remote_cmd
    assert "/dev/stdin" in remote_cmd
    assert "/etc/remo-broker/bootstrap-token" in remote_cmd


def test_push_uses_root_user_by_default(mocker):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    run_mock = mocker.patch("subprocess.run", return_value=completed)

    hetzner._push_bootstrap_token("1.2.3.4", "x")  # noqa: SLF001

    ssh_argv = run_mock.call_args[0][0]
    # SSH target is root@1.2.3.4
    assert any("root@1.2.3.4" == a for a in ssh_argv)


def test_push_propagates_ssh_failure(mocker):
    completed = subprocess.CompletedProcess(
        args=[], returncode=255, stdout="", stderr="connection refused"
    )
    mocker.patch("subprocess.run", return_value=completed)

    with pytest.raises(RuntimeError, match="failed to push"):
        hetzner._push_bootstrap_token("1.2.3.4", "x")  # noqa: SLF001


def test_push_rejects_empty_token():
    with pytest.raises(ValueError, match="bootstrap token must be non-empty"):
        hetzner._push_bootstrap_token("1.2.3.4", "")  # noqa: SLF001


def test_push_rejects_empty_ip():
    with pytest.raises(ValueError, match="server_ip must be non-empty"):
        hetzner._push_bootstrap_token("", "token")  # noqa: SLF001


# ---------------------------------------------------------------------------
# Finding 15 — Hetzner host-key verification closes the MITM window.
# ---------------------------------------------------------------------------


def _ok_proc() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_push_with_server_id_verifies_and_uses_strict(mocker):
    """When Hetzner exposes host keys and they match, ssh runs with
    StrictHostKeyChecking=yes against a temp known_hosts file."""
    mocker.patch(
        "remo_cli.providers.hetzner._fetch_hetzner_host_keys",
        return_value=[("ssh-ed25519", "AAAAVERIFIED")],
    )
    mocker.patch(
        "remo_cli.providers.hetzner._verify_ssh_host_key",
        return_value=True,
    )
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    hetzner._push_bootstrap_token(  # noqa: SLF001
        "1.2.3.4", "SECRET", server_id=99
    )

    run_mock.assert_called_once()
    ssh_argv = run_mock.call_args[0][0]
    joined = " ".join(ssh_argv)
    assert "StrictHostKeyChecking=yes" in joined
    assert "UserKnownHostsFile=" in joined
    assert "accept-new" not in joined
    # Token still on stdin, not argv.
    assert "SECRET" not in joined
    assert run_mock.call_args.kwargs.get("input") == "SECRET"


def test_push_aborts_when_host_key_verification_fails(mocker):
    """When Hetzner-reported keys disagree with the live server, we raise
    BEFORE invoking ssh — no bootstrap token is shipped to a possible MITM."""
    mocker.patch(
        "remo_cli.providers.hetzner._fetch_hetzner_host_keys",
        return_value=[("ssh-ed25519", "AAAAEXPECTED")],
    )
    mocker.patch(
        "remo_cli.providers.hetzner._verify_ssh_host_key",
        return_value=False,
    )
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    with pytest.raises(RuntimeError, match="Possible MITM"):
        hetzner._push_bootstrap_token(  # noqa: SLF001
            "1.2.3.4", "SECRET", server_id=99
        )
    run_mock.assert_not_called()


def test_push_without_server_id_falls_back_to_accept_new(mocker):
    """No server_id == no API fingerprint lookup; fall back to accept-new
    with a warning surfaced to the user."""
    fetch = mocker.patch(
        "remo_cli.providers.hetzner._fetch_hetzner_host_keys",
        return_value=[],
    )
    warn = mocker.patch("remo_cli.providers.hetzner.print_warning")
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    hetzner._push_bootstrap_token("1.2.3.4", "SECRET")  # noqa: SLF001

    fetch.assert_not_called()
    warn.assert_called_once()
    ssh_argv = run_mock.call_args[0][0]
    joined = " ".join(ssh_argv)
    assert "StrictHostKeyChecking=accept-new" in joined
    assert "UserKnownHostsFile=" not in joined


def test_push_with_server_id_but_no_api_keys_falls_back(mocker):
    """server_id supplied but the API returns no host keys → warn + accept-new."""
    mocker.patch(
        "remo_cli.providers.hetzner._fetch_hetzner_host_keys",
        return_value=[],
    )
    warn = mocker.patch("remo_cli.providers.hetzner.print_warning")
    run_mock = mocker.patch("subprocess.run", return_value=_ok_proc())

    hetzner._push_bootstrap_token(  # noqa: SLF001
        "1.2.3.4", "SECRET", server_id=99
    )

    warn.assert_called_once()
    ssh_argv = run_mock.call_args[0][0]
    assert "StrictHostKeyChecking=accept-new" in " ".join(ssh_argv)


def test_fetch_host_keys_parses_top_level_field(mocker):
    """_fetch_hetzner_host_keys extracts (algo, key) from server.host_keys."""
    body = json.dumps(
        {
            "server": {
                "host_keys": [
                    {"type": "ssh-ed25519", "key": "AAAAED25"},
                    {"type": "ssh-rsa", "key": "AAAARSA"},
                ]
            }
        }
    ).encode()
    mocker.patch(
        "remo_cli.providers.hetzner._get_hetzner_api_token",
        return_value="tok",
    )

    class _Resp:
        def __init__(self, b: bytes) -> None:
            self._b = b

        def read(self) -> bytes:
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    mocker.patch(
        "urllib.request.urlopen", return_value=_Resp(body)
    )

    keys = hetzner._fetch_hetzner_host_keys(42)  # noqa: SLF001
    assert ("ssh-ed25519", "AAAAED25") in keys
    assert ("ssh-rsa", "AAAARSA") in keys


def test_verify_ssh_host_key_matches_keyscan(mocker):
    """_verify_ssh_host_key returns True when ssh-keyscan output contains
    an (algo, key) pair from the expected list."""
    scan_out = (
        "# 1.2.3.4:22 SSH-2.0-OpenSSH_9.6\n"
        "1.2.3.4 ssh-ed25519 AAAAED25\n"
        "1.2.3.4 ssh-rsa AAAARSA\n"
    )
    mocker.patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout=scan_out, stderr=""
        ),
    )
    assert hetzner._verify_ssh_host_key(  # noqa: SLF001
        "1.2.3.4", [("ssh-ed25519", "AAAAED25")]
    )
    assert not hetzner._verify_ssh_host_key(  # noqa: SLF001
        "1.2.3.4", [("ssh-ed25519", "AAAAOTHER")]
    )
