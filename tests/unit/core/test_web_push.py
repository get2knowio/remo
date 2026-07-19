"""Unit tests for `remo web push` (012-web-adopt-pairing, T030).

Covers the non-secret, deployment-keyed push cache and the delta logic now that
`run_push(url, code, ...)` resolves URL + pairing code every time and nothing
durable (url/token) is persisted (FR-018/FR-019):

* Push cache lifecycle: atomic 0600 writes, deployment-keyed round-trip, junk
  entries dropped, absent/corrupt -> {}, and the 011 credential file format
  (url/token + name-keyed cache) ignored (parsed to {}).
* Fingerprint stability (unchanged from 011): any field change re-fingerprints.
* Delta logic via run_push: unchanged instances skip keyscan/authorize and reuse
  cached lines, new/changed get full treatment, removed instances drop out with a
  manual-revoke note, cache rebuilt only after a successful PUT.
* Errors: mount_configured, missing public key, empty-registry guard, dormant
  404 mapping.
"""

from __future__ import annotations

import json
import stat

import pytest

from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    OUTCOME_SKIPPED_UNREACHABLE,
    OUTCOME_UNCHANGED,
    AdoptError,
    CachedInstance,
    EmptyRegistryError,
    InstanceOutcome,
    MountConfiguredError,
    SetupApiError,
    SetupNotFoundError,
    _adopt_flow,
    instance_fingerprint,
    load_push_cache,
    push_cache_path,
    run_push,
    save_push_cache,
)
from remo_cli.models.host import KnownHost

URL = "http://web.example:8080"
CODE = "ephemeral-pairing-code"
DEPLOYMENT_ID = "dep-1234abcd"
PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKk4mCBB2AVDBWvIRtRZlc2VydmljZWtleQ "
    f"remo-web@{DEPLOYMENT_ID}"
)

KEY_LINE_NODE1 = "10.0.0.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyNode1ForTests"
KEY_LINE_WEB1 = "5.6.7.8 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyWeb1ForTests"


def _make_host(type_="incus", name="node1/dev", host="10.0.0.1", user="remo", **kwargs):
    return KnownHost(type=type_, name=name, host=host, user=user, **kwargs)


def _ssm_host() -> KnownHost:
    return _make_host(
        type_="aws",
        name="devbox-ssm",
        host="3.14.15.92",
        instance_id="i-0abc123def",
        access_mode="ssm",
        region="us-west-2",
    )


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = CODE
    client.get_status.return_value = {"state": "adopted", "registry_instances": 2}
    client.get_identity.return_value = {"deployment_id": DEPLOYMENT_ID, "public_key": PUBLIC_KEY}
    client.put_registry.return_value = {"registry_instances": 2, "host_key_instances": 1}
    client.post_verify.return_value = {"all_passed": True, "results": []}
    mocker.patch("remo_cli.core.web_adopt.SetupApiClient", return_value=client)
    return client


@pytest.fixture
def registry(mocker):
    return mocker.patch("remo_cli.core.web_adopt.get_known_hosts", return_value=[])


def _fake_process_instance(mocker, outcome=OUTCOME_ADOPTED):
    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        if outcome == OUTCOME_ADOPTED:
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
        return InstanceOutcome(host, outcome, detail="mocked")

    return mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)


# ---------------------------------------------------------------------------
# Push cache lifecycle (012 R10)
# ---------------------------------------------------------------------------


class TestPushCacheLifecycle:
    def test_path_under_remo_home(self, tmp_config_dir):
        assert push_cache_path() == tmp_config_dir / "web-service.json"

    def test_writes_0600(self, tmp_config_dir):
        path = save_push_cache({DEPLOYMENT_ID: {}})
        assert _mode(path) == 0o600

    def test_no_url_or_token_persisted(self, tmp_config_dir):
        save_push_cache(
            {DEPLOYMENT_ID: {"n": CachedInstance("f" * 64, [KEY_LINE_NODE1])}}
        )
        text = push_cache_path().read_text()
        assert "token" not in text
        assert "http" not in text  # no url

    def test_round_trip_deployment_keyed(self, tmp_config_dir):
        cache = {
            DEPLOYMENT_ID: {
                "node1/dev": CachedInstance("a" * 64, [KEY_LINE_NODE1]),
                "web1": CachedInstance("b" * 64, [KEY_LINE_WEB1]),
            },
            "other-dep": {"x": CachedInstance("c" * 64, [])},
        }
        save_push_cache(cache)
        assert load_push_cache() == cache

    def test_absent_returns_empty(self, tmp_config_dir):
        assert load_push_cache() == {}

    def test_corrupt_returns_empty(self, tmp_config_dir):
        push_cache_path().write_text("{not json")
        assert load_push_cache() == {}

    def test_old_011_credential_format_ignored(self, tmp_config_dir):
        # 011 file: top-level url/token + name-keyed push_cache (values are the
        # entry dicts directly). The deployment-keyed loader must ignore it.
        push_cache_path().write_text(
            json.dumps(
                {
                    "url": URL,
                    "token": "old-secret",
                    "deployment_id": DEPLOYMENT_ID,
                    "push_cache": {
                        "node1/dev": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_NODE1]}
                    },
                }
            )
        )
        assert load_push_cache() == {}

    def test_junk_entries_dropped(self, tmp_config_dir):
        push_cache_path().write_text(
            json.dumps(
                {
                    "push_cache": {
                        DEPLOYMENT_ID: {
                            "good": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_NODE1]},
                            "bad": "not-a-dict",
                        },
                        "all-junk-dep": {"y": "not-a-dict", "z": ["list"]},
                        "empty-dep": {},
                    }
                }
            )
        )
        loaded = load_push_cache()
        assert set(loaded) == {DEPLOYMENT_ID}
        assert set(loaded[DEPLOYMENT_ID]) == {"good"}


# ---------------------------------------------------------------------------
# Fingerprint stability (unchanged from 011)
# ---------------------------------------------------------------------------


class TestInstanceFingerprint:
    def test_same_entry_same_fingerprint(self):
        assert instance_fingerprint(_make_host()) == instance_fingerprint(_make_host())

    def test_is_sha256_hex(self):
        fp = instance_fingerprint(_make_host())
        assert len(fp) == 64
        int(fp, 16)

    @pytest.mark.parametrize(
        "change",
        [
            {"type_": "hetzner"},
            {"name": "node1/other"},
            {"host": "10.0.0.99"},
            {"user": "other"},
            {"instance_id": "i-0abc123"},
            {"access_mode": "direct"},
            {"region": "us-east-1"},
        ],
    )
    def test_any_field_change_changes_fingerprint(self, change):
        assert instance_fingerprint(_make_host()) != instance_fingerprint(_make_host(**change))


# ---------------------------------------------------------------------------
# Adopt seeds the push cache (no consent, no url/token)
# ---------------------------------------------------------------------------


class TestAdoptSeedsCache:
    def test_adopt_seeds_deployment_keyed_cache(self, tmp_config_dir, api_client, registry, mocker):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        _adopt_flow(api_client, allow_empty=False, interactive=False)
        loaded = load_push_cache()
        assert set(loaded) == {DEPLOYMENT_ID}
        assert set(loaded[DEPLOYMENT_ID]) == {"node1/dev"}
        assert loaded[DEPLOYMENT_ID]["node1/dev"].fingerprint == instance_fingerprint(_make_host())


# ---------------------------------------------------------------------------
# Delta logic via run_push
# ---------------------------------------------------------------------------


class TestPushDelta:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry):
        self.client = api_client
        self.registry = registry

    def _seed(self, instances):
        save_push_cache({DEPLOYMENT_ID: instances})

    def _put_payload(self):
        self.client.put_registry.assert_called_once()
        return self.client.put_registry.call_args.args[0]

    def test_unchanged_instance_skips_keyscan_and_authorize(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed({host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])})
        scan = mocker.patch("remo_cli.core.web_adopt.scan_and_verify_host_key")
        authorize = mocker.patch("remo_cli.core.web_adopt.authorize_service_key")

        result = run_push(URL, CODE, interactive=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]
        scan.assert_not_called()
        authorize.assert_not_called()

    def test_unchanged_instance_reuses_cached_lines(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed({host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])})
        _fake_process_instance(mocker)

        run_push(URL, CODE, interactive=False)

        payload = self._put_payload()
        assert payload["host_keys"] == {host.name: [KEY_LINE_NODE1]}

    def test_changed_fingerprint_gets_full_treatment(self, mocker):
        host = _make_host(host="10.0.0.99")
        self.registry.return_value = [host]
        stale_fp = instance_fingerprint(_make_host())
        self._seed({host.name: CachedInstance(stale_fp, [KEY_LINE_NODE1])})
        _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]
        assert self._put_payload()["host_keys"][host.name] != [KEY_LINE_NODE1]

    def test_new_instance_gets_full_treatment(self, mocker):
        old = _make_host()
        new = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        self.registry.return_value = [old, new]
        self._seed({old.name: CachedInstance(instance_fingerprint(old), [KEY_LINE_NODE1])})
        process = _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False)

        assert {o.host.name: o.outcome for o in result.outcomes} == {
            old.name: OUTCOME_UNCHANGED,
            new.name: OUTCOME_ADOPTED,
        }
        assert process.call_count == 1

    def test_removed_instance_dropped_with_revoke_note(self, capsys):
        remaining = _make_host()
        self.registry.return_value = [remaining]
        self._seed(
            {
                remaining.name: CachedInstance(instance_fingerprint(remaining), [KEY_LINE_NODE1]),
                "gone-host": CachedInstance("c" * 64, [KEY_LINE_WEB1]),
            }
        )

        run_push(URL, CODE, interactive=False)

        payload = self._put_payload()
        assert [e["name"] for e in payload["registry"]] == [remaining.name]
        out = capsys.readouterr().out
        assert "gone-host" in out and "revoke it manually" in out
        loaded = load_push_cache()
        assert set(loaded[DEPLOYMENT_ID]) == {remaining.name}

    def test_cache_rebuilt_after_successful_put(self, mocker):
        unchanged = _make_host()
        fresh = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        flaky = _make_host(type_="hetzner", name="down1", host="5.6.7.9")
        self.registry.return_value = [unchanged, fresh, flaky]
        self._seed({unchanged.name: CachedInstance(instance_fingerprint(unchanged), [KEY_LINE_NODE1])})

        def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
            if host.name == flaky.name:
                return InstanceOutcome(host, OUTCOME_SKIPPED_UNREACHABLE, detail="down")
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfresh"]
            return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

        mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)

        run_push(URL, CODE, interactive=False)

        loaded = load_push_cache()
        assert set(loaded[DEPLOYMENT_ID]) == {unchanged.name, fresh.name}

    def test_failed_put_leaves_cache_untouched(self, mocker):
        host = _make_host()
        changed = _make_host(host="10.9.9.9")
        self.registry.return_value = [changed]
        original = {host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])}
        self._seed(original)
        _fake_process_instance(mocker)
        self.client.put_registry.side_effect = SetupApiError("boom", status=500)

        with pytest.raises(SetupApiError):
            run_push(URL, CODE, interactive=False)

        assert load_push_cache() == {DEPLOYMENT_ID: original}


# ---------------------------------------------------------------------------
# Hard failures via run_push
# ---------------------------------------------------------------------------


class TestPushErrors:
    def test_client_built_from_supplied_url_and_code(self, tmp_config_dir, api_client, registry, mocker):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        from remo_cli.core import web_adopt

        run_push(URL, CODE, interactive=False)
        web_adopt.SetupApiClient.assert_called_once_with(URL, CODE)

    def test_mount_configured_aborts(self, tmp_config_dir, api_client, registry):
        api_client.get_status.return_value = {"state": "mount_configured"}
        with pytest.raises(MountConfiguredError):
            run_push(URL, CODE, interactive=False)
        api_client.put_registry.assert_not_called()

    def test_missing_public_key_aborts(self, tmp_config_dir, api_client, registry):
        api_client.get_identity.return_value = {"deployment_id": DEPLOYMENT_ID, "public_key": ""}
        with pytest.raises(AdoptError, match="no public key"):
            run_push(URL, CODE, interactive=False)
        api_client.put_registry.assert_not_called()

    def test_empty_registry_guard(self, tmp_config_dir, api_client, registry):
        registry.return_value = []
        with pytest.raises(EmptyRegistryError, match="--allow-empty"):
            run_push(URL, CODE, interactive=False)
        api_client.put_registry.assert_not_called()

    def test_dormant_404_maps_to_reopen_message(self, tmp_config_dir, api_client, registry):
        registry.return_value = [_make_host()]
        api_client.get_identity.side_effect = SetupNotFoundError(
            "dormant", status=404
        )
        with pytest.raises(SetupNotFoundError):
            run_push(URL, CODE, interactive=False)
