"""Readiness + `remo web check` across the four configuration states (T034).

Covers 011-web-adopt US2 / research R11 semantics:

- ``unconfigured`` -> ``GET /api/v1/ready`` 200 with ``"status":
  "unconfigured"`` (healthy-awaiting-adoption, SC-006 no-crash-loop) and
  `remo web check` PASS with an "awaiting adoption -- run `remo web adopt`"
  detail (the Docker entrypoint's startup gate, T031).
- ``adopted`` / ``mount_configured`` -> 200 ``"ready"``; check reports the
  mode. The adopted case also proves the service keypair under
  ``web-identity/`` satisfies the SSH-identity probe (T028).
- ``broken`` -> today's 503 with actionable detail; check FAILs with
  remediation.

Plus the startup path (T030): the app lifespan generates the service
identity exactly once when unconfigured, reuses an existing keypair on
restart, and never writes when the state volume is mounted read-only.

Uses the `state_dir` StateDirFactory from tests/unit/web/conftest.py.
"""

from __future__ import annotations

import os
import shutil

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from remo_cli.cli.main import cli
from remo_cli.web import app as app_module
from remo_cli.web import check as check_module
from remo_cli.web import health
from remo_cli.web.check import all_passed, format_results, run_checks

_ORIGIN = "http://testserver"

skip_if_root = pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits")
skip_without_ssh_keygen = pytest.mark.skipif(
    shutil.which("ssh-keygen") is None, reason="ssh-keygen not available"
)


class _NoopDiscovery:
    """Stops the app lifespan's initial discovery from opening real SSH."""

    async def refresh(self, instance_id: str | None = None, *, force: bool = True) -> None:
        return None


def _app_and_client(state_dir) -> TestClient:
    settings = state_dir.settings(allowed_hosts=["testserver", "localhost", "127.0.0.1"])
    application = app_module.create_app(settings)
    application.state.discovery_service = _NoopDiscovery()
    return TestClient(application, base_url=_ORIGIN)


def _patch_ssh_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic executables: `ssh` present, aws/ssm absent."""
    monkeypatch.setattr(
        health.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ssh" else None
    )


def _result(results, name):
    return next(r for r in results if r.name == name)


# ---------------------------------------------------------------------------
# GET /api/v1/ready per state (T028, research R11)
# ---------------------------------------------------------------------------


class TestReadyUnconfigured:
    def test_ready_returns_200_unconfigured_with_adopt_hint(self, state_dir, monkeypatch):
        state_dir.unconfigured()
        _patch_ssh_on_path(monkeypatch)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "unconfigured"
        assert "remo web adopt" in body["detail"]
        assert "checks" in body

    def test_missing_runtime_prerequisite_is_not_ready_not_unconfigured(
        self, state_dir, monkeypatch
    ):
        # US2 scenario 5: missing runtime prerequisites (here: no `ssh`
        # executable) are "broken", distinguishable from "unconfigured".
        state_dir.unconfigured()
        monkeypatch.setattr(health.shutil, "which", lambda name: None)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert "ssh" in body["detail"]


class TestReadyConfigured:
    def test_adopted_is_ready_via_service_identity(self, state_dir, monkeypatch):
        # No user identity anywhere: only the web-identity/ service keypair
        # can satisfy the SSH-identity probe (the T028 candidate addition).
        state_dir.adopted()
        _patch_ssh_on_path(monkeypatch)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["ssh_identity"] == "ok"

    def test_mount_configured_is_ready(self, state_dir, monkeypatch):
        state_dir.mount_configured_user_identity()
        _patch_ssh_on_path(monkeypatch)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 200
        assert response.json()["status"] == "ready"


class TestReadyBroken:
    @skip_if_root
    def test_unreadable_registry_is_503(self, state_dir, monkeypatch):
        state_dir.broken_unreadable_registry()
        _patch_ssh_on_path(monkeypatch)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert body["detail"]

    def test_half_pair_is_503_even_when_probes_pass(self, state_dir, monkeypatch):
        # A half-generated service keypair is `broken` (unusable identity)
        # even though every individual readiness probe can pass -- the state
        # gate, not just the probes, decides (research R11).
        state_dir.write_registry()
        state_dir.broken_half_pair(keep="private")
        _patch_ssh_on_path(monkeypatch)

        response = _app_and_client(state_dir).get("/api/v1/ready")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "not_ready"
        assert "state volume" in body["detail"]


# ---------------------------------------------------------------------------
# `remo web check` per state (T029)
# ---------------------------------------------------------------------------


class TestCheckUnconfigured:
    def test_unconfigured_passes_with_awaiting_adoption_detail(self, state_dir, monkeypatch):
        state_dir.unconfigured()
        monkeypatch.setattr(
            check_module.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "ssh" else None,
        )

        results = run_checks(state_dir.settings())

        assert all_passed(results) is True
        configuration = _result(results, "configuration")
        assert "awaiting adoption" in configuration.detail
        assert "remo web adopt" in configuration.detail

        # Registry/identity absence must NOT fail (or even appear) in this
        # state, and there is no registry to run instance checks against.
        names = {r.name for r in results}
        assert "registry" not in names
        assert "ssh_identity" not in names
        assert not any(name.startswith("instance ") for name in names)

    def test_cli_startup_gate_exits_zero(self, state_dir, monkeypatch):
        # The Docker entrypoint's gate (T031): `remo web check
        # --skip-instance-checks` must pass on a fresh writable state volume.
        state_dir.unconfigured()
        monkeypatch.setenv("REMO_WEB_SSH_CONTROL_DIR", str(state_dir.user_home / "ssh-ctrl"))
        monkeypatch.setattr(
            check_module.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "ssh" else None,
        )

        result = CliRunner().invoke(cli, ["web", "check", "--skip-instance-checks"])

        assert result.exit_code == 0, result.output
        assert "[PASS] configuration:" in result.output
        assert "awaiting adoption" in result.output


class TestCheckConfiguredModes:
    def test_adopted_reports_mode_and_passes(self, state_dir, monkeypatch):
        state_dir.adopted()
        monkeypatch.setattr(
            check_module.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "ssh" else None,
        )

        results = run_checks(state_dir.settings(), include_instances=False)

        assert all_passed(results) is True
        assert "adopted" in _result(results, "configuration").detail
        assert _result(results, "ssh_identity").passed is True  # service keypair
        assert _result(results, "registry").passed is True

    def test_mount_configured_reports_mode_and_passes(self, state_dir, monkeypatch):
        state_dir.mount_configured_user_identity()
        monkeypatch.setattr(
            check_module.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "ssh" else None,
        )

        results = run_checks(state_dir.settings(), include_instances=False)

        assert all_passed(results) is True
        assert "mount_configured" in _result(results, "configuration").detail


class TestCheckBroken:
    @skip_if_root
    def test_broken_fails_with_remediation(self, state_dir, monkeypatch):
        state_dir.broken_unreadable_registry()
        monkeypatch.setattr(
            check_module.shutil,
            "which",
            lambda name: f"/usr/bin/{name}" if name == "ssh" else None,
        )

        results = run_checks(state_dir.settings(), include_instances=False)

        assert all_passed(results) is False
        configuration = _result(results, "configuration")
        assert configuration.passed is False
        assert configuration.remediation is not None

        report = format_results(results)
        assert "[FAIL] configuration:" in report


# ---------------------------------------------------------------------------
# Startup service-identity generation (T030, FR-002)
# ---------------------------------------------------------------------------


class TestStartupIdentityGeneration:
    @skip_without_ssh_keygen
    def test_lifespan_generates_identity_when_unconfigured(self, state_dir):
        state_dir.unconfigured()
        assert not state_dir.private_key_path.exists()

        with _app_and_client(state_dir):
            pass

        assert state_dir.private_key_path.is_file()
        assert state_dir.public_key_path.is_file()
        assert state_dir.state_json_path.is_file()
        assert "remo-web@" in state_dir.public_key_path.read_text()

    def test_lifespan_reuses_existing_keypair(self, state_dir):
        # "Restart" of an unconfigured-but-generated service: the fixture's
        # fake key content surviving the lifespan proves ssh-keygen was never
        # invoked (FR-002: reuse, never regenerate).
        state_dir.write_keypair()
        state_dir.write_state_json()
        before = state_dir.private_key_path.read_bytes()

        with _app_and_client(state_dir):
            pass

        assert state_dir.private_key_path.read_bytes() == before

    @skip_if_root
    def test_lifespan_skips_generation_on_readonly_mount(self, state_dir):
        # mount_configured (read-only REMO_HOME): startup must not attempt to
        # write -- and must not crash (FR-005 regression safety).
        state_dir.mount_configured_readonly()
        state_dir.add_user_identity()

        with _app_and_client(state_dir):
            pass

        assert not state_dir.web_identity_dir.exists()

    def test_lifespan_never_completes_a_broken_half_pair(self, state_dir):
        state_dir.broken_half_pair(keep="private")

        with _app_and_client(state_dir):
            pass

        assert not state_dir.public_key_path.exists()
