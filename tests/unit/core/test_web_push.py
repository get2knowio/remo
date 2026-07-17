"""Unit tests for `remo web push` (011-web-adopt US4, T042).

Covers:

* Saved-credentials lifecycle (FR-025): atomic 0600 writes (including
  tightening a pre-existing 0644 file), full round-trip of
  url/token/deployment_id/push_cache, absent/corrupt files -> None from
  ``load_saved_credentials``, lenient ``_parse_push_cache`` junk handling,
  and backward compatibility with files written before the delta cache.
* Consent semantics (FR-025): ``--yes`` alone never saves; ``--save`` or an
  interactive yes does (exercised at the ``_adopt_flow`` level with mocks).
* Fingerprint + delta logic (FR-026): fingerprint stability, unchanged
  instances skipping keyscan/authorize while reusing cached host-key lines
  in the PUT payload, full treatment for new/changed instances, removed
  instances propagating out of the mirror with a manual-revoke note
  (clarification Q1), and cache rebuild only after a successful PUT.
* Error handling (FR-027): deployment_id mismatch, rejected saved token
  (401), missing credentials, and the empty-registry guard (FR-016).
"""

from __future__ import annotations

import json
import os
import stat

import pytest

from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    OUTCOME_SKIPPED_BY_DESIGN,
    OUTCOME_SKIPPED_UNREACHABLE,
    OUTCOME_UNCHANGED,
    AdoptError,
    CachedInstance,
    EmptyRegistryError,
    InstanceOutcome,
    MissingCredentialsError,
    SavedCredentials,
    SetupApiError,
    SetupAuthError,
    _adopt_flow,
    _parse_push_cache,
    credentials_path,
    instance_fingerprint,
    load_saved_credentials,
    run_push,
    save_credentials,
)
from remo_cli.models.host import KnownHost

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

URL = "http://web.example:8080"
TOKEN = "s3cret-api-token"
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


def _credentials(push_cache=None) -> SavedCredentials:
    return SavedCredentials(
        url=URL,
        token=TOKEN,
        deployment_id=DEPLOYMENT_ID,
        push_cache=push_cache or {},
    )


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def api_client(mocker):
    """A mocked SetupApiClient wired into the web_adopt module namespace."""
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = TOKEN
    client.get_status.return_value = {"state": "adopted", "registry_instances": 2}
    client.get_identity.return_value = {
        "deployment_id": DEPLOYMENT_ID,
        "public_key": PUBLIC_KEY,
    }
    client.put_registry.return_value = {"registry_instances": 2, "host_key_instances": 1}
    client.post_verify.return_value = {"all_passed": True, "results": []}
    mocker.patch("remo_cli.core.web_adopt.SetupApiClient", return_value=client)
    return client


@pytest.fixture
def registry(mocker):
    """Patch get_known_hosts; tests assign .return_value with their hosts."""
    return mocker.patch("remo_cli.core.web_adopt.get_known_hosts", return_value=[])


def _fake_process_instance(mocker, outcome=OUTCOME_ADOPTED):
    """Patch _process_instance to succeed and record the scanned key line."""

    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        if outcome == OUTCOME_ADOPTED:
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
        return InstanceOutcome(host, outcome, detail="mocked")

    return mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)


# ---------------------------------------------------------------------------
# Saved-credentials lifecycle (FR-025, research R10)
# ---------------------------------------------------------------------------


class TestCredentialsPath:
    def test_lives_under_remo_home(self, tmp_config_dir):
        assert credentials_path() == tmp_config_dir / "web-service.json"


class TestSaveCredentials:
    def test_writes_file_with_0600_perms(self, tmp_config_dir):
        path = save_credentials(_credentials())
        assert path == credentials_path()
        assert path.exists()
        assert _mode(path) == 0o600

    def test_overwrite_tightens_preexisting_0644_file(self, tmp_config_dir):
        path = credentials_path()
        path.write_text("{}")
        path.chmod(0o644)
        save_credentials(_credentials())
        assert _mode(path) == 0o600
        # ...and the content was actually replaced.
        assert json.loads(path.read_text())["url"] == URL

    def test_leaves_no_temp_files_behind(self, tmp_config_dir):
        save_credentials(_credentials())
        assert [p.name for p in tmp_config_dir.iterdir()] == ["web-service.json"]

    def test_creates_missing_parent_directory(self, tmp_config_dir):
        nested = tmp_config_dir / "does-not-exist-yet"
        os.environ["REMO_HOME"] = str(nested)
        path = save_credentials(_credentials())
        assert path.exists()
        assert _mode(path) == 0o600

    def test_round_trip_all_fields(self, tmp_config_dir):
        cache = {
            "node1/dev": CachedInstance(fingerprint="a" * 64, host_keys=[KEY_LINE_NODE1]),
            "web1": CachedInstance(fingerprint="b" * 64, host_keys=[KEY_LINE_WEB1]),
        }
        save_credentials(_credentials(push_cache=cache))
        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.url == URL
        assert loaded.token == TOKEN
        assert loaded.deployment_id == DEPLOYMENT_ID
        assert loaded.push_cache == cache

    def test_round_trip_empty_cache(self, tmp_config_dir):
        save_credentials(_credentials())
        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.push_cache == {}


class TestLoadSavedCredentials:
    def test_absent_file_returns_none(self, tmp_config_dir):
        assert load_saved_credentials() is None

    def test_corrupt_json_returns_none(self, tmp_config_dir):
        credentials_path().write_text("{not json at all")
        assert load_saved_credentials() is None

    def test_non_object_json_returns_none(self, tmp_config_dir):
        credentials_path().write_text('["a", "list"]')
        assert load_saved_credentials() is None

    @pytest.mark.parametrize("missing", ["url", "token", "deployment_id"])
    def test_missing_required_field_returns_none(self, tmp_config_dir, missing):
        data = {"url": URL, "token": TOKEN, "deployment_id": DEPLOYMENT_ID}
        del data[missing]
        credentials_path().write_text(json.dumps(data))
        assert load_saved_credentials() is None

    @pytest.mark.parametrize("bad", [None, 42, ["x"]])
    def test_non_string_required_field_returns_none(self, tmp_config_dir, bad):
        data = {"url": URL, "token": bad, "deployment_id": DEPLOYMENT_ID}
        credentials_path().write_text(json.dumps(data))
        assert load_saved_credentials() is None

    def test_backward_compat_file_without_push_cache(self, tmp_config_dir):
        """Files from before the delta cache load fine with an empty cache."""
        credentials_path().write_text(
            json.dumps({"url": URL, "token": TOKEN, "deployment_id": DEPLOYMENT_ID})
        )
        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.url == URL
        assert loaded.push_cache == {}

    def test_junk_push_cache_entries_are_dropped_not_fatal(self, tmp_config_dir):
        credentials_path().write_text(
            json.dumps(
                {
                    "url": URL,
                    "token": TOKEN,
                    "deployment_id": DEPLOYMENT_ID,
                    "push_cache": {
                        "good": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_NODE1]},
                        "junk": "not-a-dict",
                    },
                }
            )
        )
        loaded = load_saved_credentials()
        assert loaded is not None
        assert set(loaded.push_cache) == {"good"}


class TestParsePushCache:
    def test_non_dict_input_yields_empty_cache(self):
        for raw in (None, "text", 7, ["list"], True):
            assert _parse_push_cache(raw) == {}

    def test_valid_entry_parsed(self):
        cache = _parse_push_cache(
            {"web1": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_WEB1]}}
        )
        assert cache == {"web1": CachedInstance(fingerprint="f" * 64, host_keys=[KEY_LINE_WEB1])}

    def test_non_dict_entry_dropped(self):
        assert _parse_push_cache({"web1": "junk"}) == {}
        assert _parse_push_cache({"web1": ["junk"]}) == {}

    @pytest.mark.parametrize("fingerprint", [None, "", 42, ["x"], {"a": 1}])
    def test_missing_or_invalid_fingerprint_drops_entry(self, fingerprint):
        entry = {"host_keys": [KEY_LINE_WEB1]}
        if fingerprint is not None:
            entry["fingerprint"] = fingerprint
        assert _parse_push_cache({"web1": entry}) == {}

    @pytest.mark.parametrize("host_keys", ["junk", 42, {"a": 1}, [1, 2], ["ok", 3]])
    def test_invalid_host_keys_coerced_to_empty_list(self, host_keys):
        cache = _parse_push_cache({"web1": {"fingerprint": "f" * 64, "host_keys": host_keys}})
        assert cache == {"web1": CachedInstance(fingerprint="f" * 64, host_keys=[])}

    def test_good_entries_survive_next_to_junk(self):
        cache = _parse_push_cache(
            {
                "good": {"fingerprint": "a" * 64, "host_keys": [KEY_LINE_NODE1]},
                "bad-fp": {"fingerprint": "", "host_keys": []},
                "bad-shape": [],
                42: {"fingerprint": "b" * 64, "host_keys": []},
            }
        )
        assert set(cache) == {"good"}


# ---------------------------------------------------------------------------
# Consent semantics (FR-025) — exercised through _adopt_flow with mocks
# ---------------------------------------------------------------------------


class TestAdoptSaveConsent:
    def _run(self, api_client, *, interactive, save):
        return _adopt_flow(
            api_client,
            original_url=URL,
            allow_empty=False,
            interactive=interactive,
            save=save,
        )

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry, mocker):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        self.confirm = mocker.patch("remo_cli.core.web_adopt.confirm", return_value=True)

    def test_yes_alone_never_saves(self, api_client):
        """--yes implies non-interactive; without --save nothing is written."""
        self._run(api_client, interactive=False, save=False)
        assert not credentials_path().exists()
        self.confirm.assert_not_called()

    def test_save_flag_is_explicit_consent(self, api_client):
        self._run(api_client, interactive=False, save=True)
        assert credentials_path().exists()
        assert _mode(credentials_path()) == 0o600
        self.confirm.assert_not_called()

    def test_interactive_yes_saves(self, api_client):
        self.confirm.return_value = True
        self._run(api_client, interactive=True, save=False)
        assert credentials_path().exists()
        self.confirm.assert_called_once()

    def test_interactive_decline_does_not_save(self, api_client):
        self.confirm.return_value = False
        self._run(api_client, interactive=True, save=False)
        assert not credentials_path().exists()

    def test_saved_file_records_service_identity_and_seeded_cache(self, api_client):
        self._run(api_client, interactive=False, save=True)
        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.url == URL
        assert loaded.token == TOKEN
        assert loaded.deployment_id == DEPLOYMENT_ID
        # FR-026: the adopt run seeds the delta cache for the adopted host.
        assert set(loaded.push_cache) == {"node1/dev"}
        assert loaded.push_cache["node1/dev"].fingerprint == instance_fingerprint(_make_host())
        assert loaded.push_cache["node1/dev"].host_keys


# ---------------------------------------------------------------------------
# Fingerprint stability (FR-026)
# ---------------------------------------------------------------------------


class TestInstanceFingerprint:
    def test_same_entry_same_fingerprint(self):
        assert instance_fingerprint(_make_host()) == instance_fingerprint(_make_host())

    def test_is_sha256_hex(self):
        fp = instance_fingerprint(_make_host())
        assert len(fp) == 64
        int(fp, 16)  # raises if not hex

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
# Delta logic via run_push (FR-026, clarification Q1)
# ---------------------------------------------------------------------------


class TestPushDelta:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry):
        self.client = api_client
        self.registry = registry

    def _seed(self, push_cache=None):
        save_credentials(_credentials(push_cache=push_cache))

    def _put_payload(self):
        self.client.put_registry.assert_called_once()
        return self.client.put_registry.call_args.args[0]

    def test_unchanged_instance_skips_keyscan_and_authorize(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed(
            {host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])}
        )
        scan = mocker.patch("remo_cli.core.web_adopt.scan_and_verify_host_key")
        authorize = mocker.patch("remo_cli.core.web_adopt.authorize_service_key")

        result = run_push(interactive=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]
        scan.assert_not_called()
        authorize.assert_not_called()

    def test_unchanged_instance_reuses_cached_lines_in_payload(self, mocker):
        """PUT replaces known_hosts wholesale, so cached lines must be re-sent."""
        host = _make_host()
        self.registry.return_value = [host]
        self._seed(
            {host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])}
        )
        _fake_process_instance(mocker)  # would produce different lines if hit

        run_push(interactive=False)

        payload = self._put_payload()
        assert payload["host_keys"] == {host.name: [KEY_LINE_NODE1]}
        assert [e["name"] for e in payload["registry"]] == [host.name]

    def test_changed_fingerprint_gets_full_treatment(self, mocker):
        host = _make_host(host="10.0.0.99")  # host field changed since last push
        self.registry.return_value = [host]
        stale_fp = instance_fingerprint(_make_host())  # fingerprint of the OLD entry
        assert stale_fp != instance_fingerprint(host)
        self._seed({host.name: CachedInstance(stale_fp, [KEY_LINE_NODE1])})
        process = _fake_process_instance(mocker)

        result = run_push(interactive=False)

        assert process.call_count == 1
        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]
        # Fresh lines from the re-scan, not the stale cached ones.
        assert self._put_payload()["host_keys"][host.name] != [KEY_LINE_NODE1]

    def test_new_instance_gets_full_treatment(self, mocker):
        old = _make_host()
        new = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        self.registry.return_value = [old, new]
        self._seed({old.name: CachedInstance(instance_fingerprint(old), [KEY_LINE_NODE1])})
        process = _fake_process_instance(mocker)

        result = run_push(interactive=False)

        assert {o.host.name: o.outcome for o in result.outcomes} == {
            old.name: OUTCOME_UNCHANGED,
            new.name: OUTCOME_ADOPTED,
        }
        assert process.call_count == 1
        assert process.call_args.args[0] is new

    def test_cached_entry_with_no_host_keys_is_retried_in_full(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed({host.name: CachedInstance(instance_fingerprint(host), [])})
        process = _fake_process_instance(mocker)

        result = run_push(interactive=False)

        assert process.call_count == 1
        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]

    def test_ssm_instance_never_marked_unchanged(self, mocker):
        ssm = _ssm_host()
        self.registry.return_value = [ssm]
        self._seed({ssm.name: CachedInstance(instance_fingerprint(ssm), [KEY_LINE_NODE1])})
        process = _fake_process_instance(mocker, outcome=OUTCOME_SKIPPED_BY_DESIGN)

        result = run_push(interactive=False)

        assert process.call_count == 1
        assert [o.outcome for o in result.outcomes] == [OUTCOME_SKIPPED_BY_DESIGN]
        assert self._put_payload()["host_keys"] == {}

    def test_removed_instance_dropped_from_mirror_with_revoke_note(self, capsys):
        remaining = _make_host()
        self.registry.return_value = [remaining]
        self._seed(
            {
                remaining.name: CachedInstance(
                    instance_fingerprint(remaining), [KEY_LINE_NODE1]
                ),
                "gone-host": CachedInstance("c" * 64, [KEY_LINE_WEB1]),
            }
        )

        run_push(interactive=False)

        payload = self._put_payload()
        assert [e["name"] for e in payload["registry"]] == [remaining.name]
        assert "gone-host" not in payload["host_keys"]
        # Clarification Q1: removal propagates, revocation stays manual.
        out = capsys.readouterr().out
        assert "gone-host" in out
        assert "revoke it manually" in out
        assert "authorized_keys" in out
        # The removed instance is dropped from the rewritten cache.
        loaded = load_saved_credentials()
        assert loaded is not None
        assert set(loaded.push_cache) == {remaining.name}

    def test_cache_rebuilt_after_successful_put(self, mocker):
        unchanged = _make_host()
        fresh = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        flaky = _make_host(type_="hetzner", name="down1", host="5.6.7.9")
        self.registry.return_value = [unchanged, fresh, flaky]
        self._seed(
            {unchanged.name: CachedInstance(instance_fingerprint(unchanged), [KEY_LINE_NODE1])}
        )

        def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
            if host.name == flaky.name:
                return InstanceOutcome(host, OUTCOME_SKIPPED_UNREACHABLE, detail="down")
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfresh"]
            return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

        mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)

        run_push(interactive=False)

        loaded = load_saved_credentials()
        assert loaded is not None
        # unchanged + newly adopted are cached; the skipped one is NOT, so the
        # next push retries it in full.
        assert set(loaded.push_cache) == {unchanged.name, fresh.name}
        assert loaded.push_cache[unchanged.name].host_keys == [KEY_LINE_NODE1]
        assert loaded.push_cache[fresh.name].fingerprint == instance_fingerprint(fresh)

    def test_failed_put_leaves_cache_untouched(self, mocker):
        host = _make_host()
        changed = _make_host(host="10.9.9.9")
        self.registry.return_value = [changed]
        original_cache = {host.name: CachedInstance(instance_fingerprint(host), [KEY_LINE_NODE1])}
        self._seed(original_cache)
        _fake_process_instance(mocker)
        self.client.put_registry.side_effect = SetupApiError("boom", status=500)

        with pytest.raises(SetupApiError):
            run_push(interactive=False)

        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.push_cache == original_cache

    def test_full_mirror_always_put_even_when_everything_unchanged(self, mocker):
        direct = _make_host()
        ssm = _ssm_host()
        self.registry.return_value = [direct, ssm]
        self._seed(
            {direct.name: CachedInstance(instance_fingerprint(direct), [KEY_LINE_NODE1])}
        )
        _fake_process_instance(mocker, outcome=OUTCOME_SKIPPED_BY_DESIGN)

        run_push(interactive=False)

        payload = self._put_payload()
        assert {e["name"] for e in payload["registry"]} == {direct.name, ssm.name}
        assert payload["host_keys"] == {direct.name: [KEY_LINE_NODE1]}


# ---------------------------------------------------------------------------
# Hard failures (FR-027, FR-016) via run_push
# ---------------------------------------------------------------------------


class TestPushErrors:
    def test_missing_credentials_raises(self, tmp_config_dir):
        with pytest.raises(MissingCredentialsError, match="no saved service credentials"):
            run_push(interactive=False)

    def test_unreadable_credentials_file_raises_missing(self, tmp_config_dir):
        credentials_path().write_text("{corrupt")
        with pytest.raises(MissingCredentialsError):
            run_push(interactive=False)

    def test_client_built_from_saved_credentials(self, tmp_config_dir, api_client, registry, mocker):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        save_credentials(_credentials())
        from remo_cli.core import web_adopt

        run_push(interactive=False)
        web_adopt.SetupApiClient.assert_called_once_with(URL, TOKEN)

    def test_deployment_id_mismatch_aborts_with_readopt_guidance(
        self, tmp_config_dir, api_client, registry
    ):
        save_credentials(_credentials())
        api_client.get_identity.return_value = {
            "deployment_id": "dep-NEW00000",
            "public_key": PUBLIC_KEY,
        }
        with pytest.raises(AdoptError, match="remo web adopt"):
            run_push(interactive=False)
        api_client.put_registry.assert_not_called()
        # The stale cache/file is not rewritten by an aborted push.
        loaded = load_saved_credentials()
        assert loaded is not None
        assert loaded.deployment_id == DEPLOYMENT_ID

    def test_rejected_token_raises_auth_error_with_readopt_guidance(
        self, tmp_config_dir, api_client, registry
    ):
        save_credentials(_credentials())
        api_client.get_identity.side_effect = SetupAuthError("nope", status=401)
        with pytest.raises(SetupAuthError, match="remo web adopt") as excinfo:
            run_push(interactive=False)
        assert excinfo.value.status == 401
        api_client.put_registry.assert_not_called()

    def test_missing_public_key_aborts(self, tmp_config_dir, api_client, registry):
        save_credentials(_credentials())
        api_client.get_identity.return_value = {
            "deployment_id": DEPLOYMENT_ID,
            "public_key": "",
        }
        with pytest.raises(AdoptError, match="no public key"):
            run_push(interactive=False)
        api_client.put_registry.assert_not_called()

    def test_empty_registry_guard(self, tmp_config_dir, api_client, registry):
        save_credentials(_credentials())
        registry.return_value = []
        with pytest.raises(EmptyRegistryError, match="--allow-empty"):
            run_push(interactive=False)
        api_client.put_registry.assert_not_called()

    def test_allow_empty_bypasses_guard(self, tmp_config_dir, api_client, registry):
        save_credentials(_credentials())
        registry.return_value = []
        result = run_push(interactive=False, allow_empty=True)
        assert result.outcomes == []
        payload = api_client.put_registry.call_args.args[0]
        assert payload["registry"] == []
        # allow_empty must be forwarded so the SERVICE-side guard is bypassed too.
        assert api_client.put_registry.call_args.kwargs["allow_empty"] is True
