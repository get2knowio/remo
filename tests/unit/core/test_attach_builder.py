"""Attach-argv builder tests (specs/025-ssm-connector).

T002 pins `remo_cli.web.terminal.build_attach_argv`'s exact argv BEFORE it is
refactored into a thin wrapper over `core.attach.build_attach_argv`
(Constitution VI: characterize, then move). T005/T013 then prove the core
builder, the web wrapper, and the connector launcher all agree.
"""

from __future__ import annotations

import subprocess
import sys

from remo_cli.models.host import KnownHost


def _ssh_host(*, port: int = 2222) -> KnownHost:
    return KnownHost(
        type="ssh",
        name="lab",
        host="host",
        user="user",
        instance_id=str(port),
        access_mode="direct",
        region="",
    )


class _StubSettings:
    """Minimal stand-in for `WebSettings` exposing only what
    `build_attach_argv` reads (`ssh_identity_for`/`ssh_known_hosts_file`)."""

    def ssh_identity_for(self, host: KnownHost) -> str | None:  # noqa: ARG002
        return "/k"

    @property
    def ssh_known_hosts_file(self) -> str | None:
        return "/kh"


def test_web_build_attach_argv_characterization(monkeypatch) -> None:
    """Pin the web attach argv byte-for-byte before the core.attach refactor."""
    from remo_cli.web.terminal import build_attach_argv

    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: None)

    host = _ssh_host(port=2222)
    argv = build_attach_argv(
        host,
        "demo-project",
        control_dir="/run/x",
        settings=_StubSettings(),
    )

    assert argv == [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ControlMaster=auto",
        "-o", "ControlPath=/run/x/remo-%r@%h-%p",
        "-o", "ControlPersist=60s",
        "-o", "Port=2222",
        "-o", "IdentityFile=/k",
        "-o", "IdentitiesOnly=yes",
        "-o", "UserKnownHostsFile=/kh",
        "-tt",
        "user@host",
        'PATH="$HOME/.local/bin:$PATH" remo-host sessions attach --project demo-project',
    ]


# ---------------------------------------------------------------------------
# T005: core.attach parity with the web wrapper, and web-independence.
# ---------------------------------------------------------------------------


def test_web_wrapper_equals_core_builder(monkeypatch) -> None:
    from remo_cli.core.attach import build_attach_argv as core_build_attach_argv
    from remo_cli.web.terminal import build_attach_argv as web_build_attach_argv

    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: None)

    host = _ssh_host(port=2222)
    web_argv = web_build_attach_argv(
        host, "demo-project", control_dir="/run/x", settings=_StubSettings()
    )
    core_argv = core_build_attach_argv(
        host,
        "demo-project",
        control_dir="/run/x",
        identity_file="/k",
        known_hosts_file="/kh",
        use_registry_identity=False,
    )
    assert web_argv == core_argv


def test_core_builder_rejects_invalid_project() -> None:
    from remo_cli.core.attach import build_attach_argv as core_build_attach_argv

    host = _ssh_host()
    try:
        core_build_attach_argv(host, "../etc")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an invalid project name")


def test_core_builder_has_no_web_import() -> None:
    """`core.attach` must import without pulling in `remo_cli.web.*`
    (FR-010/SC-002): the connector must work without the web extra."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import remo_cli.core.attach, sys; "
            "assert not [m for m in sys.modules if m.startswith('remo_cli.web')]",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# T013: connector launcher argv == web wrapper argv for the same paths (SC-002).
# ---------------------------------------------------------------------------


def test_connector_argv_matches_web_wrapper_for_same_paths(monkeypatch, tmp_path) -> None:
    import pytest

    from remo_cli.core.connector import connector_attach_argv

    monkeypatch.setattr("remo_cli.core.ssh.detect_timezone", lambda: None)

    host = _ssh_host(port=2222)
    state = tmp_path
    connector_argv = connector_attach_argv(host, "demo-project", state)

    fastapi = pytest.importorskip("fastapi")
    del fastapi
    from remo_cli.web.terminal import build_attach_argv as web_build_attach_argv

    class _StateSettings:
        def ssh_identity_for(self, host: KnownHost) -> str | None:  # noqa: ARG002
            return str(state / "id_ed25519")

        @property
        def ssh_known_hosts_file(self) -> str | None:
            return str(state / "known_hosts")

    web_argv = web_build_attach_argv(
        host,
        "demo-project",
        control_dir=str(state / "ssh"),
        settings=_StateSettings(),
    )
    assert connector_argv == web_argv
