"""US4 / T028-T029: channel switch is a fail-secure restart on one service.

Two facets: (1) the Ansible role yields a single service on the unchanged
bind/port with a channel-templated image (the switch is a clean image swap);
(2) the core drains in-flight approvals to deny and never carries grants across
the restart (FR-008/FR-009/FR-015).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remo_cli.notifier.grants import (
    Grant,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    TargetMatchType,
)
from remo_cli.notifier.models import Decision
from remo_cli.notifier.server import create_app

from ..conftest import FakeTransport, make_request


def _role_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "ansible").is_dir():
            return parent / "ansible" / "roles" / "remo_notifier"
    raise RuntimeError("ansible role not found")


def _any_grant() -> Grant:
    return Grant.create(
        predicate=GrantPredicate(kind="command", target_match=TargetMatchType.any),
        scope=GrantScope(type=GrantScopeType.glob),
        ttl_seconds=3600, created_by="t", source_approval_id="x",
    )


# --- T028: single service / clean swap --------------------------------------
def test_service_template_force_removes_and_templates_image() -> None:
    svc = (_role_dir() / "templates" / "remo-notifier.service.j2").read_text()
    assert "docker rm -f remo-notifier" in svc  # ExecStartPre clean swap
    assert "{{ remo_notifier_image }}" in svc
    assert "--name remo-notifier" in svc  # single service name (one per host)


def test_image_default_is_channel_templated() -> None:
    defaults = (_role_dir() / "defaults" / "main.yml").read_text()
    assert "remo-notifier-{{ remo_notifier_channel }}:{{ remo_notifier_version }}" in defaults
    # Bind/port are not channel-specific (unchanged across a switch).
    assert "remo_notifier_listen_port: 18181" in defaults


# --- T029: fail-secure restart ----------------------------------------------
async def test_shutdown_drains_pending_to_deny(config, fake_transport) -> None:
    app = create_app(config, fake_transport)  # no agentsh
    async with app.router.lifespan_context(app):
        entry = await app.state.registry.reserve("x", make_request())
    # Lifespan exit (shutdown) drains every pending approval to deny.
    assert entry.future.done()
    assert entry.future.result().decision is Decision.deny


def test_grants_do_not_survive_a_restart(config, fake_transport) -> None:
    app1 = create_app(config, fake_transport)
    g = _any_grant()
    app1.state.grant_store._grants[g.grant_id] = g  # noqa: SLF001
    with TestClient(app1):
        assert app1.state.grant_store.count() == 1
    # A switch is a process restart: the new app starts with no grants (FR-009).
    app2 = create_app(config, FakeTransport())
    assert app2.state.grant_store.count() == 0
