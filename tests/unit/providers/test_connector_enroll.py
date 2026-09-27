"""US3: connector enrollment (specs/025-ssm-connector, contracts/cli.md).

Covers the secret-safety guarantees (SC-005: the activation code appears in
no argv element and no surviving file), every pre-flight refusal (no
playbook run), the self-target -> localhost mapping, the registry.json the
role receives, and the exposures HOST/PROJECT split.
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.providers import connector as connector_provider

REGISTRY_HOSTS = [
    {
        "type": "ssh",
        "name": "lxc1",
        "host": "10.0.0.12",
        "user": "remo",
        "access": "direct",
        "ssh": {"port": 22, "identity_file": "~/.ssh/k"},
    },
    {
        "type": "proxmox",
        "name": "proxmox-1/dev",
        "host": "10.0.0.20",
        "user": "remo",
        "access": "direct",
    },
    {
        "type": "aws",
        "name": "aws1",
        "host": "3.1.4.1",
        "user": "remo",
        "access": "ssm",
        "aws": {"instance_id": "i-0abc123def", "region": "us-west-2"},
    },
]


@pytest.fixture
def operator_registry(tmp_config_dir):
    (tmp_config_dir / "registry.json").write_text(
        json.dumps({"version": 2, "hosts": REGISTRY_HOSTS}, indent=2) + "\n"
    )
    return tmp_config_dir


@pytest.fixture
def captured_playbook(monkeypatch):
    calls: list[dict] = []

    def _fake_run_playbook(playbook, extra_vars=None, inventory=None, verbose=False):
        extra_vars = list(extra_vars or [])
        vars_file_content = None
        path = None
        for value in extra_vars:
            if value.startswith("@"):
                path = value[1:]
                with open(path, encoding="utf-8") as fh:
                    vars_file_content = json.load(fh)
                break
        calls.append(
            {
                "playbook": playbook,
                "extra_vars": extra_vars,
                "inventory": inventory,
                "verbose": verbose,
                "vars_file_path": path,
                "vars_file_content": vars_file_content,
            }
        )
        return 0

    monkeypatch.setattr(connector_provider, "run_playbook", _fake_run_playbook)
    return calls


def _vars_file_path(extra_vars: list[str]) -> str:
    for value in extra_vars:
        if value.startswith("@"):
            return value[1:]
    raise AssertionError(f"no '@<path>' vars-file entry found in {extra_vars!r}")


def _base_kwargs(**overrides):
    kwargs = dict(
        name="lxc1",
        activation_id="act-123",
        region="eu-central-1",
        expose=("lxc1/remo",),
        run_as_user="remo-connector",
        remo_version=None,
        remo_source=None,
        code="SECRETCODE123",
        verbose=False,
    )
    kwargs.update(overrides)
    return kwargs


def _stub_status(monkeypatch):
    """enroll() ends by calling status(), which does a live SSH round-trip
    we don't want in these unit tests -- stub it to a no-op success."""
    monkeypatch.setattr(connector_provider, "status", lambda name: 0)


# ---------------------------------------------------------------------------
# (a) secret safety
# ---------------------------------------------------------------------------


def test_code_never_in_argv_and_vars_file_0600_then_deleted(
    operator_registry, monkeypatch
) -> None:
    captured: dict = {}

    def _fake_run_playbook(playbook, extra_vars=None, inventory=None, verbose=False):
        del playbook, inventory, verbose
        path = _vars_file_path(list(extra_vars or []))
        captured["path"] = path
        captured["extra_vars"] = list(extra_vars or [])
        mode = stat.S_IMODE(os.stat(path).st_mode)
        captured["mode"] = mode
        with open(path, encoding="utf-8") as fh:
            captured["vars_file_content"] = json.load(fh)
        return 0

    monkeypatch.setattr(connector_provider, "run_playbook", _fake_run_playbook)
    _stub_status(monkeypatch)

    connector_provider.enroll(**_base_kwargs())

    assert "SECRETCODE123" not in " ".join(captured["extra_vars"])
    assert captured["mode"] == 0o600
    assert captured["vars_file_content"]["ssm_activation_code"] == "SECRETCODE123"
    assert not os.path.exists(captured["path"]), "vars file must be deleted after the run"


# ---------------------------------------------------------------------------
# (b) code input (CLI layer)
# ---------------------------------------------------------------------------


def _raw_enroll_cmd():
    """The plain Python function `cli/connector.py::enroll_cmd` wraps —
    reached via `__wrapped__` (set by `functools.wraps` inside
    `provider_command`) so it can be called directly with keyword args,
    bypassing both Click's argv parsing AND `provider_command`'s
    exception/int -> `sys.exit` translation (CliRunner also replaces
    `sys.stdin` wholesale on `invoke()`, which defeats monkeypatching
    `sys.stdin.isatty` beforehand — calling the function directly sidesteps
    that entirely)."""
    import remo_cli.cli.connector as cli_connector

    return cli_connector.enroll_cmd.callback.__wrapped__


def test_code_from_stdin_when_not_tty(operator_registry, monkeypatch) -> None:
    import io

    captured_code: dict = {}

    def _fake_enroll(name, **kwargs):
        del name
        captured_code["code"] = kwargs["code"]
        return 0

    monkeypatch.setattr("remo_cli.providers.connector.enroll", _fake_enroll)
    monkeypatch.setattr("sys.stdin", io.StringIO("PIPEDCODE\n"))

    rc = _raw_enroll_cmd()(
        name="lxc1",
        activation_id="act-1",
        region="eu-central-1",
        expose=("lxc1/remo",),
        run_as_user=None,
        remo_version=None,
        remo_source=None,
        verbose=False,
    )
    assert rc == 0
    assert captured_code["code"] == "PIPEDCODE"


def test_code_from_hidden_prompt_when_tty(operator_registry, monkeypatch) -> None:
    import click

    captured_code: dict = {}
    prompt_calls: list[dict] = []

    def _fake_enroll(name, **kwargs):
        del name
        captured_code["code"] = kwargs["code"]
        return 0

    def _fake_prompt(text, hide_input=False, **kwargs):
        prompt_calls.append({"text": text, "hide_input": hide_input})
        return "PROMPTEDCODE"

    monkeypatch.setattr("remo_cli.providers.connector.enroll", _fake_enroll)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr(click, "prompt", _fake_prompt)

    rc = _raw_enroll_cmd()(
        name="lxc1",
        activation_id="act-1",
        region="eu-central-1",
        expose=("lxc1/remo",),
        run_as_user=None,
        remo_version=None,
        remo_source=None,
        verbose=False,
    )
    assert rc == 0
    assert captured_code["code"] == "PROMPTEDCODE"
    assert prompt_calls and prompt_calls[0]["hide_input"] is True


# ---------------------------------------------------------------------------
# (c)/(d) pre-flight refusals
# ---------------------------------------------------------------------------


def test_empty_code_is_precondition_error_before_playbook(
    operator_registry, captured_playbook
) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(code="   "))
    assert captured_playbook == []


def test_unknown_connector_name(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(name="nope", expose=("nope/remo",)))
    assert captured_playbook == []


def test_unknown_exposed_host(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(expose=("ghost-host/remo",)))
    assert captured_playbook == []


def test_ssm_mode_host_refused(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError, match="directly reachable"):
        connector_provider.enroll(**_base_kwargs(expose=("aws1/remo",)))
    assert captured_playbook == []


def test_invalid_project_in_expose(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(expose=("lxc1/../etc",)))
    assert captured_playbook == []


def test_run_as_user_root_or_ssm_user_refused(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(run_as_user="root"))
    with pytest.raises(PreconditionError):
        connector_provider.enroll(**_base_kwargs(run_as_user="ssm-user"))
    assert captured_playbook == []


def test_remo_version_and_source_mutually_exclusive(operator_registry, captured_playbook) -> None:
    with pytest.raises(PreconditionError):
        connector_provider.enroll(
            **_base_kwargs(remo_version="4.4.0", remo_source="git+https://x@y")
        )
    assert captured_playbook == []


# ---------------------------------------------------------------------------
# (e) self-target -> localhost
# ---------------------------------------------------------------------------


def test_self_target_uses_localhost(operator_registry, captured_playbook, monkeypatch) -> None:
    _stub_status(monkeypatch)
    connector_provider.enroll(**_base_kwargs(expose=("lxc1/remo",)))

    call = captured_playbook[0]
    payload = call["vars_file_content"]
    registry_doc = json.loads(payload["connector_registry_json"])
    lxc1_entries = [h for h in registry_doc["hosts"] if h["name"] == "lxc1"]
    assert len(lxc1_entries) == 1
    assert lxc1_entries[0]["host"] == "localhost"


# ---------------------------------------------------------------------------
# (f) registry.json is v2, round-trips through core.registry
# ---------------------------------------------------------------------------


def test_registry_json_is_v2_from_core_serializer(
    operator_registry, captured_playbook, monkeypatch
) -> None:
    _stub_status(monkeypatch)
    connector_provider.enroll(
        **_base_kwargs(name="proxmox-1/dev", expose=("proxmox-1/dev/remo",))
    )

    call = captured_playbook[0]
    payload = call["vars_file_content"]
    registry_doc = json.loads(payload["connector_registry_json"])

    assert registry_doc["version"] == 2
    from remo_cli.core.registry import entry_to_known_host

    hosts = [entry_to_known_host(e) for e in registry_doc["hosts"]]
    assert all(h is not None for h in hosts)
    exposed = [h for h in hosts if h is not None and h.name == "proxmox-1/dev"]
    assert len(exposed) == 1
    assert exposed[0].type == "ssh"
    assert exposed[0].ssh_port == 22
    assert exposed[0].ssh_identity == "/var/lib/remo-connector/id_ed25519"


# ---------------------------------------------------------------------------
# (g) exposures split on the LAST '/'
# ---------------------------------------------------------------------------


def test_exposures_split_on_last_slash() -> None:
    parsed = connector_provider.parse_expose(("proxmox-1/dev/remo",))
    assert parsed == {"proxmox-1/dev": ["remo"]}


# ---------------------------------------------------------------------------
# (h) playbook failure -> OperationFailedError; vars file still deleted
# ---------------------------------------------------------------------------


def test_playbook_failure_is_operation_failed_error(operator_registry, monkeypatch) -> None:
    captured: dict = {}

    def _failing_run_playbook(playbook, extra_vars=None, inventory=None, verbose=False):
        del playbook, inventory, verbose
        captured["path"] = _vars_file_path(list(extra_vars or []))
        return 2

    monkeypatch.setattr(connector_provider, "run_playbook", _failing_run_playbook)

    with pytest.raises(OperationFailedError):
        connector_provider.enroll(**_base_kwargs())

    assert not os.path.exists(captured["path"])


# ---------------------------------------------------------------------------
# (i) remo_version default is the running CLI version
# ---------------------------------------------------------------------------


def test_remo_version_default_is_running_version(
    operator_registry, captured_playbook, monkeypatch
) -> None:
    import remo_cli

    _stub_status(monkeypatch)
    connector_provider.enroll(**_base_kwargs(remo_version=None, remo_source=None))

    extra_vars = captured_playbook[0]["extra_vars"]
    assert f"remo_version={remo_cli.__version__}" in extra_vars


# ---------------------------------------------------------------------------
# (j) free-form connection vars travel in the JSON vars file, never as
#     `-e key=value` (ansible-playbook splits key=value on whitespace)
# ---------------------------------------------------------------------------


def test_connection_vars_and_remo_source_travel_in_vars_file(
    operator_registry, captured_playbook, monkeypatch
) -> None:
    import shlex

    from remo_cli.core import ssh as core_ssh

    monkeypatch.setenv("TZ", "UTC")  # build_ssh_opts exports TZ; restore it afterward
    monkeypatch.setattr(core_ssh, "detect_timezone", lambda: "Europe/Berlin")
    _stub_status(monkeypatch)
    connector_provider.enroll(
        **_base_kwargs(remo_source="remo-cli @ git+https://example.invalid/remo@abc")
    )

    call = captured_playbook[0]
    assert not any(
        v.startswith(("remo_ssh_", "remo_source=", "remo_version=")) for v in call["extra_vars"]
    ), call["extra_vars"]
    payload = call["vars_file_content"]
    assert payload["remo_source"] == "remo-cli @ git+https://example.invalid/remo@abc"
    assert payload["remo_ssh_host"] == "10.0.0.12"
    assert payload["remo_ssh_user"] == "remo"
    assert payload["remo_ssh_port"] == "22"
    assert payload["remo_ssh_identity"] == "~/.ssh/k"
    assert shlex.split(payload["remo_ssh_common_args"]) == ["-o", "SendEnv=TZ"]
    exposed = payload["connector_exposed_hosts"]
    assert exposed == [
        {
            "name": "lxc1",
            "address": "localhost",
            "user": "remo",
            "port": "22",
            "identity": "~/.ssh/k",
            "common_args": "-o SendEnv=TZ",
        }
    ]


def test_ssm_connector_host_common_args_round_trip_proxycommand(operator_registry) -> None:
    import shlex

    from remo_cli.core.known_hosts import get_known_hosts

    aws = next(h for h in get_known_hosts() if h.name == "aws1")
    conn = connector_provider._connection_vars(aws)
    args = shlex.split(conn["common_args"])
    assert "ControlMaster=auto" not in " ".join(args)
    proxy = [a for a in args if a.startswith("ProxyCommand=")]
    assert len(proxy) == 1
    assert "start-session" in proxy[0]
    assert conn["host"] == "i-0abc123def"  # the ProxyCommand's %h


def test_unenroll_passes_connection_vars_via_deleted_vars_file(
    operator_registry, captured_playbook, monkeypatch
) -> None:
    monkeypatch.setattr(
        connector_provider.subprocess,
        "run",
        lambda *a, **k: connector_provider.subprocess.CompletedProcess(a, 1, "", ""),
    )
    rc = connector_provider.unenroll("lxc1", purge=True, assume_yes=True, confirm=lambda _p: False)

    assert rc == 0
    call = captured_playbook[0]
    assert call["playbook"] == "ssm_connector_unenroll.yml"
    assert "connector_purge=true" in call["extra_vars"]
    assert not any(v.startswith("remo_ssh_") for v in call["extra_vars"])
    assert call["vars_file_content"]["remo_ssh_host"] == "10.0.0.12"
    assert call["vars_file_content"]["remo_ssh_identity"] == "~/.ssh/k"
    assert not os.path.exists(call["vars_file_path"])


def test_unenroll_without_yes_asks_and_aborts(operator_registry, captured_playbook) -> None:
    from remo_cli.core.errors import UserAbortedError

    with pytest.raises(UserAbortedError):
        connector_provider.unenroll("lxc1", purge=False, assume_yes=False, confirm=lambda _p: False)
    assert captured_playbook == []
