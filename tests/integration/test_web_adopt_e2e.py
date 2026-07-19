"""End-to-end integration test for CLI-to-Web adoption (012-web-adopt-pairing).

Proves quickstart.md scenarios B (first-time adoption via a page-minted pairing
code), idempotent re-run, and E (`remo web push` after adoption) against a LIVE
local service: a real uvicorn subprocess serving `create_app()` on an ephemeral
127.0.0.1 port, in the **network-restricted** operator-auth posture
(`REMO_WEB_OPERATOR_AUTH=none`) so the test can mint a pairing code over HTTP
without a forward-auth proxy, with its own writable service-side `REMO_HOME`
(distinct from the workstation-side temp `REMO_HOME`/`$HOME` the adopt flow runs
under in the test process). The service subprocess and the test process
therefore see genuinely different registries/homes, exactly like a real
workstation adopting a real container.

Each adopt/push obtains a FRESH pairing code (minted via
`POST /api/v1/pairing/mint`); the code authenticates the whole flow and the
service ends the session on the terminal `POST /setup/verify` (FR-007), so a
subsequent probe re-mints.

No real SSH instances exist here, so the workstation-side per-instance SSH
work is selectively substituted (see `adoption_ssh_mocks`):

* the direct-access instance's `scan_and_verify_host_key` returns `trusted`
  with canned known_hosts lines and `authorize_service_key` reports success;
* the `unreachable.invalid` instance is deliberately left UNMOCKED so its
  keyscan genuinely fails (reserved `.invalid` TLD -> DNS failure) and the
  flow classifies it `skipped_unreachable` for real;
* the SSM entry never reaches SSH at all (`skipped_by_design`, FR-012).

The origin-allowlist middleware exempts Origin-less requests to
/api/v1/setup/* (code-authenticated surface, no ambient credentials -- see
web/app.py), which is what lets the Origin-less `SetupApiClient` used here
talk to the live service exactly like the real CLI does. The browser-facing
`POST /pairing/mint` DOES require an allowed Origin, which the mint helper sets.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from remo_cli.core import web_adopt
from remo_cli.models.host import KnownHost

# ---------------------------------------------------------------------------
# Collection-time gating (same technique as the other integration suites:
# a skipif computed once at import, no pyproject marker machinery).
# ---------------------------------------------------------------------------


def _web_extra_available() -> bool:
    return all(
        importlib.util.find_spec(mod) is not None for mod in ("fastapi", "uvicorn")
    )


requires_live_web = pytest.mark.skipif(
    not (_web_extra_available() and shutil.which("ssh-keygen")),
    reason="requires the `web` extra (fastapi/uvicorn) and ssh-keygen on PATH",
)

# ---------------------------------------------------------------------------
# Registry fixture data
# ---------------------------------------------------------------------------

#: RFC 5737 TEST-NET-1 -- guaranteed non-routable, so the service-side verify
#: round-trip to this "instance" fails within its own bounded 5s timeout.
_DIRECT_ADDR = "192.0.2.10"
#: Reserved TLD (RFC 2606) -- DNS resolution fails fast and deterministically.
_UNREACHABLE_ADDR = "unreachable.invalid"

_DIRECT = KnownHost(type="incus", name="webbox", host=_DIRECT_ADDR, user="remo")
_SSM = KnownHost(
    type="aws",
    name="ssmbox",
    host="10.0.0.5",
    user="remo",
    instance_id="i-0123456789abcdef0",
    access_mode="ssm",
    region="us-east-1",
)
_UNREACHABLE = KnownHost(
    type="hetzner", name="ghost", host=_UNREACHABLE_ADDR, user="remo"
)
_HOSTS = [_DIRECT, _SSM, _UNREACHABLE]

#: What the service-side registry mirror must contain, byte-for-byte
#: (colon-delimited `KnownHost.to_line()` per entry, payload order).
_EXPECTED_SERVICE_REGISTRY = "".join(h.to_line() + "\n" for h in _HOSTS)

#: Canned "scanned & workstation-trusted" host keys for the direct instance.
#: Structurally valid known_hosts lines (plausible key type + base64) so the
#: service's PUT-side payload validation accepts them unmodified.
_CANNED_HOST_KEY_LINES = [
    f"{_DIRECT_ADDR} ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIF4kAcWTOQqmpvSF3Y5LFbTe2e0adoptTESTkeymaterial00",
    f"{_DIRECT_ADDR} ecdsa-sha2-nistp256 "
    "AAAAE2VjZHNhLXNoYTItbmlzdHAyNTZlMmVBZG9wdFRlc3RLZXlNYXRlcmlhbA==",
]

_EXPECTED_SERVICE_KNOWN_HOSTS = "".join(line + "\n" for line in _CANNED_HOST_KEY_LINES)

#: A direct-access instance registered AFTER the first adoption. Quickstart
#: scenario E: `remo web push` must give it -- and only it -- the full adopt
#: treatment (keyscan + authorize) while the already-adopted instance is
#: reported `unchanged` (FR-026). Another TEST-NET-1 address, canned in
#: `adoption_ssh_mocks` exactly like the first direct instance.
_NEW_ADDR = "192.0.2.20"
_NEW_DIRECT = KnownHost(type="incus", name="newbox", host=_NEW_ADDR, user="remo")
_NEW_CANNED_HOST_KEY_LINES = [
    f"{_NEW_ADDR} ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIGpushTESTnewboxKeyMaterial4kAcWTOQqmpvSF3Y5LFbT",
]


# ---------------------------------------------------------------------------
# Live service (uvicorn subprocess with its own REMO_HOME/HOME)
# ---------------------------------------------------------------------------

_SERVER_BOOTSTRAP = """\
import sys
import uvicorn
from remo_cli.web.app import create_app
from remo_cli.web.config import WebSettings

uvicorn.run(
    create_app(WebSettings()),
    host="127.0.0.1",
    port=int(sys.argv[1]),
    log_level="warning",
)
"""


@dataclass
class LiveService:
    url: str
    port: int
    remo_home: Path  # service-side state dir (the container's ~/.config/remo)

    @property
    def identity_dir(self) -> Path:
        return self.remo_home / "web-identity"

    @property
    def registry_path(self) -> Path:
        return self.remo_home / "known_hosts"

    def mint(self) -> str:
        """Mint a fresh pairing code over HTTP (network-restricted posture)."""
        status, body = _http_json(
            "POST", f"{self.url}/api/v1/pairing/mint", headers={"Origin": self.url}
        )
        assert status == 200, (status, body)
        return str(body["code"])

    def setup_status(self) -> tuple[int, dict]:
        """GET /setup/status behind a freshly minted code (probe helper)."""
        return _http_json(
            "GET", f"{self.url}/api/v1/setup/status", token=self.mint()
        )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_json(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def service(tmp_path: Path) -> LiveService:
    """Boot a fresh, unconfigured live service; tear it down afterwards.

    Function-scoped on purpose: every test gets its own pristine state
    volume, so the tests are order-independent (fresh-boot assertions can
    never observe a previously adopted service).
    """
    remo_home = tmp_path / "service-remo-home"
    remo_home.mkdir()
    fake_home = tmp_path / "service-home"  # no ~/.ssh/id_* -> never mount_configured
    (fake_home / ".ssh").mkdir(parents=True)
    control_dir = tmp_path / "service-ssh-control"
    control_dir.mkdir()
    log_path = tmp_path / "service.log"

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    env = {k: v for k, v in os.environ.items() if not k.startswith("REMO_")}
    env.pop("XDG_CONFIG_HOME", None)
    env.update(
        {
            "HOME": str(fake_home),
            "REMO_HOME": str(remo_home),
            # Network-restricted posture: mint a pairing code over HTTP without a
            # forward-auth proxy (loud-opt-in, FR-013).
            "REMO_WEB_OPERATOR_AUTH": "none",
            "REMO_WEB_SSH_CONTROL_DIR": str(control_dir),
            # The Origin the mint helper sets must be allowed.
            "REMO_WEB_ALLOWED_ORIGINS": url,
        }
    )

    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SERVER_BOOTSTRAP, str(port)],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 30.0
        while True:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"service exited early (rc={proc.returncode}):\n{log_path.read_text()}"
                )
            try:
                status, body = _http_json("GET", f"{url}/api/v1/health", timeout=2.0)
                if status == 200 and body.get("status") == "alive":
                    break
            except OSError:
                pass
            if time.monotonic() > deadline:
                proc.terminate()
                raise RuntimeError(
                    f"service never became healthy:\n{log_path.read_text()}"
                )
            time.sleep(0.2)

        yield LiveService(url=url, port=port, remo_home=remo_home)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Workstation side (test process): temp REMO_HOME/$HOME + selective SSH mocks
# ---------------------------------------------------------------------------


@pytest.fixture
def workstation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the test process's registry/home at temp dirs with the fixture
    registry: one direct entry, one SSM entry, one unreachable direct entry."""
    ws_home = tmp_path / "workstation-home"
    (ws_home / ".ssh").mkdir(parents=True)
    ws_remo = tmp_path / "workstation-remo-home"
    ws_remo.mkdir()
    (ws_remo / "known_hosts").write_text(_EXPECTED_SERVICE_REGISTRY)

    monkeypatch.setenv("HOME", str(ws_home))
    monkeypatch.setenv("REMO_HOME", str(ws_remo))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return ws_remo


@pytest.fixture
def adoption_ssh_mocks(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Selective passthrough around the real workstation-side SSH helpers.

    Only the known direct-access instances (`192.0.2.10`, plus `192.0.2.20`
    for the push scenarios) are canned; any other hostname -- in this suite,
    exactly `unreachable.invalid` -- goes through the REAL
    `scan_and_verify_host_key`, whose keyscan genuinely fails, so the
    `skipped_unreachable` classification is exercised for real. Every scan
    target (`calls["scanned"]`) and every would-be authorized_keys install
    (`calls["authorized"]`: host name + service public key, reported as
    success) is recorded, so tests can prove which instances were -- and
    were NOT -- processed.
    """
    calls: dict = {"authorized": [], "scanned": []}
    real_scan = web_adopt.scan_and_verify_host_key
    canned_lines = {
        _DIRECT_ADDR: _CANNED_HOST_KEY_LINES,
        _NEW_ADDR: _NEW_CANNED_HOST_KEY_LINES,
    }

    def selective_scan(hostname: str, **kwargs) -> web_adopt.HostKeyScan:
        calls["scanned"].append(hostname)
        if hostname in canned_lines:
            return web_adopt.HostKeyScan(
                "trusted",
                lines=list(canned_lines[hostname]),
                detail="matches trusted known_hosts entry (canned for e2e test)",
            )
        return real_scan(hostname, **kwargs)

    def fake_authorize(host: KnownHost, public_key: str, **kwargs) -> tuple[bool, str]:
        calls["authorized"].append((host.name, public_key))
        return True, ""

    monkeypatch.setattr(web_adopt, "scan_and_verify_host_key", selective_scan)
    monkeypatch.setattr(web_adopt, "authorize_service_key", fake_authorize)
    return calls


# ---------------------------------------------------------------------------
# 1. Fresh boot (quickstart C precondition; startup identity generation)
# ---------------------------------------------------------------------------


@requires_live_web
def test_fresh_boot_is_unconfigured_with_generated_identity(service: LiveService):
    status, ready = _http_json("GET", f"{service.url}/api/v1/ready")
    assert status == 200
    assert ready["status"] == "unconfigured"

    # Startup identity generation (app lifespan): the keypair + state.json
    # exist in the service state dir before any adoption traffic.
    files = sorted(p.name for p in service.identity_dir.iterdir())
    assert files == ["id_ed25519", "id_ed25519.pub", "state.json"]
    state = json.loads((service.identity_dir / "state.json").read_text())
    assert state["deployment_id"]

    public_key = (service.identity_dir / "id_ed25519.pub").read_text().strip()
    assert public_key.endswith(f"remo-web@{state['deployment_id']}")

    status, setup = service.setup_status()
    assert status == 200
    assert setup == {
        "state": "unconfigured",
        "deployment_id": state["deployment_id"],
        "public_key_available": True,
        "registry_instances": 0,
    }
    assert not service.registry_path.exists()


# ---------------------------------------------------------------------------
# 2 + 3. Full adopt via run_adopt() over real HTTP, then idempotent re-run
# (quickstart scenarios C and D; FR-015)
# ---------------------------------------------------------------------------


@requires_live_web
def test_full_adopt_then_idempotent_rerun(
    service: LiveService,
    workstation: Path,
    adoption_ssh_mocks: dict,
):
    status, ready = _http_json("GET", f"{service.url}/api/v1/ready")
    assert (status, ready["status"]) == (200, "unconfigured")

    # ---- First adoption (scenario B) ------------------------------------
    result = web_adopt.run_adopt(service.url, service.mint(), interactive=False)

    outcomes = {o.host.name: o.outcome for o in result.outcomes}
    assert outcomes == {
        "webbox": web_adopt.OUTCOME_ADOPTED,
        "ssmbox": web_adopt.OUTCOME_SKIPPED_BY_DESIGN,
        "ghost": web_adopt.OUTCOME_SKIPPED_UNREACHABLE,
    }
    assert result.applied == {
        "applied": True,
        "registry_instances": 3,
        "host_key_instances": 1,
    }
    assert result.deployment_id

    # The service key (and only the service key) was pushed to the one
    # trusted, reachable direct instance -- SSM and unreachable entries were
    # never authorized (FR-011/FR-012/FR-013).
    assert len(adoption_ssh_mocks["authorized"]) == 1
    authorized_name, authorized_key = adoption_ssh_mocks["authorized"][0]
    assert authorized_name == "webbox"
    assert authorized_key.startswith("ssh-ed25519 ")
    assert authorized_key.endswith(f"remo-web@{result.deployment_id}")

    # Verification report present (FR-014): one service-side round-trip
    # result per registry entry (they fail -- no instance actually exists --
    # but the report itself must be structured and complete).
    verify_results = result.verify.get("results")
    assert isinstance(verify_results, list) and verify_results
    verify_names = {entry["name"] for entry in verify_results}
    assert {
        "instance incus/webbox",
        "instance aws/ssmbox",
        "instance hetzner/ghost",
    } <= verify_names
    config_lines = [e for e in verify_results if e["name"] == "configuration"]
    assert config_lines and config_lines[0]["passed"]
    assert "adopted" in config_lines[0]["detail"]

    # Service-side end state: full colon-delimited registry mirror (all 3
    # entries) + service known_hosts containing exactly the canned lines.
    assert service.registry_path.read_text() == _EXPECTED_SERVICE_REGISTRY
    service_known_hosts = service.identity_dir / "known_hosts"
    assert service_known_hosts.read_text() == _EXPECTED_SERVICE_KNOWN_HOSTS

    # Readiness flips off "unconfigured"; setup state is now "adopted".
    status, ready = _http_json("GET", f"{service.url}/api/v1/ready")
    assert (status, ready["status"]) == (200, "ready")
    status, setup = service.setup_status()
    assert status == 200
    assert setup["state"] == "adopted"
    assert setup["registry_instances"] == 3
    assert setup["deployment_id"] == result.deployment_id

    # ---- Second, identical run (idempotence / FR-015) -------------------
    registry_bytes = service.registry_path.read_bytes()
    known_hosts_bytes = service_known_hosts.read_bytes()
    state_bytes = (service.identity_dir / "state.json").read_bytes()

    rerun = web_adopt.run_adopt(service.url, service.mint(), interactive=False)

    assert {o.host.name: o.outcome for o in rerun.outcomes} == outcomes
    assert rerun.applied == result.applied
    assert rerun.deployment_id == result.deployment_id
    assert isinstance(rerun.verify.get("results"), list) and rerun.verify["results"]

    # Byte-identical service-side files; identity untouched (FR-002/FR-015).
    assert service.registry_path.read_bytes() == registry_bytes
    assert service_known_hosts.read_bytes() == known_hosts_bytes
    assert (service.identity_dir / "state.json").read_bytes() == state_bytes


# ---------------------------------------------------------------------------
# 4. FR-019 session continuity across a registry PUT (clarification Q3)
# ---------------------------------------------------------------------------


def _read_fd_fully(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(fd, 65536):
        chunks.append(chunk)
    return b"".join(chunks)


@requires_live_web
def test_registry_put_preserves_established_sessions(
    service: LiveService
):
    """A registry PUT replaces files via `os.replace` and must never disturb
    resources already held open.

    Established terminal sessions hold their own SSH processes and file
    descriptors (clarification Q3 / FR-019): the service's registry and
    known_hosts files are replaced atomically (temp file + `os.replace`), so
    an already-open fd keeps reading the ORIGINAL inode while new opens see
    the new content, and in-flight client connections to the service itself
    stay open. We prove both halves without real SSH sessions: a held fd on
    the service-side registry (standing in for an established session's held
    resources) and a live keep-alive HTTP connection spanning the PUT.
    """
    # One minted code drives every call here: the flow never calls /verify, so
    # the session stays live across both PUTs (it would end only on verify).
    code = service.mint()
    client = web_adopt.SetupApiClient(service.url, code)

    # Adopt the service (fast path: direct PUT of the same mirror payload the
    # adopt flow builds; no per-instance SSH or verify round-trips needed).
    payload = web_adopt.build_adoption_payload(
        _HOSTS, {_DIRECT.name: list(_CANNED_HOST_KEY_LINES)}
    )
    applied = client.put_registry(payload)
    assert applied["applied"] is True
    status, setup = _http_json(
        "GET", f"{service.url}/api/v1/setup/status", token=code
    )
    assert (status, setup["state"]) == (200, "adopted")

    original_bytes = service.registry_path.read_bytes()
    held_fd = os.open(service.registry_path, os.O_RDONLY)
    try:
        # A live keep-alive HTTP connection to the service, opened BEFORE the
        # PUT and reused after it on the same socket.
        conn = http.client.HTTPConnection("127.0.0.1", service.port, timeout=10)
        try:
            conn.request("GET", "/api/v1/health")
            assert conn.getresponse().read() and True
            live_socket = conn.sock
            assert live_socket is not None

            # Changed mirror: the direct entry's user changes remo -> dev.
            changed_direct = KnownHost(
                type=_DIRECT.type, name=_DIRECT.name, host=_DIRECT.host, user="dev"
            )
            changed_payload = web_adopt.build_adoption_payload(
                [changed_direct, _SSM, _UNREACHABLE],
                {_DIRECT.name: list(_CANNED_HOST_KEY_LINES)},
            )
            client.put_registry(changed_payload)

            # The path now serves the NEW content...
            new_bytes = service.registry_path.read_bytes()
            assert new_bytes != original_bytes
            assert b"incus:webbox:192.0.2.10:dev" in new_bytes
            # ...while the held fd still reads the ORIGINAL content: the PUT
            # swapped the directory entry (os.replace), never the old inode.
            assert _read_fd_fully(held_fd) == original_bytes

            # The pre-PUT connection is still usable on the same socket --
            # the PUT tore down no live connections.
            conn.request("GET", "/api/v1/health")
            follow_up = conn.getresponse()
            assert follow_up.status == 200
            follow_up.read()
            assert conn.sock is live_socket
        finally:
            conn.close()
    finally:
        os.close(held_fd)


# ---------------------------------------------------------------------------
# 5. Push after adoption (US4; quickstart scenario E) against the live service
# ---------------------------------------------------------------------------


@requires_live_web
def test_push_after_adopt_processes_only_the_new_instance(
    service: LiveService,
    workstation: Path,
    adoption_ssh_mocks: dict,
):
    """Scenario E happy path: adopt (auto-seeds the non-secret push cache),
    register a new direct-access instance workstation-side, `run_push()` with a
    fresh code -- only the new instance gets keyscan+authorize; the original is
    `unchanged` from the delta cache but still contributes its cached host-key
    lines to the full mirror PUT."""
    # ---- Adopt (auto-seeds the deployment-keyed push cache, no secret) ---
    result = web_adopt.run_adopt(service.url, service.mint(), interactive=False)
    assert {o.host.name: o.outcome for o in result.outcomes} == {
        "webbox": web_adopt.OUTCOME_ADOPTED,
        "ssmbox": web_adopt.OUTCOME_SKIPPED_BY_DESIGN,
        "ghost": web_adopt.OUTCOME_SKIPPED_UNREACHABLE,
    }

    cache_path = workstation / "web-service.json"
    assert cache_path.stat().st_mode & 0o777 == 0o600
    saved = json.loads(cache_path.read_text())
    # No secret is persisted (FR-019): no url, no token/code, no top-level id.
    assert set(saved) == {"push_cache"}
    dep = result.deployment_id
    # Delta cache is deployment-keyed and seeded with the adopted instance.
    assert set(saved["push_cache"]) == {dep}
    assert set(saved["push_cache"][dep]) == {"webbox"}
    assert saved["push_cache"][dep]["webbox"]["host_keys"] == _CANNED_HOST_KEY_LINES

    adoption_ssh_mocks["scanned"].clear()
    adoption_ssh_mocks["authorized"].clear()

    # ---- A NEW direct-access instance is registered workstation-side ----
    registry = workstation / "known_hosts"
    registry.write_text(registry.read_text() + _NEW_DIRECT.to_line() + "\n")

    # ---- Push with a freshly minted code (FR-018/FR-019) -----------------
    push = web_adopt.run_push(service.url, service.mint(), interactive=False)

    assert {o.host.name: o.outcome for o in push.outcomes} == {
        "webbox": web_adopt.OUTCOME_UNCHANGED,
        "ssmbox": web_adopt.OUTCOME_SKIPPED_BY_DESIGN,
        "ghost": web_adopt.OUTCOME_SKIPPED_UNREACHABLE,
        "newbox": web_adopt.OUTCOME_ADOPTED,
    }
    assert push.deployment_id == result.deployment_id
    assert push.applied == {
        "applied": True,
        "registry_instances": 4,
        "host_key_instances": 2,
    }

    # Call recording proves the delta: the original instance was never
    # re-scanned, the new one was scanned exactly once (ghost still goes
    # through its real, failing keyscan), and the ONLY authorized_keys
    # install targeted the new instance -- with the service's EXISTING
    # identity (same deployment_id as adoption).
    assert _DIRECT_ADDR not in adoption_ssh_mocks["scanned"]
    assert adoption_ssh_mocks["scanned"].count(_NEW_ADDR) == 1
    assert _UNREACHABLE_ADDR in adoption_ssh_mocks["scanned"]
    assert [name for name, _ in adoption_ssh_mocks["authorized"]] == ["newbox"]
    authorized_key = adoption_ssh_mocks["authorized"][0][1]
    assert authorized_key.endswith(f"remo-web@{result.deployment_id}")

    # Service-side end state: the mirror gained exactly the new entry, and
    # the service known_hosts holds the cached webbox lines (reused from the
    # delta cache) plus the new instance's freshly scanned lines, in
    # registry order.
    assert (
        service.registry_path.read_text()
        == _EXPECTED_SERVICE_REGISTRY + _NEW_DIRECT.to_line() + "\n"
    )
    assert (
        service.identity_dir / "known_hosts"
    ).read_text() == _EXPECTED_SERVICE_KNOWN_HOSTS + "".join(
        line + "\n" for line in _NEW_CANNED_HOST_KEY_LINES
    )

    status, setup = service.setup_status()
    assert status == 200
    assert setup["state"] == "adopted"
    assert setup["registry_instances"] == 4

    # The delta cache was rewritten after the successful PUT: both adopted
    # instances now present under the same deployment key, so the NEXT push
    # would skip newbox too.
    resaved = json.loads(cache_path.read_text())
    assert set(resaved["push_cache"][dep]) == {"webbox", "newbox"}
    assert resaved["push_cache"][dep]["newbox"]["host_keys"] == _NEW_CANNED_HOST_KEY_LINES
    assert resaved["push_cache"][dep]["webbox"] == saved["push_cache"][dep]["webbox"]


@requires_live_web
def test_push_with_dormant_code_fails_with_reopen_guidance(
    service: LiveService,
    workstation: Path,
    adoption_ssh_mocks: dict,
):
    """A stale/dormant code (never minted, or already used) makes every setup
    call return the dormant 404, which the CLI maps to reopen-the-page guidance
    -- before any per-instance work or registry PUT."""
    # Adopt once so there is a registry to (not) disturb.
    web_adopt.run_adopt(service.url, service.mint(), interactive=False)
    adoption_ssh_mocks["scanned"].clear()
    adoption_ssh_mocks["authorized"].clear()
    registry_before = service.registry_path.read_bytes()

    # A code that was never minted -> the surface is dormant for it.
    with pytest.raises(web_adopt.SetupNotFoundError) as excinfo:
        web_adopt.run_push(service.url, "never-minted-code", interactive=False)

    message = str(excinfo.value)
    assert "dormant" in message
    assert "fresh code" in message  # reopen-the-page remediation

    # Failed at the first setup call: no instance touched, no PUT.
    assert adoption_ssh_mocks["scanned"] == []
    assert adoption_ssh_mocks["authorized"] == []
    assert service.registry_path.read_bytes() == registry_before
