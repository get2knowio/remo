"""The local host never travels through `remo web push` / `sync` (spec 029 FR-004a)."""

from __future__ import annotations

import pytest

from remo_cli.core import registry as core_registry
from remo_cli.core.registry import replace_registry
from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    InstanceOutcome,
    build_adoption_payload,
    is_portable_host,
    run_push,
)
from remo_cli.core.web_drift import DriftState, diff_registry_against_cache
from remo_cli.core.web_sync import parse_remote_registry, run_web_sync
from remo_cli.models.host import KnownHost

URL = "http://web.example:8080"
CODE = "ephemeral-pairing-code"
DEPLOYMENT_ID = "dep-1234abcd"
PUBLIC_KEY = f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeServiceKey remo-web@{DEPLOYMENT_ID}"


pytestmark = pytest.mark.usefixtures("ssm_provider")


def _local() -> KnownHost:
    return KnownHost("local", "local", "localhost", "paul", access_mode="direct")


def _box() -> KnownHost:
    return KnownHost("incus", "node1/dev", "10.0.0.1", "remo", access_mode="direct")


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = CODE
    client.get_status.return_value = {
        "state": "adopted", "registry_instances": 0, "payload_versions": [1, 2, 3],
    }
    client.get_identity.return_value = {
        "deployment_id": DEPLOYMENT_ID, "public_key": PUBLIC_KEY,
    }
    client.get_registry.return_value = {
        "entry_version": 2, "registry": [], "host_keys": {},
        "mirror_generation": 0, "last_change": None,
    }
    client.put_registry.return_value = {
        "registry_instances": 1, "host_key_instances": 1, "mirror_generation": 1,
    }
    client.post_verify.return_value = {"all_passed": True, "results": []}
    mocker.patch("remo_cli.core.web_adopt.SetupApiClient", return_value=client)
    mocker.patch("remo_cli.core.web_sync.SetupApiClient", return_value=client)
    return client


def _fake_process(mocker, module):
    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
        return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

    return mocker.patch(f"remo_cli.core.{module}._process_instance", side_effect=fake)


def test_predicate():
    assert is_portable_host(_box()) is True
    assert is_portable_host(_local()) is False


def test_payload_never_contains_the_local_entry():
    payload = build_adoption_payload([_box(), _local()], {"local": ["x"], "node1/dev": ["k"]})
    assert [e["name"] for e in payload["registry"]] == ["node1/dev"]
    assert "local" not in payload["host_keys"]


def test_push_never_processes_a_local_host(tmp_config_dir, api_client, mocker):
    mocker.patch(
        "remo_cli.core.web_adopt.get_known_hosts", return_value=[_local(), _box()]
    )
    process = _fake_process(mocker, "web_adopt")
    run_push(URL, CODE, interactive=False)
    assert [c.args[0].name for c in process.call_args_list] == ["node1/dev"]
    payload = api_client.put_registry.call_args.args[0]
    assert [e["name"] for e in payload["registry"]] == ["node1/dev"]


def test_push_with_only_a_local_host_counts_as_empty(tmp_config_dir, api_client, mocker):
    from remo_cli.core.web_adopt import EmptyRegistryError

    mocker.patch("remo_cli.core.web_adopt.get_known_hosts", return_value=[_local()])
    with pytest.raises(EmptyRegistryError):
        run_push(URL, CODE, interactive=False)


def test_sync_excludes_local_and_keeps_it_locally(tmp_config_dir, api_client, mocker):
    mocker.patch("sys.stdin.isatty", return_value=False)
    process = _fake_process(mocker, "web_sync")
    remote = KnownHost("cloudvm", "webbox", "10.0.0.2", "remo", access_mode="direct")
    replace_registry([_local(), _box()])
    api_client.get_registry.return_value = {
        "registry": [core_registry.known_host_to_entry(remote)],
        "host_keys": {}, "mirror_generation": 2,
    }
    assert run_web_sync(URL, CODE, assume_yes=True) == 0

    assert [c.args[0].name for c in process.call_args_list] == ["node1/dev"]
    payload = api_client.put_registry.call_args.args[0]
    assert {e["name"] for e in payload["registry"]} == {"node1/dev", "webbox"}
    names = {h.name for h in core_registry.read_registry(readonly=True).hosts}
    assert names == {"local", "node1/dev", "webbox"}


def test_sync_pull_cannot_overwrite_the_local_host_by_name(tmp_config_dir, api_client, mocker):
    """A deployment entry that happens to be named like the local host is
    ignored (it parses as a `local` entry) and never replaces ours."""
    mocker.patch("sys.stdin.isatty", return_value=False)
    _fake_process(mocker, "web_sync")
    replace_registry([_local()])
    spoof = KnownHost("local", "local", "localhost", "evil", access_mode="direct")
    api_client.get_registry.return_value = {
        "registry": [core_registry.known_host_to_entry(spoof)],
        "host_keys": {}, "mirror_generation": 2,
    }
    run_web_sync(URL, CODE, assume_yes=True, allow_empty=True)
    hosts = core_registry.read_registry(readonly=True).hosts
    assert [(h.type, h.user) for h in hosts] == [("local", "paul")]


def test_parse_remote_registry_drops_a_local_entry_with_a_warning(capsys):
    doc = {
        "registry": [
            core_registry.known_host_to_entry(_local()),
            core_registry.known_host_to_entry(_box()),
        ]
    }
    parsed = parse_remote_registry(doc)
    assert [h.name for h in parsed.hosts] == ["node1/dev"]
    captured = capsys.readouterr()
    assert "never synced" in captured.out + captured.err


def test_drift_ignores_the_local_host():
    assert [e.name for e in diff_registry_against_cache([_local(), _box()], {})] == [
        "node1/dev"
    ]
    assert DriftState.NEW  # sanity: the box is "new", the local host is absent


def test_service_discovery_never_sees_the_local_host(tmp_config_dir):
    from remo_cli.web.discovery import _read_registry_hosts_readonly

    replace_registry([_local(), _box()])
    assert [h.name for h in _read_registry_hosts_readonly()] == ["node1/dev"]


def test_web_check_never_probes_the_local_host(tmp_config_dir, tmp_path, monkeypatch, mocker):
    """`remo web check` on a mount-configured service must not SSH to the
    workstation's own `localhost` for the local host (as discovery does not)."""
    from remo_cli.web import check as check_module
    from remo_cli.web.config import WebSettings

    replace_registry([_local(), _box()])
    key = tmp_path / "id_ed25519"
    key.write_text("x\n")
    monkeypatch.setenv("REMO_WEB_SSH_IDENTITY_FILE", str(key))
    monkeypatch.setenv("REMO_WEB_MODE", "mount_configured")
    probed = mocker.patch.object(
        check_module, "_instance_check",
        side_effect=lambda host, settings: check_module.CheckResult(
            f"instance {host.type}/{host.name}", True, "ok"
        ),
    )
    results = check_module.run_checks(WebSettings(ssh_control_dir=str(tmp_path / "ctl")))
    assert [c.args[0].name for c in probed.call_args_list] == ["node1/dev"]
    assert not any(r.name == "instance local/local" for r in results)
