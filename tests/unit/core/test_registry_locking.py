"""Tests for the advisory locking mechanism in remo_cli.core.registry (T028).

Covers research.md R3: timeout -> RegistryBusyError, degraded (unlocked)
proceed with a one-time warning when flock is unavailable on the filesystem,
and atomicity of the underlying write primitive under a simulated
mid-write crash (FR-018 -- readers never see a torn file).
"""

from __future__ import annotations

import errno
import fcntl
import os
import time

import pytest

from remo_cli.core import registry
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost


# -----------------------------------------------------------------------
# Timeout -> RegistryBusyError
# -----------------------------------------------------------------------


def test_lock_timeout_raises_registry_busy_error(tmp_config_dir):
    """An externally-held exclusive flock causes registry_lock() to time out."""
    lock_path = registry.get_registry_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        start = time.monotonic()
        with pytest.raises(registry.RegistryBusyError):
            with registry.registry_lock(timeout_s=0.3):
                pass  # pragma: no cover - never reached, lock is held externally
        elapsed = time.monotonic() - start

        # Bounded wall-clock wait: close to the explicit short timeout, not the
        # production 5s default, and not near-instant either (retry loop ran).
        assert 0.25 <= elapsed <= 2.0
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# -----------------------------------------------------------------------
# Degraded (unlocked) proceed on flock OSError
# -----------------------------------------------------------------------


def test_flock_oserror_degrades_to_unlocked_proceed_with_one_time_warning(
    tmp_config_dir, monkeypatch, capsys
):
    """flock raising ENOLCK degrades to an unlocked proceed, warned only once."""
    monkeypatch.setattr(registry, "_lock_degradation_warned", False)

    real_flock = fcntl.flock

    def fake_flock(fd, operation):
        if operation & fcntl.LOCK_EX and not (operation & fcntl.LOCK_UN):
            raise OSError(errno.ENOLCK, "no locks available")
        return real_flock(fd, operation)

    monkeypatch.setattr(fcntl, "flock", fake_flock)

    # First call: does not raise RegistryBusyError, proceeds unlocked, warns.
    entered = False
    with registry.registry_lock(timeout_s=1.0):
        entered = True
    assert entered

    first_output = capsys.readouterr().out
    assert "registry locking unavailable" in first_output

    # Second call in the same process: warning is one-time, must not repeat.
    entered_again = False
    with registry.registry_lock(timeout_s=1.0):
        entered_again = True
    assert entered_again

    second_output = capsys.readouterr().out
    assert "registry locking unavailable" not in second_output


# -----------------------------------------------------------------------
# Atomicity: a failed rename must never leave a torn/partial file
# -----------------------------------------------------------------------


def test_atomic_write_failure_leaves_prior_registry_state_intact(tmp_config_dir, monkeypatch):
    """A crash between temp-file write and os.replace() must not corrupt
    or partially overwrite the previously-committed registry.json, and must
    not leave a leftover temp file behind (FR-018)."""
    host = KnownHost(
        type="ssh", name="known-good", host="10.0.0.5", user="remo", access_mode="direct"
    )
    save_known_host(host)

    registry_path = registry.get_registry_path()
    original_content = registry_path.read_text()
    assert "known-good" in original_content

    def raising_replace(*args, **kwargs):
        raise OSError("simulated crash between temp-file write and rename")

    monkeypatch.setattr(os, "replace", raising_replace)

    with pytest.raises(OSError):
        registry._atomic_write_text(registry_path, "this must never land on disk")

    # Original file is byte-for-byte untouched.
    assert registry_path.read_text() == original_content

    # No leftover temp file survives the failed write.
    leftover = list(registry_path.parent.glob(".registry_tmp_*"))
    assert leftover == []


def test_atomic_write_failure_when_target_never_existed_leaves_nothing(
    tmp_config_dir, monkeypatch
):
    """Same crash-mid-write scenario, but for a target path that never
    existed: no file should appear at the target, and no temp file should
    be left behind."""
    target = registry.get_registry_path().parent / "fresh_never_written.json"
    assert not target.exists()

    def raising_replace(*args, **kwargs):
        raise OSError("simulated crash between temp-file write and rename")

    monkeypatch.setattr(os, "replace", raising_replace)

    with pytest.raises(OSError):
        registry._atomic_write_text(target, "should never land on disk")

    assert not target.exists()
    leftover = list(target.parent.glob(".registry_tmp_*"))
    assert leftover == []
