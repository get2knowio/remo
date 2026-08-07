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

from remo_cli.core.web_adopt import (
    HostKeyScan,
    _process_instance,
    known_hosts_lookup_key,
    scan_and_verify_host_key,
)
from remo_cli.models.host import KnownHost

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
        # #157: the confirmation is recorded locally too, without disturbing
        # the unrelated entry that was already there.
        assert known_hosts.read_text() == (
            "other.example.com ssh-ed25519 AAAAunrelated\n" f"{HOST} {ED25519_KEY}\n"
        )

    def test_interactive_decline_is_no_trust(self, mocker, known_hosts, tmp_path):
        dispatcher = self._dispatcher_not_found()
        _patch_run(mocker, dispatcher)
        before = known_hosts.read_text()

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: False,
        )

        assert result.decision == "no_trust"
        assert result.lines == []
        assert result.detail == "fingerprint confirmation declined"
        # A declined fingerprint must never be trusted locally either (#157).
        assert known_hosts.read_text() == before

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
        # Nothing was confirmed, so nothing may be trusted locally (#157).
        assert known_hosts.read_text() == f"{HOST} {ED25519_KEY}\n"

    def test_missing_known_hosts_file_skips_keygen_f(self, mocker, tmp_path):
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)
        missing = tmp_path / "does-not-exist"

        result = scan_and_verify_host_key(
            HOST, known_hosts_file=missing, interactive=False
        )

        assert result.decision == "no_trust"
        assert dispatcher.commands() == ["ssh-keyscan"]
        # A non-interactive run must not create a trust store either (#157).
        assert not missing.exists()


# ---------------------------------------------------------------------------
# Confirmed fingerprints are persisted to the workstation's known_hosts (#157)
# ---------------------------------------------------------------------------


class TestConfirmPersistsTrust:
    """Issue #157: confirming a fingerprint used to update only the push
    payload, so the authorize step that immediately follows (ssh with
    BatchMode=yes) died with "Host key verification failed"."""

    def _dispatcher(self, *keys: str, found: str | None = None) -> RunDispatcher:
        return RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(*keys)),
            keygen_f=(
                _cp(["ssh-keygen"], stdout=_keygen_f_stdout(found))
                if found
                else _cp(["ssh-keygen"], rc=1)
            ),
            keygen_lf=_cp(["ssh-keygen"], stdout="256 SHA256:zZz fingerprint\n"),
        )

    def test_creates_store_with_tight_permissions(self, mocker, tmp_path):
        _patch_run(mocker, self._dispatcher(ED25519_KEY))
        store = tmp_path / "ssh" / "known_hosts"

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.decision == "trusted"
        assert store.read_text() == f"{HOST} {ED25519_KEY}\n"
        assert store.stat().st_mode & 0o777 == 0o600
        assert store.parent.stat().st_mode & 0o777 == 0o700

    def test_appends_all_scanned_key_types(self, mocker, tmp_path):
        _patch_run(mocker, self._dispatcher(ED25519_KEY, RSA_KEY))
        store = tmp_path / "known_hosts"

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.lines == [f"{HOST} {ED25519_KEY}", f"{HOST} {RSA_KEY}"]
        assert store.read_text() == f"{HOST} {ED25519_KEY}\n{HOST} {RSA_KEY}\n"

    def test_appends_only_the_missing_line(self, mocker, tmp_path):
        """Never duplicate a line the store already holds verbatim — the
        lookup can come back empty for a line that is physically present (an
        unusable `ssh-keygen`, a record `-F` could not parse), and a second
        confirmed run must still leave one copy."""
        _patch_run(mocker, self._dispatcher(ED25519_KEY, RSA_KEY))
        store = tmp_path / "known_hosts"
        store.write_text(f"{HOST} {RSA_KEY}\n")

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.decision == "trusted"
        assert store.read_text() == f"{HOST} {RSA_KEY}\n{HOST} {ED25519_KEY}\n"

    def test_unterminated_existing_file_gets_a_separator(self, mocker, tmp_path):
        _patch_run(mocker, self._dispatcher(ED25519_KEY))
        store = tmp_path / "known_hosts"
        store.write_text("other.example.com ssh-ed25519 AAAAunrelated")  # no newline

        scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert store.read_text() == (
            "other.example.com ssh-ed25519 AAAAunrelated\n" f"{HOST} {ED25519_KEY}\n"
        )

    def test_non_default_port_is_recorded_in_bracketed_form(self, mocker, tmp_path):
        """`ssh-keyscan -p` emits the `[host]:port` line form; it is stored
        verbatim, which is exactly what `ssh -p 2222` later looks up."""
        line = f"[{HOST}]:2222 {ED25519_KEY}"
        _patch_run(
            mocker,
            RunDispatcher(
                keyscan=_cp(["ssh-keyscan"], stdout=line + "\n"),
                keygen_f=_cp(["ssh-keygen"], rc=1),
                keygen_lf=_cp(["ssh-keygen"], stdout="256 SHA256:zZz fingerprint\n"),
            ),
        )
        store = tmp_path / "known_hosts"

        result = scan_and_verify_host_key(
            HOST,
            port=2222,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )

        assert result.decision == "trusted"
        assert store.read_text() == line + "\n"

    def test_mismatch_never_writes(self, mocker, known_hosts):
        _patch_run(mocker, self._dispatcher(ED25519_OTHER, found=ED25519_KEY))
        before = known_hosts.read_text()

        result = scan_and_verify_host_key(
            HOST,
            known_hosts_file=known_hosts,
            interactive=True,
            confirm_fn=lambda _prompt: pytest.fail("must not prompt on mismatch"),
        )

        assert result.decision == "mismatch"
        assert known_hosts.read_text() == before

    def test_second_run_takes_the_trusted_path_without_prompting(
        self, mocker, tmp_path, real_pubkeys
    ):
        """Round-trip against the REAL ssh-keygen: what the first (confirmed)
        run wrote is what the second run's `-F` lookup must find, so the
        operator is asked exactly once (Principle VII — the write is a no-op
        the second time)."""
        key_a, _key_b = real_pubkeys
        store = tmp_path / "known_hosts"

        def dispatch(cmd: list[str], **kwargs: object):
            if cmd[0] == "ssh-keyscan":
                return _cp(cmd, stdout=_keyscan_stdout(key_a))
            return _REAL_RUN(cmd, **kwargs)  # type: ignore[arg-type]

        mocker.patch("remo_cli.core.web_adopt.subprocess.run", side_effect=dispatch)

        first = scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: True,
        )
        after_first = store.read_text()

        second = scan_and_verify_host_key(
            HOST,
            known_hosts_file=store,
            interactive=True,
            confirm_fn=lambda _prompt: pytest.fail("already trusted; must not prompt"),
        )

        assert first.decision == "trusted"
        assert first.detail == "fingerprint confirmed interactively"
        assert second.decision == "trusted"
        assert second.detail == "matches trusted known_hosts entry"
        assert store.read_text() == after_first

    def test_write_failure_warns_but_still_trusts(self, mocker, tmp_path, capsys):
        """The scanned lines are still valid for the payload, so a failed local
        write must not turn a confirmed key into a skipped instance."""
        _patch_run(mocker, self._dispatcher(ED25519_KEY))
        parent = tmp_path / "readonly"
        parent.mkdir()
        store = parent / "known_hosts"
        parent.chmod(0o500)
        try:
            result = scan_and_verify_host_key(
                HOST,
                known_hosts_file=store,
                interactive=True,
                confirm_fn=lambda _prompt: True,
            )
        finally:
            parent.chmod(0o700)

        assert result.decision == "trusted"
        assert result.lines == [f"{HOST} {ED25519_KEY}"]
        assert result.detail == "fingerprint confirmed interactively"
        out = capsys.readouterr().out
        # The path reaches the operator through the OSError rather than being
        # interpolated from `trusted_store` (CodeQL reads that flow as a leaked
        # secret) — it still has to be there, whichever way it arrives.
        assert str(store) in out
        assert not store.exists()


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
        # The fixture store already holds this exact line (the `-F` lookup here
        # is mocked to report only an rsa record), so the confirmed write
        # dedupes to a no-op rather than duplicating it.
        assert known_hosts.read_text() == f"{HOST} {ED25519_KEY}\n"


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


# ---------------------------------------------------------------------------
# Non-default SSH ports (`remo add NAME host:2222`)
# ---------------------------------------------------------------------------
#
# Before this, `remo web push` always scanned port 22. For an added host on
# another port that meant one of two silent failures: nothing answered on 22, so
# the instance was reported "unreachable" and the service key was NEVER
# authorized (`_process_instance` returns before `authorize_service_key`) — with
# a remediation telling the user to check a host that was in fact reachable; or
# a DIFFERENT machine answered on 22 and had its host keys pushed instead.
#
# Both halves have to move together: `ssh-keyscan -p` and the known_hosts lookup
# must agree on OpenSSH's `[host]:port` record form, which is also the form the
# service needs in its trust file to match when it later connects with
# `-o Port=`.

PORTED_HOST_KEYS = f"[{HOST}]:2222 {ED25519_KEY}"


class TestNonDefaultPort:
    def test_lookup_key_follows_openssh_record_form(self):
        # Verified against the real ssh-keygen: `-F 10.0.0.9` does NOT find a
        # `[10.0.0.9]:2222` record, and `-F "[10.0.0.5]:22"` does NOT find a
        # bare `10.0.0.5` one. Neither direction is forgiving.
        assert known_hosts_lookup_key(HOST) == HOST
        assert known_hosts_lookup_key(HOST, 22) == HOST
        assert known_hosts_lookup_key(HOST, 2222) == f"[{HOST}]:2222"

    def test_port_22_argv_is_unchanged(self, mocker, known_hosts):
        # Every provider-managed entry reports ssh_port 22, so this is the path
        # that must stay byte-identical: no `-p`, bare hostname lookup.
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=_keyscan_stdout(ED25519_KEY)),
            keygen_f=_cp(["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY)),
        )
        _patch_run(mocker, dispatcher)

        scan_and_verify_host_key(HOST, port=22, known_hosts_file=known_hosts)

        keyscan_cmd = next(c for c in dispatcher.calls if c[0] == "ssh-keyscan")
        assert "-p" not in keyscan_cmd
        assert keyscan_cmd[-1] == HOST
        keygen_cmd = next(c for c in dispatcher.calls if "-F" in c)
        assert keygen_cmd[keygen_cmd.index("-F") + 1] == HOST

    def test_custom_port_reaches_keyscan_and_the_trust_lookup(
        self, mocker, tmp_path
    ):
        store = tmp_path / "known_hosts"
        store.write_text(PORTED_HOST_KEYS + "\n")
        dispatcher = RunDispatcher(
            keyscan=_cp(
                ["ssh-keyscan"],
                stdout=f"# [{HOST}]:2222 SSH-2.0-OpenSSH_9.6\n{PORTED_HOST_KEYS}\n",
            ),
            keygen_f=_cp(
                ["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY, host=f"[{HOST}]:2222")
            ),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, port=2222, known_hosts_file=store)

        assert result.decision == "trusted"
        keyscan_cmd = next(c for c in dispatcher.calls if c[0] == "ssh-keyscan")
        assert keyscan_cmd[keyscan_cmd.index("-p") + 1] == "2222"
        keygen_cmd = next(c for c in dispatcher.calls if "-F" in c)
        assert keygen_cmd[keygen_cmd.index("-F") + 1] == f"[{HOST}]:2222"

    def test_scanned_lines_carry_the_bracketed_form_to_the_service(
        self, mocker, tmp_path
    ):
        # The service writes these lines verbatim into its own known_hosts and
        # then connects with `-o Port=2222`, where OpenSSH looks up
        # `[host]:2222`. Bare-hostname lines would not match, and the service
        # would report auth_failed for a host that had just been "pushed".
        store = tmp_path / "known_hosts"
        store.write_text(PORTED_HOST_KEYS + "\n")
        dispatcher = RunDispatcher(
            keyscan=_cp(["ssh-keyscan"], stdout=PORTED_HOST_KEYS + "\n"),
            keygen_f=_cp(
                ["ssh-keygen"], stdout=_keygen_f_stdout(ED25519_KEY, host=f"[{HOST}]:2222")
            ),
        )
        _patch_run(mocker, dispatcher)

        result = scan_and_verify_host_key(HOST, port=2222, known_hosts_file=store)

        assert result.lines == [PORTED_HOST_KEYS]

    def test_real_ssh_keygen_agrees_with_the_lookup_key(self, tmp_path):
        """Pin the OpenSSH behavior the whole fix rests on, with the real binary."""
        store = tmp_path / "known_hosts"
        store.write_text(f"[{HOST}]:2222 {ED25519_KEY}\n{HOST} {RSA_KEY}\n")

        def found(query: str) -> bool:
            return (
                _REAL_RUN(
                    ["ssh-keygen", "-F", query, "-f", str(store)],
                    capture_output=True,
                ).returncode
                == 0
            )

        assert found(known_hosts_lookup_key(HOST, 2222))
        assert found(known_hosts_lookup_key(HOST, 22))
        # The two forms are NOT interchangeable in either direction.
        assert not found(f"[{HOST}]:22")


class TestPushThreadsThePort:
    def test_process_instance_scans_the_entrys_own_port(self, mocker, tmp_path):
        # The whole fix is worthless if the caller keeps passing nothing.
        scan = mocker.patch(
            "remo_cli.core.web_adopt.scan_and_verify_host_key",
            return_value=HostKeyScan("unreachable", detail="stub"),
        )
        host = KnownHost(
            type="ssh",
            name="mbp",
            host=HOST,
            user="remo",
            instance_id="2222",
            access_mode="direct",
        )

        _process_instance(host, "ssh-ed25519 AAAA", interactive=False, host_keys={})

        assert scan.call_args.kwargs["port"] == 2222

    def test_provider_hosts_still_scan_port_22(self, mocker):
        scan = mocker.patch(
            "remo_cli.core.web_adopt.scan_and_verify_host_key",
            return_value=HostKeyScan("unreachable", detail="stub"),
        )
        host = KnownHost(
            type="hetzner",
            name="web1",
            host=HOST,
            user="remo",
            instance_id="",
            access_mode="direct",
        )

        _process_instance(host, "ssh-ed25519 AAAA", interactive=False, host_keys={})

        assert scan.call_args.kwargs["port"] == 22


class TestAuthorizeFailureRemediation:
    """#157: an authorize failure caused by an untrusted host key used to point
    the operator at a host that was demonstrably reachable."""

    def _run(self, mocker, error: str, *, port: int = 22) -> str:
        mocker.patch(
            "remo_cli.core.web_adopt.scan_and_verify_host_key",
            return_value=HostKeyScan("trusted", lines=[f"{HOST} {ED25519_KEY}"]),
        )
        mocker.patch(
            "remo_cli.core.web_adopt.authorize_service_key",
            return_value=(False, error),
        )
        host = KnownHost(
            type="ssh",
            name="mbp",
            host=HOST,
            user="remo",
            instance_id=str(port),
            access_mode="direct",
        )
        outcome = _process_instance(
            host, "ssh-ed25519 AAAA", interactive=False, host_keys={}
        )
        assert outcome.outcome == "skipped_unreachable"
        assert error in outcome.detail
        return outcome.remediation or ""

    def test_host_key_verification_failure_names_the_real_cause(self, mocker):
        remediation = self._run(
            mocker, "Host key verification failed.\r\nlost connection"
        )

        assert "known_hosts" in remediation
        assert HOST in remediation
        assert "remo shell mbp" in remediation
        assert "ssh remo@" not in remediation

    def test_custom_port_remediation_names_the_bracketed_record(self, mocker):
        remediation = self._run(mocker, "Host key verification failed.", port=2222)

        assert f"[{HOST}]:2222" in remediation

    def test_other_failures_keep_the_generic_ssh_hint(self, mocker):
        remediation = self._run(mocker, "Permission denied (publickey).")

        assert f"ssh remo@{HOST}" in remediation
        assert "known_hosts" not in remediation
