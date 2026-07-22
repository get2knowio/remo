"""Tests for providers/added.py --verify reachability (feature 014, US3, FR-014).

Fail-closed: on a failed probe nothing is registered and a non-zero code is
returned; without --verify no network round-trip occurs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from remo_cli.models.host import KnownHost
from remo_cli.providers import added


def _completed(rc: int, stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stderr = stderr
    return cp


# ---------------------------------------------------------------------------
# add(verify=...) wiring
# ---------------------------------------------------------------------------


class TestAddVerify:
    def test_reachable_registers(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        vr = mocker.patch(
            "remo_cli.providers.added.verify_reachable", return_value=(True, None)
        )
        mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@1.2.3.4", verify=True)

        assert rc == 0
        vr.assert_called_once()
        save.assert_called_once()

    def test_unreachable_fails_closed_no_write(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        save = mocker.patch("remo_cli.providers.added.save_known_host")
        mocker.patch(
            "remo_cli.providers.added.verify_reachable",
            return_value=(False, "Connection refused"),
        )
        err = mocker.patch("remo_cli.providers.added.print_error")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@1.2.3.4", verify=True)

        assert rc == 1
        save.assert_not_called()  # fail-closed: nothing registered
        assert "Connection refused" in " ".join(
            str(c.args[0]) for c in err.call_args_list
        )

    def test_no_verify_makes_no_network_call(self, mocker) -> None:
        mocker.patch("remo_cli.providers.added.get_known_hosts", return_value=[])
        mocker.patch("remo_cli.providers.added.save_known_host")
        vr = mocker.patch("remo_cli.providers.added.verify_reachable")
        mocker.patch("remo_cli.providers.added.print_success")
        mocker.patch("remo_cli.providers.added.print_info")

        rc = added.add(name="box", target="dev@1.2.3.4", verify=False)

        assert rc == 0
        vr.assert_not_called()  # FR-014: no round-trip when not requested


# ---------------------------------------------------------------------------
# verify_reachable() probe
# ---------------------------------------------------------------------------


class TestVerifyReachable:
    def _host(self) -> KnownHost:
        return KnownHost(
            type="ssh",
            name="box",
            host="1.2.3.4",
            user="remo",
            instance_id="2222",
            access_mode="direct",
        )

    def test_success(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.subprocess.run", return_value=_completed(0)
        )
        ok, err = added.verify_reachable(self._host())
        assert ok is True and err is None

    def test_ssh_failure_returns_stderr(self, mocker) -> None:
        mocker.patch(
            "remo_cli.providers.added.subprocess.run",
            return_value=_completed(255, stderr="Permission denied (publickey)"),
        )
        ok, err = added.verify_reachable(self._host())
        assert ok is False
        assert "Permission denied" in (err or "")

    def test_probe_uses_port_from_host(self, mocker) -> None:
        run = mocker.patch(
            "remo_cli.providers.added.subprocess.run", return_value=_completed(0)
        )
        added.verify_reachable(self._host())
        argv = run.call_args.args[0]
        # Port 2222 flows through build_ssh_opts into the probe argv.
        assert "Port=2222" in argv

    def test_probe_relaxes_host_key_check(self, mocker) -> None:
        # A reachable but never-before-seen host must pass the probe rather than
        # fail "Host key verification failed" under BatchMode.
        run = mocker.patch(
            "remo_cli.providers.added.subprocess.run", return_value=_completed(0)
        )
        added.verify_reachable(self._host())
        argv = run.call_args.args[0]
        assert "StrictHostKeyChecking=no" in argv
        assert "UserKnownHostsFile=/dev/null" in argv
