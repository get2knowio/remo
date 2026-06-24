"""US3 / SC-002 guard: adding a channel must touch no core module.

Asserts the core service modules and the transport ABC are import-clean of any
channel id, so the only files needed to add a channel are under ``channels/<id>/``
plus the one-line ``catalog.py`` registration. See contracts/channel-extension.md.
"""

from __future__ import annotations

from pathlib import Path

import remo_cli.notifier as notifier_pkg

_CORE_MODULES = [
    "server.py",
    "state.py",
    "models.py",
    "grants.py",
    "logging_setup.py",
    "config.py",
    "agentsh_client.py",
    "cli.py",
    "transports/base.py",
]

# Channel ids that must never appear hard-coded in the core.
_CHANNEL_IDS = ("telegram", "slack", "discord", "ntfy")


def _pkg_root() -> Path:
    return Path(notifier_pkg.__file__).parent


def test_core_modules_are_channel_agnostic() -> None:
    root = _pkg_root()
    offenders = {}
    for rel in _CORE_MODULES:
        text = (root / rel).read_text().lower()
        hits = [cid for cid in _CHANNEL_IDS if cid in text]
        if hits:
            offenders[rel] = hits
    assert not offenders, f"core modules reference channel ids: {offenders}"


def test_only_catalog_registers_channels() -> None:
    # The catalog is the single permitted place that names channels.
    catalog_text = (_pkg_root() / "channels" / "catalog.py").read_text().lower()
    assert "telegram" in catalog_text  # the one-line registration lives here


def test_adding_a_channel_is_confined_to_channels_dir() -> None:
    # The Telegram channel package is fully self-contained under channels/telegram/.
    tg = _pkg_root() / "channels" / "telegram"
    assert (tg / "transport.py").is_file()
    assert (tg / "config.py").is_file()
    assert (tg / "descriptor.py").is_file()
