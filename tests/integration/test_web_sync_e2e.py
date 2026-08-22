"""End-to-end integration test for `remo web sync` (023) against a LIVE service.

Mirrors tests/integration/test_web_adopt_e2e.py's harness (imported below): a
real uvicorn subprocess with its own REMO_HOME, a workstation-side temp
registry in the test process, pairing codes minted over HTTP, and selective
SSH mocks (canned scans for the known direct instances, real failure for the
`.invalid` one).

Scenarios:

1. Divergence on BOTH sides after an initial push — a host added locally and
   a host added "in the console" (simulated by editing the service's state
   volume directly, exactly what the registry-admin API does) — converges in
   one `run_web_sync`: the local add is pushed (keyscanned + authorized), the
   console add is pulled (never keyscanned), both stores match afterwards,
   and the push-cache generation agrees with the service marker.
2. A concurrent console-side bump BETWEEN sync's GET and its PUT exercises
   the real route's 409 `generation_conflict` and the driver's re-merge
   retry, converging on the second attempt.
"""

from __future__ import annotations

import json

import pytest

from remo_cli.core import registry as core_registry
from remo_cli.core import web_adopt, web_sync
from remo_cli.core.known_hosts import save_known_host
from remo_cli.models.host import KnownHost

# Reuse the adopt-e2e harness wholesale: fixtures import by name.
from tests.integration.test_web_adopt_e2e import (  # noqa: F401
    _CANNED_HOST_KEY_LINES,
    _NEW_ADDR,
    _NEW_CANNED_HOST_KEY_LINES,
    _NEW_DIRECT,
    LiveService,
    _http_json,
    adoption_ssh_mocks,
    requires_live_web,
    service,
    workstation,
)

#: The "console-added" host: another TEST-NET-1 address. Its keys were
#: confirmed in the browser (trust-key), i.e. they live in the SERVICE trust
#: file — the workstation never scans it.
_CONSOLE_ADDR = "192.0.2.30"
_CONSOLE_HOST = KnownHost(
    type="ssh",
    name="consolebox",
    host=_CONSOLE_ADDR,
    user="paul",
    instance_id="22",
    access_mode="direct",
)
_CONSOLE_KEY_LINES = [
    f"{_CONSOLE_ADDR} ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIFsyncTESTconsoleboxKeyMaterialWTOQqmpvSF3Y5LF",
]


def _console_side_add(svc: LiveService) -> None:
    """Simulate the registry-admin API's effect directly on the state volume:
    append the entry to registry.json, its keys to the trust file, and bump
    the mirror marker with a web-origin last_change."""
    doc = json.loads(svc.registry_path.read_text())
    doc["hosts"].append(core_registry.known_host_to_entry(_CONSOLE_HOST))
    doc["hosts"].sort(key=lambda e: (e["type"], e["name"]))
    svc.registry_path.write_text(json.dumps(doc, indent=2, sort_keys=True))

    trust = svc.identity_dir / "known_hosts"
    existing = trust.read_text() if trust.exists() else ""
    trust.write_text(existing + "".join(line + "\n" for line in _CONSOLE_KEY_LINES))

    _bump_marker(svc)


def _bump_marker(svc: LiveService) -> int:
    meta_path = svc.identity_dir / "mirror-meta.json"
    meta = json.loads(meta_path.read_text())
    meta["generation"] += 1
    meta["last_change"] = {
        "at": "2026-08-20T00:00:00+00:00",
        "origin": "web",
        "workstation": None,
    }
    meta_path.write_text(json.dumps(meta))
    return int(meta["generation"])


def _service_generation(svc: LiveService) -> int:
    meta = json.loads((svc.identity_dir / "mirror-meta.json").read_text())
    return int(meta["generation"])


@requires_live_web
def test_bidirectional_sync_converges_both_stores(
    service: LiveService,
    workstation,
    adoption_ssh_mocks: dict,
):
    # Seed: initial push adopts the deployment (generation 1, cache v4 base).
    result = web_adopt.run_push(service.url, service.mint(), interactive=False)
    assert result.deployment_id

    # Diverge both sides: newbox added locally, consolebox added "in the console".
    save_known_host(_NEW_DIRECT)
    _console_side_add(service)
    assert _service_generation(service) == 2

    rc = web_sync.run_web_sync(
        service.url, service.mint(), assume_yes=True, interactive=False
    )
    assert rc == 0

    # Local registry gained the console-added host.
    local_names = {h.name for h in core_registry.read_registry(readonly=True).hosts}
    assert {"consolebox", "newbox"} <= local_names

    # Service registry gained the locally-added host (and kept consolebox).
    service_doc = json.loads(service.registry_path.read_text())
    service_names = {e["name"] for e in service_doc["hosts"]}
    assert {"consolebox", "newbox"} <= service_names
    assert local_names == service_names

    # The pulled host was NEVER keyscanned or authorized by the workstation;
    # the pushed one was.
    assert _CONSOLE_ADDR not in adoption_ssh_mocks["scanned"]
    assert _NEW_ADDR in adoption_ssh_mocks["scanned"]
    assert any(name == "newbox" for name, _key in adoption_ssh_mocks["authorized"])

    # Pulled key lines were round-tripped into the (wholesale) service trust
    # file rather than dropped.
    trust = (service.identity_dir / "known_hosts").read_text()
    assert _CONSOLE_KEY_LINES[0] in trust

    # Generations agree: service marker == push-cache generation (== 3: push,
    # console add, sync PUT).
    cache = web_adopt.load_push_cache()[result.deployment_id]
    assert _service_generation(service) == 3
    assert cache.mirror_generation == 3
    # And the pulled entry is now part of the merge base.
    assert cache.instances["consolebox"].entry == core_registry.known_host_to_entry(
        _CONSOLE_HOST
    )


@requires_live_web
def test_concurrent_bump_between_get_and_put_retries_via_409(
    service: LiveService,
    workstation,
    adoption_ssh_mocks: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    result = web_adopt.run_push(service.url, service.mint(), interactive=False)
    assert result.deployment_id
    save_known_host(_NEW_DIRECT)

    # Interleave: after sync's GET (render is the first post-GET step), bump
    # the marker on the service's state volume — the real PUT route then
    # answers 409 generation_conflict and the driver must re-merge + retry.
    real_render = web_sync.render_sync_plan
    bumped = {"done": False}

    def render_and_bump(plan, deployment_id):
        real_render(plan, deployment_id)
        if not bumped["done"]:
            bumped["done"] = True
            _bump_marker(service)

    monkeypatch.setattr(web_sync, "render_sync_plan", render_and_bump)

    rc = web_sync.run_web_sync(
        service.url, service.mint(), assume_yes=True, interactive=False
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "re-merging and retrying" in out

    # Converged despite the race: push landed, generations agree.
    service_doc = json.loads(service.registry_path.read_text())
    assert "newbox" in {e["name"] for e in service_doc["hosts"]}
    cache = web_adopt.load_push_cache()[result.deployment_id]
    assert cache.mirror_generation == _service_generation(service)
