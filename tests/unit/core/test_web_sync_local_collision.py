"""A deployment entry named like this machine's local host is never pulled
(#280, follow-up to spec 029 FR-004a) — and never deleted from the deployment."""

from __future__ import annotations

import pytest

from remo_cli.core import registry as core_registry
from remo_cli.core.registry import replace_registry
from remo_cli.core.web_adopt import OUTCOME_ADOPTED, InstanceOutcome, load_push_cache
from remo_cli.core.web_sync import run_web_sync, split_local_name_collisions
from remo_cli.models.host import KnownHost

URL = "http://web.example:8080"
CODE = "ephemeral-pairing-code"
DEPLOYMENT_ID = "dep-1234abcd"
PUBLIC_KEY = f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeServiceKey remo-web@{DEPLOYMENT_ID}"
SHADOW_KEYS = ["10.9.9.9 ssh-ed25519 AAAAshadowkey"]

pytestmark = pytest.mark.usefixtures("ssm_provider")


def _local() -> KnownHost:
    return KnownHost("local", "local", "localhost", "paul", access_mode="direct")


def _box() -> KnownHost:
    return KnownHost("incus", "node1/dev", "10.0.0.1", "remo", access_mode="direct")


def _shadow() -> KnownHost:
    return KnownHost("ssh", "local", "10.9.9.9", "remo", access_mode="direct")


def _webbox() -> KnownHost:
    return KnownHost("ssh", "webbox", "10.0.0.2", "remo", access_mode="direct")


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
    client.put_registry.return_value = {
        "registry_instances": 2, "host_key_instances": 2, "mirror_generation": 3,
    }
    client.post_verify.return_value = {"all_passed": True, "results": []}
    mocker.patch("remo_cli.core.web_sync.SetupApiClient", return_value=client)
    mocker.patch("sys.stdin.isatty", return_value=False)
    return client


@pytest.fixture
def processed(mocker):
    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
        return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

    return mocker.patch("remo_cli.core.web_sync._process_instance", side_effect=fake)


def _deployment(*hosts: KnownHost, keys: dict[str, list[str]] | None = None) -> dict:
    return {
        "registry": [core_registry.known_host_to_entry(h) for h in hosts],
        "host_keys": keys or {},
        "mirror_generation": 2,
    }


def _registry_tuples() -> list[tuple[str, str, str]]:
    hosts = core_registry.read_registry(readonly=True).hosts
    return sorted((h.type, h.name, h.host) for h in hosts)


def test_split_partitions_on_local_host_names_only():
    mergeable, shadowed = split_local_name_collisions(
        [_shadow(), _webbox()], [_local(), _box()]
    )
    assert [h.name for h in mergeable] == ["webbox"]
    assert [(h.type, h.name) for h in shadowed] == [("ssh", "local")]
    # No local host registered: nothing is shadowed.
    mergeable, shadowed = split_local_name_collisions([_shadow()], [_box()])
    assert [h.name for h in mergeable] == ["local"]
    assert shadowed == []


def test_colliding_pull_is_skipped_warned_and_kept_on_the_deployment(
    tmp_config_dir, api_client, processed, capsys
):
    replace_registry([_local(), _box()])
    before = _registry_tuples()
    api_client.get_registry.return_value = _deployment(
        _box(), _shadow(), keys={"local": SHADOW_KEYS}
    )

    assert run_web_sync(URL, CODE, assume_yes=True) == 0

    # Registry unchanged: no second host named "local".
    assert _registry_tuples() == before
    names = [h.name for h in core_registry.read_registry(readonly=True).hosts]
    assert names.count("local") == 1

    # The warning names both sides and the way out.
    out = capsys.readouterr()
    text = out.out + out.err
    assert "deployment's ssh host 'local'" in text
    assert "local host is also named 'local'" in text
    assert "remo local setup --name" in text

    # The deployment keeps its entry (and its host keys) untouched.
    payload = api_client.put_registry.call_args.args[0]
    entries = {e["name"]: e for e in payload["registry"]}
    assert set(entries) == {"node1/dev", "local"}
    assert entries["local"] == core_registry.known_host_to_entry(_shadow())
    assert payload["host_keys"]["local"] == SHADOW_KEYS
    # Never keyscanned/authorized from this workstation.
    assert "local" not in [c.args[0].name for c in processed.call_args_list]
    # Never recorded as a synchronized merge base.
    assert "local" not in load_push_cache()[DEPLOYMENT_ID].instances


def test_colliding_pull_rerun_is_a_noop(tmp_config_dir, api_client, processed):
    replace_registry([_local(), _box()])
    api_client.get_registry.return_value = _deployment(
        _box(), _shadow(), keys={"local": SHADOW_KEYS}
    )
    assert run_web_sync(URL, CODE, assume_yes=True) == 0
    first_registry = _registry_tuples()
    first_payload = api_client.put_registry.call_args.args[0]
    first_cache = dict(load_push_cache()[DEPLOYMENT_ID].instances)

    api_client.get_registry.return_value = {
        **_deployment(_box(), _shadow(), keys={"local": SHADOW_KEYS}),
        "mirror_generation": 3,
    }
    assert run_web_sync(URL, CODE, assume_yes=True) == 0

    assert _registry_tuples() == first_registry
    second_payload = api_client.put_registry.call_args.args[0]
    assert second_payload["registry"] == first_payload["registry"]
    assert second_payload["host_keys"] == first_payload["host_keys"]
    assert load_push_cache()[DEPLOYMENT_ID].instances == first_cache


def test_colliding_entry_alone_does_not_trip_the_empty_guard(
    tmp_config_dir, api_client, processed
):
    replace_registry([_local()])
    api_client.get_registry.return_value = _deployment(_shadow())
    assert run_web_sync(URL, CODE, assume_yes=True) == 0
    payload = api_client.put_registry.call_args.args[0]
    assert [e["name"] for e in payload["registry"]] == ["local"]
    assert _registry_tuples() == [("local", "local", "localhost")]


def test_non_colliding_pull_is_unchanged(tmp_config_dir, api_client, processed, capsys):
    replace_registry([_local(), _box()])
    api_client.get_registry.return_value = _deployment(_box(), _webbox())

    assert run_web_sync(URL, CODE, assume_yes=True) == 0

    assert _registry_tuples() == sorted(
        [
            ("local", "local", "localhost"),
            ("incus", "node1/dev", "10.0.0.1"),
            ("ssh", "webbox", "10.0.0.2"),
        ]
    )
    out = capsys.readouterr()
    assert "not pulling" not in out.out + out.err
    payload = api_client.put_registry.call_args.args[0]
    assert {e["name"] for e in payload["registry"]} == {"node1/dev", "webbox"}


def test_same_name_without_a_local_host_is_pulled(tmp_config_dir, api_client, processed):
    """No local host registered -> nothing to collide with: an ssh entry named
    `local` is an ordinary pull."""
    replace_registry([_box()])
    api_client.get_registry.return_value = _deployment(_box(), _shadow())
    assert run_web_sync(URL, CODE, assume_yes=True) == 0
    assert ("ssh", "local", "10.9.9.9") in _registry_tuples()


def test_dry_run_warns_and_writes_nothing(tmp_config_dir, api_client, processed, capsys):
    replace_registry([_local(), _box()])
    before = _registry_tuples()
    api_client.get_registry.return_value = _deployment(_box(), _shadow())
    assert run_web_sync(URL, CODE, assume_yes=True, dry_run=True) == 0
    assert _registry_tuples() == before
    api_client.put_registry.assert_not_called()
    out = capsys.readouterr()
    assert "not pulling the deployment's ssh host 'local'" in out.out + out.err
