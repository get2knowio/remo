"""Hetzner's half of the #107 `observed` audit.

Moved from remo's tests/unit/providers/test_host_user_consistency.py when
Hetzner became the out-of-tree `remo-provider-hetzner` plugin. A probe that
builds `DiscoveredHost` without `observed=` silently opts back into the legacy
"every non-empty field was observed" semantics #87 replaced, so a provider-filled
default would clobber a hand-edited registry value on the next `sync`.
"""

from __future__ import annotations

import inspect

import remo_provider_hetzner.provider as providers_hetzner
from remo_cli.core.reconcile import SyncScope


def test_hetzner_observes_only_the_address(mocker):
    mocker.patch(
        "remo_provider_hetzner.provider._hetzner_api_paged",
        return_value=(
            [
                {
                    "name": "dev1",
                    "public_net": {"ipv4": {"ip": "1.2.3.4"}},
                    "labels": {"remo": "true"},
                    "status": "running",
                }
            ],
            True,
        ),
    )
    result = providers_hetzner._probe(
        SyncScope(type="hetzner", host="", region=""), include_all=False
    )

    assert result.hosts[0].observed == frozenset({"host"})


def test_probe_does_not_rely_on_legacy_semantics():
    source = inspect.getsource(providers_hetzner._probe)
    assert "DiscoveredHost(" in source
    assert "observed=" in source, (
        f"{providers_hetzner.__name__}._probe builds DiscoveredHost without observed=, "
        "falling back to the legacy semantics #87 replaced"
    )
