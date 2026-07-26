"""Cross-provider sync-reconcile integration tests (feature 016-sync-reconcile).

These exercise a provider's `sync()` end-to-end against a *real* temporary
registry (`tmp_config_dir` + `seed_registry`): only the SSH/API boundary is
mocked, everything downstream of the probe -- diffing, consent, the single
atomic `mutate_registry()` write, and exit codes -- runs for real through
`core/reconcile.py`.

This module is shared across providers. Each provider's functions are
grouped under a `Test<Provider>...` class and its test names are prefixed
accordingly (e.g. `test_incus_...`), so later phases (AWS, Proxmox, Hetzner)
can append their own classes as siblings without touching this one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remo_cli.core import reconcile
from remo_cli.models.host import KnownHost
from remo_cli.providers import hetzner as providers_hetzner
from remo_cli.providers import incus as providers_incus

from tests.conftest import seed_registry


def _completed(rc: int, stdout: str = "", stderr: str = "") -> MagicMock:
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def _registry_bytes(config_dir) -> bytes:
    return (config_dir / "registry.json").read_bytes()


@pytest.fixture
def existing_incus_host() -> KnownHost:
    return KnownHost(
        type="incus",
        name="myhost/dev1",
        host="dev1.incus",
        user="remo",
        instance_id="paul",
        access_mode="direct",
    )


# ---------------------------------------------------------------------------
# Incus (016-sync-reconcile User Story 1 -- the MVP; see tasks.md T023)
# ---------------------------------------------------------------------------


class TestIncusSyncSafetyMatrix:
    def test_incus_empty_probe_non_interactive_decline_leaves_registry_unchanged(
        self, tmp_config_dir, existing_incus_host, mocker
    ):
        seed_registry(tmp_config_dir, [existing_incus_host])
        before = _registry_bytes(tmp_config_dir)

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout=""),
        )
        mocker.patch("sys.stdin.isatty", return_value=False)

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=False)

        assert rc == reconcile.EXIT_ABORTED
        assert _registry_bytes(tmp_config_dir) == before

    def test_incus_empty_probe_interactive_decline_leaves_registry_unchanged(
        self, tmp_config_dir, existing_incus_host, mocker
    ):
        seed_registry(tmp_config_dir, [existing_incus_host])
        before = _registry_bytes(tmp_config_dir)

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout=""),
        )
        mocker.patch("sys.stdin.isatty", return_value=True)
        mocker.patch("remo_cli.core.reconcile.confirm", return_value=False)

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=False)

        assert rc == reconcile.EXIT_ABORTED
        assert _registry_bytes(tmp_config_dir) == before

    def test_incus_empty_probe_auto_confirm_removes_entry_and_names_it(
        self, tmp_config_dir, existing_incus_host, mocker, capsys
    ):
        seed_registry(tmp_config_dir, [existing_incus_host])

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout=""),
        )

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=True)

        assert rc == reconcile.EXIT_OK
        registry = read_registry_hosts(tmp_config_dir)
        assert registry == []
        out = capsys.readouterr().out
        assert "myhost/dev1" in out

    def test_incus_probe_failure_leaves_registry_unchanged(
        self, tmp_config_dir, existing_incus_host, mocker
    ):
        seed_registry(tmp_config_dir, [existing_incus_host])
        before = _registry_bytes(tmp_config_dir)

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(1, stderr="ssh: connection refused"),
        )

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=True)

        assert rc == reconcile.EXIT_FAILURE
        assert _registry_bytes(tmp_config_dir) == before

    def test_incus_dry_run_leaves_registry_unchanged_and_never_prompts(
        self, tmp_config_dir, existing_incus_host, mocker
    ):
        seed_registry(tmp_config_dir, [existing_incus_host])
        before = _registry_bytes(tmp_config_dir)

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout=""),
        )
        confirm_spy = mocker.patch("remo_cli.core.reconcile.confirm")

        rc = providers_incus.sync(host="myhost", user="paul", dry_run=True)

        assert rc == reconcile.EXIT_OK
        assert _registry_bytes(tmp_config_dir) == before
        confirm_spy.assert_not_called()

    def test_incus_additions_only_needs_no_prompt_regardless_of_tty(
        self, tmp_config_dir, mocker
    ):
        # Nothing in the registry yet; the probe sees one marked container.
        seed_registry(tmp_config_dir, [])

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout="dev1,true\n"),
        )
        confirm_spy = mocker.patch("remo_cli.core.reconcile.confirm")
        mocker.patch("sys.stdin.isatty", return_value=False)

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=False)

        assert rc == reconcile.EXIT_OK
        confirm_spy.assert_not_called()
        registry = read_registry_hosts(tmp_config_dir)
        assert [h.name for h in registry] == ["myhost/dev1"]

    def test_incus_no_removals_is_noop_and_exits_0(self, tmp_config_dir, mocker):
        seed_registry(tmp_config_dir, [])
        before = _registry_bytes(tmp_config_dir)

        mocker.patch(
            "remo_cli.providers.incus._ssh_run_on_incus_host",
            return_value=_completed(0, stdout=""),
        )

        rc = providers_incus.sync(host="myhost", user="paul", auto_confirm=False)

        assert rc == reconcile.EXIT_OK
        assert _registry_bytes(tmp_config_dir) == before


# ---------------------------------------------------------------------------
# Hetzner (016-sync-reconcile User Story 5 -- adoption durability; T062)
#
# Regression guard for the marker-semantics change: an entry adopted via
# `--all` is unmarked (no `remo` label) by definition. It must survive a
# subsequent *plain* sync purely by being present -- reported as
# `retained_unmarked`, never proposed for removal -- with no confirmation
# prompt attempted, since nothing needs removing.
# ---------------------------------------------------------------------------


def _hetzner_page(name: str, ip: str = "9.9.9.9") -> dict:
    return {
        "servers": [
            {
                "id": 1,
                "name": name,
                "status": "running",
                "labels": {},
                "public_net": {"ipv4": {"ip": ip}},
            }
        ],
        "meta": {"pagination": {"next_page": None}},
    }


class TestHetznerAdoptionDurability:
    def test_all_adopted_entry_is_retained_unmarked_by_a_subsequent_plain_sync(
        self, tmp_config_dir, mocker, capsys
    ):
        seed_registry(tmp_config_dir, [])

        # First sync: --all adopts the unmarked, unlabelled server.
        mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[_hetzner_page("dev1")]
        )
        rc = providers_hetzner.sync(include_all=True, auto_confirm=True)
        assert rc == reconcile.EXIT_OK
        assert [h.name for h in read_registry_hosts(tmp_config_dir)] == ["dev1"]

        # Second sync: plain (no --all). The server is still unlabelled, but
        # it must be retained -- not dropped -- because it is still present.
        mocker.patch.object(
            providers_hetzner, "_hetzner_api", side_effect=[_hetzner_page("dev1")]
        )
        confirm_spy = mocker.patch("remo_cli.core.reconcile.confirm")
        mocker.patch("sys.stdin.isatty", return_value=True)

        rc = providers_hetzner.sync(include_all=False, auto_confirm=False)

        assert rc == reconcile.EXIT_OK
        confirm_spy.assert_not_called()
        assert [h.name for h in read_registry_hosts(tmp_config_dir)] == ["dev1"]

        out = capsys.readouterr().out
        assert "retained" in out
        assert "not remo-marked" in out
        assert "dev1" in out


def read_registry_hosts(config_dir) -> list[KnownHost]:
    """Read back the hosts actually persisted to registry.json."""
    from remo_cli.core.registry import read_registry

    return read_registry(readonly=True).hosts
