"""Hetzner sync-reconcile integration tests, moved out of remo's core
``tests/integration/test_sync_reconcile.py`` when Hetzner became a plugin.

These exercise the provider's `sync()` end-to-end against a *real* temporary
registry: only the Hetzner API boundary (`_hetzner_api`) is mocked, everything
downstream of the probe -- diffing, consent, the single atomic
`mutate_registry()` write, and exit codes -- runs for real through
`core/reconcile.py`.
"""

from __future__ import annotations

import json

from remo_cli.core import reconcile
from remo_cli.models.host import KnownHost

import remo_provider_hetzner.provider as providers_hetzner


def _seed_empty_registry(config_dir) -> None:
    doc = {"version": 2, "hosts": []}
    (config_dir / "registry.json").write_text(json.dumps(doc, indent=2) + "\n")


def read_registry_hosts(config_dir) -> list[KnownHost]:
    """Read back the hosts actually persisted to registry.json."""
    from remo_cli.core.registry import read_registry

    return read_registry(readonly=True).hosts


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
        _seed_empty_registry(tmp_config_dir)

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
