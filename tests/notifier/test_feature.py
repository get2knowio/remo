"""Smoke test for the remo-notifier-source devcontainer Feature (spec 009 T021).

Covers: valid JSON manifest; shellcheck (skipped if unavailable); the connector's
fail-fast preflight names missing options; a dry-run builds the expected
SourceRegistration JSON and POST target from the resolved options.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _feature_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "features" / "remo-notifier-source").is_dir():
            return parent / "features" / "remo-notifier-source"
    raise RuntimeError("feature dir not found")


FEATURE = _feature_dir()
CONNECTOR = FEATURE / "scripts" / "remo-source-connect.sh"
INSTALL = FEATURE / "install.sh"


def _run(env: dict, *, expect_ok: bool | None = None) -> subprocess.CompletedProcess:
    base = {"REMO_SOURCE_ENV_FILE": "/dev/null", "PATH": "/usr/bin:/bin"}
    base.update(env)
    proc = subprocess.run(
        ["sh", str(CONNECTOR)], env=base, text=True, capture_output=True
    )
    if expect_ok is True:
        assert proc.returncode == 0, proc.stderr
    elif expect_ok is False:
        assert proc.returncode != 0, proc.stdout
    return proc


def test_devcontainer_feature_json_is_valid() -> None:
    data = json.loads((FEATURE / "devcontainer-feature.json").read_text())
    assert data["id"] == "remo-notifier-source"
    assert data["entrypoint"].endswith("remo-source-connect.sh")
    assert set(data["options"]) >= {
        "notifierAddress", "agentshApiUrl", "apiKey", "apiKeyFile", "sourceId", "labels"
    }


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_passes() -> None:
    for script in (INSTALL, CONNECTOR):
        proc = subprocess.run(
            ["shellcheck", "-s", "sh", str(script)], text=True, capture_output=True
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_preflight_names_missing_key() -> None:
    # notifierAddress defaults and agentshApiUrl derives by convention, so with an
    # empty env only the approver key is unresolved — and it fails fail-closed.
    proc = _run({"REMO_SOURCE_DEFAULT_API_KEY_FILE": "/nonexistent/key"}, expect_ok=False)
    missing = next(ln for ln in proc.stderr.splitlines() if "missing required option" in ln)
    assert "apiKey" in missing
    # Derived/defaulted values must not appear in the missing list.
    assert "notifierAddress" not in missing
    assert "agentshApiUrl" not in missing


def test_dry_run_derives_api_url_from_source_id() -> None:
    proc = _run(
        {
            "REMO_SOURCE_API_KEY": "k",
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    # notifierAddress defaults; api_url derives from sourceId + default port 8080.
    assert "POST http://172.17.0.1:18181/v1/sources" in proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[1])
    assert payload["api_url"] == "http://proj-a:8080"


def test_dry_run_derives_api_url_with_custom_port() -> None:
    proc = _run(
        {
            "REMO_SOURCE_API_KEY": "k",
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_AGENTSH_PORT": "9000",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[1])
    assert payload["api_url"] == "http://proj-a:9000"


def test_dry_run_uses_conventional_key_file(tmp_path: Path) -> None:
    key_file = tmp_path / "agentsh_approver_key"
    key_file.write_text("conv-key\n")
    proc = _run(
        {
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_DEFAULT_API_KEY_FILE": str(key_file),
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[1])
    assert payload["api_key"] == "conv-key"  # picked up from the conventional path
    assert payload["api_url"] == "http://proj-a:8080"


def test_preflight_passes_with_inline_key() -> None:
    proc = _run(
        {
            "REMO_SOURCE_NOTIFIER_ADDRESS": "172.17.0.1:18181",
            "REMO_SOURCE_AGENTSH_API_URL": "http://proj-a:8080",
            "REMO_SOURCE_API_KEY": "k",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    assert "POST http://172.17.0.1:18181/v1/sources" in proc.stdout


def test_dry_run_builds_expected_registration() -> None:
    proc = _run(
        {
            "REMO_SOURCE_NOTIFIER_ADDRESS": "172.17.0.1:18181",
            "REMO_SOURCE_AGENTSH_API_URL": "http://proj-a:8080",
            "REMO_SOURCE_API_KEY": "secret-key",
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_LABELS": "project=proj-a,owner=paul",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == "POST http://172.17.0.1:18181/v1/sources"
    payload = json.loads(lines[1])
    assert payload == {
        "source_id": "proj-a",
        "api_url": "http://proj-a:8080",
        "api_key": "secret-key",
        "labels": {"project": "proj-a", "owner": "paul"},
    }


def test_dry_run_reads_key_from_file(tmp_path: Path) -> None:
    key_file = tmp_path / "key"
    key_file.write_text("file-key\n")
    proc = _run(
        {
            "REMO_SOURCE_NOTIFIER_ADDRESS": "172.17.0.1:18181",
            "REMO_SOURCE_AGENTSH_API_URL": "http://proj-a:8080",
            "REMO_SOURCE_API_KEY_FILE": str(key_file),
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[1])
    assert payload["api_key"] == "file-key"  # trailing newline stripped


def test_dry_run_empty_labels_is_empty_object() -> None:
    proc = _run(
        {
            "REMO_SOURCE_NOTIFIER_ADDRESS": "172.17.0.1:18181",
            "REMO_SOURCE_AGENTSH_API_URL": "http://proj-a:8080",
            "REMO_SOURCE_API_KEY": "k",
            "REMO_SOURCE_ID": "proj-a",
            "REMO_SOURCE_DRY_RUN": "1",
        },
        expect_ok=True,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[1])
    assert payload["labels"] == {}
