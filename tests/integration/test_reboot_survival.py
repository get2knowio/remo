"""US2 T052: assert the broker_install role's systemd unit is reboot-survivable.

Stub-based: parses the rendered service unit and asserts the required
properties for survival across reboot. A fixture inventory + check-mode
end-to-end test against a real container is out of scope for this CI tier.
"""

from __future__ import annotations

from pathlib import Path

UNIT_TEMPLATE = Path("ansible/roles/broker_install/templates/remo-broker.service.j2")


def test_template_exists():
    assert UNIT_TEMPLATE.exists(), f"missing systemd unit template: {UNIT_TEMPLATE}"


def test_unit_restarts_on_failure():
    content = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "Restart=on-failure" in content


def test_unit_restart_backoff():
    content = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "RestartSec=5s" in content


def test_unit_wantedby_multiuser():
    content = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "WantedBy=multi-user.target" in content


def test_unit_loads_bootstrap_credential():
    content = UNIT_TEMPLATE.read_text(encoding="utf-8")
    assert "LoadCredential=bootstrap-token:" in content
