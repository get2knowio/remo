"""Jinja2 parse check for every Ansible template in the repo.

Catches syntax errors before they hit a real Ansible run — most notably the
``${#var}`` bash array-length idiom, which Jinja2 reads as a ``{#`` comment
opener and consumes the rest of the file looking for ``#}``. That class of
bug otherwise only surfaces on a live host during a smoke test.

We use ``Environment.parse`` rather than ``render`` so the test doesn't need
to know what variables each template expects.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "ansible"


def _all_templates() -> list[Path]:
    return sorted(
        path
        for path in TEMPLATE_ROOT.rglob("*")
        if path.is_file() and "templates" in path.parts
    )


def _render_template(relative_path: str, **context: str) -> str:
    env = Environment(
        autoescape=False,
        loader=FileSystemLoader(str(TEMPLATE_ROOT)),
    )
    return env.get_template(relative_path).render(**context)


@pytest.mark.parametrize("template_path", _all_templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_template_parses(template_path: Path) -> None:
    env = Environment(autoescape=False)
    source = template_path.read_text()
    try:
        env.parse(source)
    except TemplateSyntaxError as exc:
        pytest.fail(
            f"{template_path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.message}"
        )


def test_project_menu_template_mentions_managed_vault() -> None:
    rendered = _render_template(
        "roles/user_setup/templates/project-menu.sh.j2",
        dev_workspace_dir="/home/remo/projects",
        remo_version="1.2.3",
    )

    assert 'MANAGED_VAULT_PROJECT="_remo-vault"' in rendered
    assert "cannot be deleted from project-menu" in rendered


def test_project_launch_template_allows_managed_vault_name() -> None:
    rendered = _render_template(
        "roles/user_setup/templates/project-launch.sh.j2",
        dev_workspace_dir="/home/remo/projects",
    )

    assert 'MANAGED_VAULT_PROJECT="_remo-vault"' in rendered
    assert '[[ "$PROJECT" != "$MANAGED_VAULT_PROJECT" ]]' in rendered


def test_remo_broker_service_template_uses_bootstrap_credential() -> None:
    rendered = _render_template(
        "roles/remo_broker/templates/remo-broker.service.j2",
        remo_broker_binary_path="/usr/bin/remo-broker",
        remo_broker_user="remo-broker",
        remo_broker_group="remo-broker",
        remo_broker_socket_dir="/run/remo-broker",
        remo_broker_log_dir="/var/log/remo-broker",
        remo_broker_bootstrap_token_path="/etc/remo-broker/bootstrap-token",
    )

    assert "LoadCredential=bootstrap-token:/etc/remo-broker/bootstrap-token" in rendered
    assert "ExecStart=/usr/bin/remo-broker --bootstrap-token-path %d/bootstrap-token" in rendered


def test_vault_devcontainer_mounts_admin_socket_and_manifest() -> None:
    rendered = _render_template(
        "roles/vault_devcontainer/templates/devcontainer.json.j2",
        dev_workspace_dir="/home/remo/projects",
    )

    assert "/run/remo-broker/admin.sock" in rendered
    assert "/workspace/.remo/manifest.toml" in rendered


def test_vault_status_helper_checks_protocol_v2() -> None:
    rendered = _render_template("roles/vault_devcontainer/templates/remo-vend-status.sh.j2")

    assert 'json.dumps({"op": "status"})' in rendered
    assert "expected 2" in rendered


def test_manifest_schema_and_feature_templates_render() -> None:
    schema = _render_template("roles/remo_secrets_feature/templates/manifest.schema.toml")
    feature = _render_template(
        "roles/remo_secrets_feature/templates/feature-devcontainer.json.j2"
    )

    assert 'fetch_as = ["env", "file"]' in schema
    assert "REMO_BROKER_PROJECT_SOCKET" in feature
    assert "readonly" in feature
    assert "REMO_SECRETS_ENV_FILE" in feature


def test_vault_helper_templates_render_expected_commands() -> None:
    motd = _render_template("roles/vault_devcontainer/templates/motd.j2")
    test_project = _render_template(
        "roles/vault_devcontainer/templates/remo-test-project.sh.j2"
    )

    assert "remo-list-creds" in motd
    assert "remo-vend-status" in motd
    assert 'remo-reload "$PROJECT"' in test_project
    assert 'REMO_PROJECT="$PROJECT" remo-fetch-secrets "$PROJECT"' in test_project
    assert "Check broker audit log" in test_project


def _start_fake_broker(
    socket_path: Path,
    responder,
) -> tuple[threading.Event, threading.Thread]:
    stop_event = threading.Event()

    def serve() -> None:
        if socket_path.exists():
            socket_path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(socket_path))
            server.listen()
            server.settimeout(0.1)

            while not stop_event.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    continue
                with conn:
                    payload = b""
                    while not payload.endswith(b"\n"):
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        payload += chunk
                    if not payload:
                        continue
                    request = json.loads(payload.decode().strip())
                    response = responder(request)
                    conn.sendall(json.dumps(response).encode() + b"\n")

        if socket_path.exists():
            socket_path.unlink()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return stop_event, thread


def test_fetch_secrets_renders_env_and_tmpfs_backed_files(tmp_path: Path) -> None:
    script = _render_template("roles/remo_secrets_feature/templates/remo-fetch-secrets.sh.j2")
    script_path = tmp_path / "remo-fetch-secrets.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        '''
schema_version = 1
project = "demo"

[secrets.gh]
fetch_as = "env"
env_var = "GH_TOKEN"

[secrets.aws]
fetch_as = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template = """
[default]
aws_access_key_id={{aws_access_key_id}}
aws_secret_access_key={{aws_secret_access_key}}
"""
'''.strip()
    )

    socket_path = tmp_path / "broker.sock"
    env_file = tmp_path / "demo.env"
    tmp_root = tmp_path / "tmpfs"

    def responder(request: dict[str, object]) -> dict[str, object]:
        if request["op"] == "ping":
            return {"ok": True, "protocol_version": 2}
        name = request["name"]
        if name == "gh":
            return {"ok": True, "value": "ghp_test"}
        if name == "aws":
            return {
                "ok": True,
                "value": json.dumps(
                    {
                        "aws_access_key_id": "AKIA123",
                        "aws_secret_access_key": "secret456",
                    }
                ),
            }
        return {"ok": False, "code": "unknown"}

    stop_event, thread = _start_fake_broker(socket_path, responder)
    try:
        for _ in range(20):
            if socket_path.exists():
                break
            time.sleep(0.05)
        result = subprocess.run(
            ["bash", str(script_path), "demo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={
                **os.environ,
                "HOME": str(home),
                "REMO_MANIFEST_PATH": str(manifest),
                "REMO_BROKER_PROJECT_SOCKET": str(socket_path),
                "REMO_SECRETS_ENV_FILE": str(env_file),
                "REMO_SECRETS_TMP_ROOT": str(tmp_root),
            },
        )
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f'source "{env_file}"'
    env_contents = env_file.read_text()
    assert "export GH_TOKEN=ghp_test" in env_contents

    rendered_path = home / ".aws" / "credentials"
    assert rendered_path.is_symlink()
    resolved = rendered_path.resolve()
    assert str(resolved).startswith(str(tmp_root))
    assert resolved.read_text() == (
        "[default]\naws_access_key_id=AKIA123\naws_secret_access_key=secret456\n"
    )
    assert oct(resolved.stat().st_mode & 0o777) == "0o600"
    assert f"export REMO_SECRET_FILE_AWS={rendered_path}" in env_contents


def test_fetch_secrets_fails_closed_when_secret_stays_unavailable(tmp_path: Path) -> None:
    script = _render_template("roles/remo_secrets_feature/templates/remo-fetch-secrets.sh.j2")
    script_path = tmp_path / "remo-fetch-secrets.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """
schema_version = 1
project = "demo"

[secrets.missing_demo]
fetch_as = "env"
env_var = "MISSING_DEMO"
""".strip()
    )

    socket_path = tmp_path / "broker.sock"

    def responder(request: dict[str, object]) -> dict[str, object]:
        if request["op"] == "ping":
            return {"ok": True, "protocol_version": 2}
        return {"ok": False, "code": "missing_secret"}

    stop_event, thread = _start_fake_broker(socket_path, responder)
    try:
        for _ in range(20):
            if socket_path.exists():
                break
            time.sleep(0.05)
        result = subprocess.run(
            ["bash", str(script_path), "demo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={
                **os.environ,
                "HOME": str(tmp_path / "home"),
                "REMO_MANIFEST_PATH": str(manifest),
                "REMO_BROKER_PROJECT_SOCKET": str(socket_path),
                "REMO_FETCH_SECRETS_TIMEOUT_SECONDS": "1",
            },
        )
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert result.returncode == 1
    assert "stayed unavailable for 1 seconds" in result.stderr


def test_fetch_secrets_fails_on_protocol_mismatch(tmp_path: Path) -> None:
    script = _render_template("roles/remo_secrets_feature/templates/remo-fetch-secrets.sh.j2")
    script_path = tmp_path / "remo-fetch-secrets.sh"
    script_path.write_text(script)
    script_path.chmod(0o755)

    manifest = tmp_path / "manifest.toml"
    manifest.write_text(
        """
schema_version = 1
project = "demo"

[secrets.demo_token]
fetch_as = "env"
env_var = "DEMO_TOKEN"
""".strip()
    )

    socket_path = tmp_path / "broker.sock"

    def responder(_: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "protocol_version": 1}

    stop_event, thread = _start_fake_broker(socket_path, responder)
    try:
        for _ in range(20):
            if socket_path.exists():
                break
            time.sleep(0.05)
        result = subprocess.run(
            ["bash", str(script_path), "demo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={
                **os.environ,
                "HOME": str(tmp_path / "home"),
                "REMO_MANIFEST_PATH": str(manifest),
                "REMO_BROKER_PROJECT_SOCKET": str(socket_path),
            },
        )
    finally:
        stop_event.set()
        thread.join(timeout=2)

    assert result.returncode == 1
    assert "incompatible broker protocol 1, expected 2" in result.stderr
