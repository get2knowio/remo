"""Unit tests for the host-key scan + trust decision table (T023, research R8).

Covers ``scan_and_verify_host_key`` in ``remo_cli.core.web_adopt``:

* trusted-record match / mismatch (FR-009 / FR-010),
* no-trusted-record interactive confirmation vs non-interactive skip
  (spec clarification Q2),
* hashed known_hosts handling via a REAL ``ssh-keygen -H`` round-trip,
* keyscan failure modes -> ``unreachable``,
* multiple key types with partial overlap against the trusted store,
* the ``ssh-keygen -lf`` fingerprint-rendering path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import ANY

import pytest

from remo_cli.core.web_adopt import scan_and_verify_host_key

# Captured before any test patches subprocess.run, so hashed-known_hosts tests
# can delegate ssh-keygen calls to the real binary.
_REAL_RUN = subprocess.run

HOST = "203.0.113.7"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _cp(cmd: list[str], rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr=stderr)


@pytest.fixture(scope="session")
def real_pubkeys(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Two REAL ed25519 public keys ('ssh-ed25519 AAAA...') via ssh-keygen."""
    keys_dir = tmp_path_factory.mktemp("keys")
    keys: list[str] = []
    for name in ("key_a", "key_b"):
        path = keys_dir / name
        _REAL_RUN(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(path)],
            check=True,
            capture_output=True,
        )
        key_type, material = path.with_suffix(".pub").read_text().split()[:2]
        keys.append(f"{key_type} {material}")
    return keys


# Fake-but-well-formed key material for the fully mocked decision-table tests.
ED25519_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMatchMatchMatchMatchMatchMatchMatchMatch01"
ED25519_OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEvilEvilEvilEvilEvilEvilEvilEvilEvil02"
RSA_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCrsaRsaRsaRsaRsaRsaRsaRsaRsaRsaRsa03"
RSA_OTHER = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCevilEvilEvilEvilEvilEvilEvilEvil04"

KEYSCAN_COMMENT = f"# {HOST}:22 SSH-2.0-OpenSSH_9.6"


def _keyscan_stdout(*keys: str) -> str:
    """Realistic ssh-keyscan stdout: comment header + one line per key."""
    lines = [KEYSCAN_COMMENT] + [f"{HOST} {key}" for key in keys]
    return "\n".join(lines) + "\n"


def _keygen_f_stdout(*keys: str, host: str = HOST) -> str:
    """Realistic `ssh-keygen -F` stdout for a found (plaintext) entry."""
    lines: list[str] = []
    for i, key in enumerate(keys, start=1):
        lines.append(f"# Host {HOST} found: line {i}")
        lines.append(f"{host} {key}")
    return "\n".join(lines) + "\n"


class RunDispatcher:
    """subprocess.run side_effect routing ssh-keyscan / ssh-keygen -F / -lf."""

    def __init__(
        self,
        keyscan: subprocess.CompletedProcess[str] | BaseException | None = None,
        keygen_f: subprocess.CompletedProcess[str] | None = None,
        keygen_lf: subprocess.CompletedProcess[str] | None = None,
    ) -> None:
        self.keyscan = keyscan
        self.keygen_f = keygen_f
        self.keygen_lf = keygen_lf
        self.calls: list[list[str]] = []
        self.lf_file_contents: list[str] = []

    def __call__(self, cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        prog = cmd[0]
        if prog == "ssh-keyscan":
            if isinstance(self.keyscan, BaseException):
                raise self.keyscan
            assert self.keyscan is not None, "unexpected ssh-keyscan call"
            return self.keyscan
        if prog == "ssh-keygen" and "-F" in cmd:
            assert self.keygen_f is not None, "unexpected ssh-keygen -F call"
            return self.keygen_f
        if prog == "ssh-keygen" and "-lf" in cmd:
            # Snapshot the temp pubkey file the implementation wrote for -lf.
            lf_path = cmd[cmd.index("-lf") + 1]
            self.lf_file_contents.append(Path(lf_path).read_text())
            if self.keygen_lf is not None:
                return self.keygen_lf
            return _cp(cmd, stdout="")
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    def commands(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def known_hosts(tmp_path: Path) -> Path:
    """An existing (plaintext, single-entry) known_hosts file."""
    path = tmp_path / "known_hosts"
    path.write_text(f"{HOST} {ED25519_KEY}\n")
    return path


def _patch_run(mocker, dispatcher: RunDispatcher) -> None:
    mocker.patch("remo_cli.core.web_adopt.subprocess.run", side_effect=dispatcher)


# ---------------------------------------------------------------------------
# Trusted-record match
# ---------------------------------------------------------------------------


class TestMatch:
    def test_match_returns_trusted_with_scanned_lines(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {ED25519_KEY}"]
        assert "matches trusted" in result.detail

    def test_match_invokes_keyscan_and_keygen_f_with_expected_args(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert dispatcher.calls[0] == [
            "ssh-keyscan", "-T", "5", "-t", "ed25519,ecdsa,rsa", HOST,
        ]
        assert dispatcher.calls[1] == ["ssh-keygen", "-F", HOST, "-f", str(known_hosts)]

    def test_match_never_prompts(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)
        confirm_calls: list[str] = []

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda prompt: confirm_calls.append(prompt) or True,
        )

        assert result.decision == "trusted"
        assert confirm_calls == []


# ---------------------------------------------------------------------------
# Trusted-record mismatch (FR-010)
# ---------------------------------------------------------------------------


class TestMismatch:
    def test_different_key_same_type_is_mismatch_with_no_lines(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_OTHER)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "mismatch"
        assert result.lines == []
        assert "does not match" in result.detail
        assert str(known_hosts) in result.detail

    def test_mismatch_wins_even_when_interactive(self, mocker, known_hosts):
        """A mismatch must never fall through to fingerprint confirmation."""
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_OTHER)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: pytest.fail("confirm_fn must not be called"),
        )

        assert result.decision == "mismatch"
        assert result.lines == []


# ---------------------------------------------------------------------------
# No trusted record (spec clarification Q2)
# ---------------------------------------------------------------------------


class TestNoTrustedRecord:
    def _dispatcher_not_found(self) -> RunDispatcher:
        return RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], rc=1),  # -F: not found
            keygen_lf=_cp(
                ["ssh-keygen"],
                stdout=f"256 SHA256:AbCdEffingerprint {HOST} (ED25519)\n",
            ),
        )

    def test_interactive_confirm_yes_is_trusted_with_lines(self, mocker, known_hosts):
        known_hosts.write_text("other.example.com ssh-ed25519 AAAAunrelated\n")
        dispatcher = self._dispatcher_not_found()
        _patch_run(mocker, dispatcher)
        prompts: list[str] = []

        def confirm_fn(prompt: str) -> bool:
            prompts.append(prompt)
            return True

        result = scan_and_verify_host_key(
            HOST, known_hosts_file=known_hosts, interactive=True, confirm_fn=confirm_fn
        )

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {ED25519_KEY}"]
        assert result.detail == "fingerprint confirmed interactively"
        assert len(prompts) == 1
        assert HOST in prompts[0]

    def test_interactive_decline_is_no_trust(self, mocker, known_hosts):
        dispatcher = self._dispatcher_not_found()
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: False,
        )

        assert result.decision == "no_trust"
        assert result.lines == []
        assert result.detail == "fingerprint confirmation declined"

    def test_non_interactive_is_no_trust_without_prompting(self, mocker, known_hosts):
        dispatcher = self._dispatcher_not_found()
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=False,
            confirm_fn=lambda _prompt: pytest.fail("confirm_fn must not be called"),
        )

        assert result.decision == "no_trust"
        assert result.lines == []
        assert HOST in result.detail
        assert "non-interactive" in result.detail
        # The fingerprint-rendering path must not run either.
        assert dispatcher.lf_file_contents == []

    def test_missing_known_hosts_file_skips_keygen_f(self, mocker, tmp_path):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST, known_hosts_file=tmp_path / "does-not-exist", interactive=False
        )

        assert result.decision == "no_trust"
        assert dispatcher.commands() == ["ssh-keyscan"]


# ---------------------------------------------------------------------------
# Hashed known_hosts (real `ssh-keygen -H` + real `ssh-keygen -F`) — pins the
# hashed-entry-safety claim from research R8.
# ---------------------------------------------------------------------------


class TestHashedKnownHosts:
    def _hashed_known_hosts(self, tmp_path: Path, pubkey: str) -> Path:
        path = tmp_path / "known_hosts"
        path.write_text(f"{HOST} {pubkey}\n")
        _REAL_RUN(["ssh-keygen", "-H", "-f", str(path)], check=True, capture_output=True)
        content = path.read_text()
        assert content.startswith("|1|"), "ssh-keygen -H did not hash the entry"
        assert HOST not in content, "hostname still visible after hashing"
        return path

    def _patch_keyscan_only(self, mocker, stdout: str) -> None:
        """Intercept ssh-keyscan; delegate ssh-keygen to the REAL binary."""

        def dispatch(cmd: list[str], **kwargs: object):
            if cmd[0] == "ssh-keyscan":
                return _cp(cmd, stdout=stdout)
            return _REAL_RUN(cmd, **kwargs)  # type: ignore[arg-type]

        mocker.patch("remo_cli.core.web_adopt.subprocess.run", side_effect=dispatch)

    def test_hashed_entry_match_is_trusted(self, mocker, tmp_path, real_pubkeys):
        key_a, _key_b = real_pubkeys
        known_hosts = self._hashed_known_hosts(tmp_path, key_a)
        self._patch_keyscan_only(mocker, _keyscan_stdout(key_a))

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {key_a}"]

    def test_hashed_entry_mismatch_is_flagged(self, mocker, tmp_path, real_pubkeys):
        key_a, key_b = real_pubkeys
        known_hosts = self._hashed_known_hosts(tmp_path, key_a)
        self._patch_keyscan_only(mocker, _keyscan_stdout(key_b))

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "mismatch"
        assert result.lines == []


# ---------------------------------------------------------------------------
# Keyscan failure modes -> unreachable
# ---------------------------------------------------------------------------


class TestUnreachable:
    def test_keyscan_timeout(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=subprocess.TimeoutExpired(cmd=["ssh-keyscan"], timeout=20)
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST, known_hosts_file=known_hosts, scan_timeout=20.0
        )

        assert result.decision == "unreachable"
        assert result.lines == []
        assert "timed out after 20s" in result.detail
        # Nothing beyond the scan should have run.
        assert dispatcher.commands() == ["ssh-keyscan"]

    def test_keyscan_binary_missing(self, mocker, known_hosts):
        dispatcher = RunDispatcher(keyscan=FileNotFoundError("ssh-keyscan"))
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "unreachable"
        assert "ssh-keyscan not found" in result.detail

    def test_keyscan_os_error(self, mocker, known_hosts):
        dispatcher = RunDispatcher(keyscan=OSError("fork failed"))
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "unreachable"
        assert "fork failed" in result.detail

    def test_keyscan_empty_output_reports_last_stderr_line(self, mocker, known_hosts):
        stderr = "getaddrinfo 203.0.113.7: Name or service not known\n"
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], rc=1, stdout="", stderr=stderr)
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "unreachable"
        assert result.detail == "getaddrinfo 203.0.113.7: Name or service not known"

    def test_keyscan_empty_output_no_stderr(self, mocker, known_hosts):
        dispatcher = RunDispatcher(keyscan=_cp(["ssh-keyscan"], stdout="", stderr=""))
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "unreachable"
        assert result.detail == "no host keys returned by ssh-keyscan"

    def test_keyscan_comment_only_output_is_unreachable(self, mocker, known_hosts):
        """Comment lines (# banner) without key lines yield no scannable keys."""
        dispatcher = RunDispatcher(
            keyscan=_cp(
                ["ssh-keyscan"],
                stdout=f"{KEYSCAN_COMMENT}\n",
                stderr="connection reset\n",
            )
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "unreachable"
        assert result.detail == "connection reset"


# ---------------------------------------------------------------------------
# Multiple key types with partial overlap against the trusted store
# ---------------------------------------------------------------------------


class TestMultipleKeyTypes:
    def test_partial_overlap_match_pushes_all_scanned_lines(self, mocker, known_hosts):
        """Documented behavior: one matching trusted type vouches for the whole
        scan — ALL scanned lines are returned, including key types (rsa here)
        the trusted store has never seen."""
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY, RSA_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {ED25519_KEY}", f"{HOST} {RSA_KEY}"]

    def test_any_overlapping_type_mismatch_flags_whole_instance(self, mocker, known_hosts):
        """ed25519 matches but the rsa key differs from the trusted rsa record
        -> mismatch for the instance; nothing is pushed."""
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY, RSA_OTHER)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY, RSA_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts)

        assert result.decision == "mismatch"
        assert result.lines == []
        assert "ssh-rsa" in result.detail

    def test_record_for_other_types_only_falls_through_to_no_trust(
        self, mocker, known_hosts
    ):
        """Trusted store knows only an rsa key; the scan returns only ed25519.
        Nothing is comparable, so this is the no-trusted-record path (documented
        fall-through), not a mismatch."""
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(RSA_KEY)),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, known_hosts_file=known_hosts, interactive=False)

        assert result.decision == "no_trust"
        assert result.lines == []

    def test_record_for_other_types_only_interactive_can_confirm(self, mocker, known_hosts):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(RSA_KEY)),
            keygen_lf=_cp(["ssh-keygen"], stdout="256 SHA256:zZz fingerprint\n"),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {ED25519_KEY}"]


# ---------------------------------------------------------------------------
# Fingerprint rendering (ssh-keygen -lf) on the interactive-confirm path
# ---------------------------------------------------------------------------


class TestFingerprintRendering:
    def test_lf_invoked_with_scanned_lines_and_output_shown(
        self, mocker, known_hosts, capsys
    ):
        fingerprint = f"256 SHA256:AbCdEf0123456789 {HOST} (ED25519)"
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], rc=1),
            keygen_lf=_cp(["ssh-keygen"], stdout=fingerprint + "\n"),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.decision == "trusted"
        # ssh-keygen -lf ran exactly once, against a temp file holding the
        # scanned key lines.
        lf_calls = [c for c in dispatcher.calls if c[:2] == ["ssh-keygen", "-lf"]]
        assert lf_calls == [["ssh-keygen", "-lf", ANY]]
        assert dispatcher.lf_file_contents == [f"{HOST} {ED25519_KEY}\n"]
        # The rendered fingerprint was printed for the user to verify.
        assert fingerprint in capsys.readouterr().out

    def test_lf_failure_falls_back_to_raw_lines(self, mocker, known_hosts, capsys):
        """If fingerprint rendering fails, the raw scanned lines are shown so
        the user can still make a decision."""
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], rc=1),
            keygen_lf=_cp(["ssh-keygen"], rc=255, stdout=""),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: False,
        )

        assert result.decision == "no_trust"
        assert f"{HOST} {ED25519_KEY}" in capsys.readouterr().out
