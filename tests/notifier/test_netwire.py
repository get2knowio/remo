"""Tests for the Option-A network-wiring helper (issue #42 §2.2).

Drives `remo-notifier-netwire.sh` with a fake `docker` (injected via $DOCKER) that
records mutating calls and answers inspects from a small on-disk state, so we can
assert the connect/disconnect/idempotency/no-op behavior without a real daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _helper() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "ansible" / "roles" / "remo_notifier" / "files" / "remo-notifier-netwire.sh"
        if cand.is_file():
            return cand
    raise RuntimeError("helper not found")


HELPER = _helper()

# Fake docker: state lives under $STATE — exists/<name> (presence), nets/<name>
# (one network per line), members/<net> (one container per line). Mutations append
# to actions and update state so idempotency is observable.
FAKE_DOCKER = r"""#!/bin/sh
S="$STATE"
case "$1" in
inspect)
  if [ "$2" = "-f" ]; then
    name="$4"; cat "$S/nets/$name" 2>/dev/null || true
  else
    name="$2"; [ -f "$S/exists/$name" ] && exit 0 || exit 1
  fi
  ;;
network)
  case "$2" in
  inspect) net="$5"; cat "$S/members/$net" 2>/dev/null || true ;;
  connect) net="$3"; name="$4"; echo "connect $net $name" >>"$S/actions"
    echo "$net" >>"$S/nets/$name"; echo "$name" >>"$S/members/$net" ;;
  disconnect) net="$3"; name="$4"; echo "disconnect $net $name" >>"$S/actions" ;;
  esac
  ;;
esac
"""


class _State:
    def __init__(self, root: Path) -> None:
        self.root = root
        for sub in ("exists", "nets", "members"):
            (root / sub).mkdir(parents=True, exist_ok=True)
        self.docker = root / "docker"
        self.docker.write_text(FAKE_DOCKER)
        self.docker.chmod(0o755)
        self.actions = root / "actions"

    def exists(self, name: str) -> None:
        (self.root / "exists" / name).write_text("")

    def on_nets(self, name: str, *nets: str) -> None:
        self.exists(name)
        (self.root / "nets" / name).write_text("".join(f"{n}\n" for n in nets))

    def members(self, net: str, *names: str) -> None:
        (self.root / "members" / net).write_text("".join(f"{n}\n" for n in names))

    def run(self, action: str, container: str) -> subprocess.CompletedProcess:
        env = {"STATE": str(self.root), "DOCKER": str(self.docker), "PATH": "/usr/bin:/bin"}
        return subprocess.run(
            ["sh", str(HELPER), action, container], env=env, text=True, capture_output=True
        )

    def recorded(self) -> list[str]:
        return self.actions.read_text().splitlines() if self.actions.exists() else []


@pytest.fixture()
def state(tmp_path: Path) -> _State:
    return _State(tmp_path)


def test_noop_when_notifier_absent(state: _State) -> None:
    state.on_nets("proj-a", "net-a")  # container exists, notifier does not
    proc = state.run("connect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == []
    assert "absent" in proc.stderr


def test_noop_when_only_bridge(state: _State) -> None:
    state.exists("remo-notifier")
    state.on_nets("proj-a", "bridge")  # only the shared bridge → nothing to isolate
    proc = state.run("connect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == []


def test_connect_joins_user_network(state: _State) -> None:
    state.on_nets("remo-notifier", "bridge")  # notifier present, not on net-a yet
    state.on_nets("proj-a", "net-a")
    proc = state.run("connect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == ["connect net-a remo-notifier"]


def test_connect_is_idempotent(state: _State) -> None:
    state.on_nets("remo-notifier", "bridge", "net-a")  # already attached
    state.on_nets("proj-a", "net-a")
    proc = state.run("connect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == []
    assert "already on" in proc.stderr


def test_disconnect_when_no_others(state: _State) -> None:
    state.on_nets("remo-notifier", "bridge", "net-a")
    state.on_nets("proj-a", "net-a")
    state.members("net-a", "remo-notifier")  # proj-a already gone
    proc = state.run("disconnect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == ["disconnect net-a remo-notifier"]


def test_disconnect_keeps_when_others_attached(state: _State) -> None:
    state.on_nets("remo-notifier", "bridge", "net-shared")
    state.on_nets("proj-a", "net-shared")
    state.members("net-shared", "remo-notifier", "proj-b")  # another source remains
    proc = state.run("disconnect", "proj-a")
    assert proc.returncode == 0
    assert state.recorded() == []
    assert "other sources still attached" in proc.stderr


def test_usage_error_without_args(state: _State) -> None:
    proc = state.run("connect", "")
    assert proc.returncode == 2
