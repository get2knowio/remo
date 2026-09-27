"""US1/US2: the connector launcher (specs/025-ssm-connector).

T012 proves the happy path execs the exact parity argv; T018/T019 prove
every refusal path prints one error line, returns 1, and never execs.
"""

from __future__ import annotations

import json
import os
import re

import pytest

from remo_cli.core.connector import encode_target
from remo_cli.models.host import KnownHost
from remo_cli.providers import connector as connector_provider

_ERROR_LINE_RE = re.compile(r"^remo-connector-error: (\S+) (.+)$")


def _write_state(
    state_dir,
    *,
    exposures: list[dict] | None = None,
    registry_hosts: list[dict] | None = None,
    write_registry: bool = True,
    write_exposure: bool = True,
    write_key: bool = True,
    write_known_hosts: bool = True,
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "ssh").mkdir(exist_ok=True)

    if write_registry:
        hosts = (
            registry_hosts
            if registry_hosts is not None
            else [
                {
                    "type": "ssh",
                    "name": "lab",
                    "host": "10.0.0.5",
                    "user": "remo",
                    "access": "direct",
                    "ssh": {"port": 2222, "identity_file": str(state_dir / "id_ed25519")},
                }
            ]
        )
        (state_dir / "registry.json").write_text(
            json.dumps({"version": 2, "hosts": hosts}, indent=2) + "\n"
        )

    if write_exposure:
        exp = (
            exposures
            if exposures is not None
            else [{"host": "lab", "projects": ["remo"]}]
        )
        (state_dir / "exposure.json").write_text(
            json.dumps({"version": 1, "exposures": exp}, indent=2) + "\n"
        )

    if write_key:
        (state_dir / "id_ed25519").write_text("fake-private-key\n")
        (state_dir / "id_ed25519.pub").write_text("fake-public-key\n")

    if write_known_hosts:
        (state_dir / "known_hosts").write_text("")


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("REMO_CONNECTOR_STATE_DIR", str(state))
    old_remo_home = os.environ.get("REMO_HOME")
    yield state
    if old_remo_home is None:
        os.environ.pop("REMO_HOME", None)
    else:
        os.environ["REMO_HOME"] = old_remo_home


@pytest.fixture
def captured_execvp(monkeypatch):
    captured: dict[str, list[str]] = {}

    def _fake_execvp(file: str, args: list[str]) -> None:
        captured["file"] = file
        captured["args"] = list(args)
        raise SystemExit(0)  # never actually replace the test process

    monkeypatch.setattr(connector_provider.os, "execvp", _fake_execvp)
    return captured


@pytest.fixture(autouse=True)
def _default_run_as_and_ssh(monkeypatch):
    monkeypatch.setattr(connector_provider.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(connector_provider, "_effective_user", lambda: "remo-connector")
    monkeypatch.setattr(connector_provider.shutil, "which", lambda name: "/usr/bin/ssh")


def test_attach_execs_parity_argv(state_dir, captured_execvp) -> None:
    _write_state(state_dir)

    with pytest.raises(SystemExit):
        connector_provider.attach(encode_target("lab", "remo"))

    from remo_cli.core.connector import connector_attach_argv

    host = KnownHost(
        type="ssh",
        name="lab",
        host="10.0.0.5",
        user="remo",
        instance_id="2222",
        access_mode="direct",
        region=str(state_dir / "id_ed25519"),
    )
    expected = connector_attach_argv(host, "remo", state_dir)
    assert captured_execvp["args"] == expected
    assert captured_execvp["file"] == expected[0]
    assert os.environ.get("REMO_HOME") == str(state_dir)


# ---------------------------------------------------------------------------
# US2 (T018/T019/T020): every refusal path -> one error line, rc 1, no exec.
# ---------------------------------------------------------------------------


@pytest.fixture
def forbid_execvp(monkeypatch):
    def _boom(*_args, **_kwargs):  # pragma: no cover - only invoked on a bug
        raise AssertionError("os.execvp must not be called on a refusal path")

    monkeypatch.setattr(connector_provider.os, "execvp", _boom)


def _target_with_bad_version() -> str:
    import base64
    import json as _json

    payload = _json.dumps({"v": 9, "host": "lab", "project": "remo"}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")


REFUSAL_CASES = [
    (
        "exposed_host_other_project",
        lambda state: _write_state(state, exposures=[{"host": "lab", "projects": ["other"]}]),
        lambda: encode_target("lab", "widget"),
        "not-exposed",
        {},
    ),
    (
        "unexposed_host",
        lambda state: _write_state(
            state, exposures=[{"host": "other-host", "projects": ["widget"]}]
        ),
        lambda: encode_target("lab", "widget"),
        "not-exposed",
        {},
    ),
    (
        "missing_exposure_file",
        lambda state: _write_state(state, write_exposure=False),
        lambda: encode_target("lab", "widget"),
        "not-exposed",
        {},
    ),
    (
        "empty_exposures",
        lambda state: _write_state(state, exposures=[]),
        lambda: encode_target("lab", "widget"),
        "not-exposed",
        {},
    ),
    (
        "malformed_exposure_file",
        lambda state: (
            _write_state(state, write_exposure=False),
            (state / "exposure.json").write_text("{not valid json"),
        ),
        lambda: encode_target("lab", "remo"),
        "config-unreadable",
        {},
    ),
    (
        "missing_registry_file",
        lambda state: _write_state(state, write_registry=False),
        lambda: encode_target("lab", "remo"),
        "config-unreadable",
        {},
    ),
    (
        "raw_unencoded_name_with_space",
        lambda state: _write_state(state),
        lambda: "lab remo",
        "bad-target",
        {},
    ),
    (
        "unsupported_version",
        lambda state: _write_state(state),
        _target_with_bad_version,
        "unsupported-version",
        {},
    ),
    (
        "project_over_byte_cap",
        lambda state: _write_state(state),
        lambda: encode_target("lab", "a" * 300),
        "invalid-name",
        {},
    ),
    (
        "euid_zero",
        lambda state: _write_state(state),
        lambda: encode_target("lab", "remo"),
        "run-as-not-in-effect",
        {"geteuid": 0},
    ),
    (
        "user_is_ssm_user",
        lambda state: _write_state(state),
        lambda: encode_target("lab", "remo"),
        "run-as-not-in-effect",
        {"getuser": "ssm-user"},
    ),
    (
        "ssh_missing",
        lambda state: _write_state(state),
        lambda: encode_target("lab", "remo"),
        "ssh-missing",
        {"which": None},
    ),
]


@pytest.mark.parametrize(
    "name, setup, make_target, expected_code, overrides",
    REFUSAL_CASES,
    ids=[c[0] for c in REFUSAL_CASES],
)
def test_refusals_print_one_line_and_never_exec(
    name, setup, make_target, expected_code, overrides, state_dir, forbid_execvp, capsys, monkeypatch
) -> None:
    del name
    setup(state_dir)
    if "geteuid" in overrides:
        monkeypatch.setattr(connector_provider.os, "geteuid", lambda: overrides["geteuid"])
    if "getuser" in overrides:
        monkeypatch.setattr(connector_provider, "_effective_user", lambda: overrides["getuser"])
    if "which" in overrides:
        monkeypatch.setattr(connector_provider.shutil, "which", lambda _n: overrides["which"])

    rc = connector_provider.attach(make_target())

    assert rc == 1
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line]
    assert len(lines) == 1, f"expected exactly one output line, got: {lines!r}"
    match = _ERROR_LINE_RE.match(lines[0])
    assert match is not None, f"line does not match the error-line contract: {lines[0]!r}"
    assert match.group(1) == expected_code

    if expected_code == "not-exposed":
        assert "lab" not in match.group(2)
        assert "widget" not in match.group(2)


def test_not_exposed_does_not_reveal_registry_membership(state_dir, forbid_execvp, capsys) -> None:
    _write_state(state_dir, exposures=[{"host": "lab", "projects": ["other-project"]}])
    rc1 = connector_provider.attach(encode_target("lab", "remo"))
    line1 = capsys.readouterr().out.strip()
    assert rc1 == 1

    _write_state(
        state_dir,
        registry_hosts=[],
        exposures=[{"host": "lab", "projects": ["other-project"]}],
    )
    rc2 = connector_provider.attach(encode_target("unknown-host", "remo"))
    line2 = capsys.readouterr().out.strip()
    assert rc2 == 1

    match1 = _ERROR_LINE_RE.match(line1)
    match2 = _ERROR_LINE_RE.match(line2)
    assert match1 is not None and match2 is not None
    assert match1.group(0) == match2.group(0), (
        "the error line must be identical whether the host is in the registry-but-"
        "unexposed, or absent from the registry entirely"
    )


def test_unexpected_exception_is_one_error_line_not_a_traceback(
    state_dir, forbid_execvp, capsys, monkeypatch
) -> None:
    _write_state(state_dir)

    def _boom():
        raise RuntimeError("secret-ish internal detail")

    monkeypatch.setattr(connector_provider, "get_known_hosts", _boom)
    rc = connector_provider.attach(encode_target("lab", "remo"))

    assert rc == 1
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert len(lines) == 1
    match = _ERROR_LINE_RE.match(lines[0])
    assert match is not None
    assert match.group(1) == "config-unreadable"
    assert str(state_dir) in match.group(2)
    assert "secret-ish" not in match.group(2)
