"""Unit tests for the unified `remo web push` flow (017-web-adopt-simplify).

Covers the non-secret, deployment-keyed push cache (now format v3) and the
delta/force/flap/revocation logic of the single `run_push` path:

* Push cache v3 lifecycle: atomic 0600 writes, nested `{mirror_generation,
  instances}` per deployment, connection-tuple persistence, junk entries
  dropped, absent/corrupt -> {}, and the "v2/unversioned file treated as empty"
  graceful-degradation case.
* Fingerprint stability (unchanged): any field change re-fingerprints.
* Unified flow: first push seeds the cache; a re-run skips unchanged instances.
* Delta logic: unchanged instances skip keyscan/authorize and reuse cached
  lines; new/changed get full treatment; cache rebuilt only after a good PUT.
* `--force`: bypasses the unchanged fast-path (US4).
* Best-effort revocation of removed instances (US3).
* Multi-workstation flap detection (US5).
* Errors: mount_configured, missing public key, empty-registry guard, dormant 404.
"""

from __future__ import annotations

import json
import stat

import pytest

from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    OUTCOME_SKIPPED_BY_DESIGN,
    OUTCOME_SKIPPED_UNREACHABLE,
    OUTCOME_UNCHANGED,
    REVOKE_FAILED,
    REVOKE_OK,
    AdoptError,
    CachedInstance,
    DeploymentCache,
    EmptyRegistryError,
    InstanceOutcome,
    MountConfiguredError,
    SetupApiError,
    SetupNotFoundError,
    build_revoke_command,
    instance_fingerprint,
    load_push_cache,
    push_cache_path,
    run_adopt,
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


def _cached(host: KnownHost, lines: list[str]) -> CachedInstance:
    """A v3 cache entry for *host* with a full (direct-access) connection tuple."""
    return CachedInstance(
        fingerprint=instance_fingerprint(host),
        host_keys=list(lines),
        host=host.host,
        user=host.user,
        access=host.access_mode or "direct",
        type=host.type,
    )


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = CODE
    client.get_status.return_value = {
        "state": "adopted",
        "registry_instances": 2,
        "payload_versions": [1, 2],
    }
    client.get_identity.return_value = {"deployment_id": DEPLOYMENT_ID, "public_key": PUBLIC_KEY}
    client.put_registry.return_value = {
        "registry_instances": 2,
        "host_key_instances": 1,
        "mirror_generation": 1,
    }
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
# Push cache v3 lifecycle (T005)
# ---------------------------------------------------------------------------


class TestPushCacheLifecycle:
    def test_path_under_remo_home(self, tmp_config_dir):
        assert push_cache_path() == tmp_config_dir / "web-service.json"

    def test_version_is_three(self, tmp_config_dir):
        save_push_cache({DEPLOYMENT_ID: DeploymentCache(mirror_generation=1)})
        assert json.loads(push_cache_path().read_text())["cache_version"] == 3

    def test_writes_0600(self, tmp_config_dir):
        path = save_push_cache({DEPLOYMENT_ID: DeploymentCache(mirror_generation=1)})
        assert _mode(path) == 0o600

    def test_no_url_or_token_persisted(self, tmp_config_dir):
        save_push_cache(
            {DEPLOYMENT_ID: DeploymentCache(instances={"n": _cached(_make_host(), [KEY_LINE_NODE1])})}
        )
        text = push_cache_path().read_text()
        assert "token" not in text
        assert "http" not in text  # no url

    def test_round_trip_nested_with_generation_and_tuple(self, tmp_config_dir):
        host = _make_host()
        cache = {
            DEPLOYMENT_ID: DeploymentCache(
                instances={
                    "node1/dev": _cached(host, [KEY_LINE_NODE1]),
                    "web1": CachedInstance("b" * 64, [KEY_LINE_WEB1], host="5.6.7.8", user="remo",
                                           access="direct", type="hetzner"),
                },
                mirror_generation=7,
            ),
            "other-dep": DeploymentCache(mirror_generation=3),
        }
        save_push_cache(cache)
        loaded = load_push_cache()
        assert loaded == cache
        # Connection tuple round-tripped.
        entry = loaded[DEPLOYMENT_ID].instances["node1/dev"]
        assert (entry.host, entry.user, entry.access, entry.type) == (
            "10.0.0.1", "remo", "direct", "incus",
        )
        assert loaded[DEPLOYMENT_ID].mirror_generation == 7

    def test_absent_returns_empty(self, tmp_config_dir):
        assert load_push_cache() == {}

    def test_corrupt_returns_empty(self, tmp_config_dir):
        push_cache_path().write_text("{not json")
        assert load_push_cache() == {}

    def test_v2_file_treated_as_empty(self, tmp_config_dir):
        # A cache_version: 2 file (the pre-017 shape: deployment -> {name -> entry},
        # no mirror_generation/instances nesting) forces a full re-verify push.
        push_cache_path().write_text(
            json.dumps(
                {
                    "cache_version": 2,
                    "push_cache": {
                        DEPLOYMENT_ID: {
                            "node1/dev": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_NODE1]}
                        }
                    },
                }
            )
        )
        assert load_push_cache() == {}

    def test_unversioned_file_treated_as_empty(self, tmp_config_dir):
        push_cache_path().write_text(json.dumps({"push_cache": {DEPLOYMENT_ID: {}}}))
        assert load_push_cache() == {}

    def test_old_011_credential_format_ignored(self, tmp_config_dir):
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
                    "cache_version": 3,
                    "push_cache": {
                        DEPLOYMENT_ID: {
                            "mirror_generation": 4,
                            "instances": {
                                "good": {"fingerprint": "f" * 64, "host_keys": [KEY_LINE_NODE1]},
                                "bad": "not-a-dict",
                            },
                        },
                        "all-junk-dep": {"instances": {"y": "not-a-dict", "z": ["list"]}},
                        "empty-dep": {"instances": {}},
                    },
                }
            )
        )
        loaded = load_push_cache()
        # good deployment kept (has a valid instance); all-junk kept only if it
        # has a generation (it has none) -> dropped; empty-dep dropped.
        assert set(loaded) == {DEPLOYMENT_ID}
        assert set(loaded[DEPLOYMENT_ID].instances) == {"good"}
        assert loaded[DEPLOYMENT_ID].mirror_generation == 4


# ---------------------------------------------------------------------------
# Fingerprint stability (unchanged)
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
            {"access_mode": "ssm"},
        ],
    )
    def test_any_field_change_changes_fingerprint(self, change):
        assert instance_fingerprint(_make_host()) != instance_fingerprint(_make_host(**change))


# ---------------------------------------------------------------------------
# Unified flow: first push seeds the cache (adopt-or-resync, one path)
# ---------------------------------------------------------------------------


class TestUnifiedFirstPushSeedsCache:
    def test_first_push_seeds_deployment_keyed_cache_v3(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        run_push(URL, CODE, interactive=False)
        loaded = load_push_cache()
        assert set(loaded) == {DEPLOYMENT_ID}
        dep = loaded[DEPLOYMENT_ID]
        assert set(dep.instances) == {"node1/dev"}
        assert dep.instances["node1/dev"].fingerprint == instance_fingerprint(_make_host())
        # Generation returned by the PUT is recorded for flap detection.
        assert dep.mirror_generation == 1

    def test_run_adopt_is_alias_for_run_push(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        registry.return_value = [_make_host()]
        _fake_process_instance(mocker)
        result = run_adopt(URL, CODE, interactive=False)
        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]
        assert load_push_cache()[DEPLOYMENT_ID].instances["node1/dev"]

    def test_ssm_instance_is_cached_so_status_does_not_report_it_as_new(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        """Regression: an SSM instance ends `skipped_by_design` (no keyscan/
        authorize) but its registry entry IS mirrored on every push, so it must
        be cached too — otherwise `remo web status` reports it as perpetually
        `new`. Exercises the real _cache_from_outcomes SSM path, not a hand-seeded
        cache, so a regression that stops caching SSM would fail here."""
        from remo_cli.core.web_drift import (  # noqa: PLC0415
            DriftState,
            diff_registry_against_cache,
        )

        direct = _make_host()
        ssm = _ssm_host()
        registry.return_value = [direct, ssm]

        def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
            if host.access_mode == "ssm":
                return InstanceOutcome(host, OUTCOME_SKIPPED_BY_DESIGN, detail="ssm")
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
            return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

        mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)

        run_push(URL, CODE, interactive=False)

        cached = load_push_cache()[DEPLOYMENT_ID].instances
        assert set(cached) == {direct.name, ssm.name}
        # SSM carries no host keys (never keyscanned) but keeps a fingerprint...
        assert cached[ssm.name].host_keys == []
        assert cached[ssm.name].access == "ssm"
        assert cached[ssm.name].fingerprint == instance_fingerprint(ssm)
        # ...so an unchanged SSM instance reads as in_sync, not new.
        drift = {
            e.name: e.state for e in diff_registry_against_cache([direct, ssm], cached)
        }
        assert drift == {direct.name: DriftState.IN_SYNC, ssm.name: DriftState.IN_SYNC}


# ---------------------------------------------------------------------------
# Delta logic via run_push
# ---------------------------------------------------------------------------


class TestPushDelta:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry):
        self.client = api_client
        self.registry = registry

    def _seed(self, instances, generation=1):
        save_push_cache(
            {DEPLOYMENT_ID: DeploymentCache(instances=instances, mirror_generation=generation)}
        )

    def _put_payload(self):
        self.client.put_registry.assert_called_once()
        return self.client.put_registry.call_args.args[0]

    def test_unchanged_instance_skips_keyscan_and_authorize(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed({host.name: _cached(host, [KEY_LINE_NODE1])})
        scan = mocker.patch("remo_cli.core.web_adopt.scan_and_verify_host_key")
        authorize = mocker.patch("remo_cli.core.web_adopt.authorize_service_key")

        result = run_push(URL, CODE, interactive=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]
        scan.assert_not_called()
        authorize.assert_not_called()

    def test_unchanged_instance_reuses_cached_lines(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed({host.name: _cached(host, [KEY_LINE_NODE1])})
        _fake_process_instance(mocker)

        run_push(URL, CODE, interactive=False)

        payload = self._put_payload()
        assert payload["host_keys"] == {host.name: [KEY_LINE_NODE1]}
        # The payload carries a workstation label for the flap marker (US5).
        assert "workstation" in payload

    def test_changed_fingerprint_gets_full_treatment(self, mocker):
        host = _make_host(host="10.0.0.99")
        self.registry.return_value = [host]
        stale = _cached(_make_host(), [KEY_LINE_NODE1])
        self._seed({host.name: stale})
        _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]
        assert self._put_payload()["host_keys"][host.name] != [KEY_LINE_NODE1]

    def test_new_instance_gets_full_treatment(self, mocker):
        old = _make_host()
        new = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        self.registry.return_value = [old, new]
        self._seed({old.name: _cached(old, [KEY_LINE_NODE1])})
        process = _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False)

        assert {o.host.name: o.outcome for o in result.outcomes} == {
            old.name: OUTCOME_UNCHANGED,
            new.name: OUTCOME_ADOPTED,
        }
        assert process.call_count == 1

    def test_cache_rebuilt_after_successful_put(self, mocker):
        unchanged = _make_host()
        fresh = _make_host(type_="hetzner", name="web1", host="5.6.7.8")
        flaky = _make_host(type_="hetzner", name="down1", host="5.6.7.9")
        self.registry.return_value = [unchanged, fresh, flaky]
        self._seed({unchanged.name: _cached(unchanged, [KEY_LINE_NODE1])})

        def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
            if host.name == flaky.name:
                return InstanceOutcome(host, OUTCOME_SKIPPED_UNREACHABLE, detail="down")
            host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfresh"]
            return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

        mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)

        run_push(URL, CODE, interactive=False)

        loaded = load_push_cache()
        assert set(loaded[DEPLOYMENT_ID].instances) == {unchanged.name, fresh.name}

    def test_failed_put_leaves_cache_untouched(self, mocker):
        host = _make_host()
        changed = _make_host(host="10.9.9.9")
        self.registry.return_value = [changed]
        original = {host.name: _cached(host, [KEY_LINE_NODE1])}
        self._seed(original, generation=5)
        _fake_process_instance(mocker)
        self.client.put_registry.side_effect = SetupApiError("boom", status=500)

        with pytest.raises(SetupApiError):
            run_push(URL, CODE, interactive=False)

        assert load_push_cache() == {
            DEPLOYMENT_ID: DeploymentCache(instances=original, mirror_generation=5)
        }


# ---------------------------------------------------------------------------
# --force full re-authorization (T029, US4)
# ---------------------------------------------------------------------------


class TestForce:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry):
        self.client = api_client
        self.registry = registry

    def _seed(self, host):
        save_push_cache(
            {DEPLOYMENT_ID: DeploymentCache(instances={host.name: _cached(host, [KEY_LINE_NODE1])})}
        )

    def test_force_reprocesses_unchanged_instance(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed(host)
        process = _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False, force=True)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_ADOPTED]
        assert process.call_count == 1  # full path, not unchanged

    def test_no_force_preserves_unchanged_fast_path(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed(host)
        process = _fake_process_instance(mocker)

        result = run_push(URL, CODE, interactive=False, force=False)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]
        process.assert_not_called()

    def test_force_per_instance_failure_still_non_fatal(self, mocker):
        host = _make_host()
        self.registry.return_value = [host]
        self._seed(host)
        mocker.patch(
            "remo_cli.core.web_adopt._process_instance",
            side_effect=lambda h, pk, **kw: InstanceOutcome(
                h, OUTCOME_SKIPPED_UNREACHABLE, detail="down"
            ),
        )

        result = run_push(URL, CODE, interactive=False, force=True)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_SKIPPED_UNREACHABLE]
        self.client.put_registry.assert_called_once()  # push still completes


# ---------------------------------------------------------------------------
# Best-effort revocation of removed instances (T022, US3)
# ---------------------------------------------------------------------------


class TestRevocation:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry):
        self.client = api_client
        self.registry = registry

    def test_removed_direct_instance_is_revoked(self, mocker):
        remaining = _make_host()
        gone = _make_host(type_="hetzner", name="gone", host="5.6.7.8")
        self.registry.return_value = [remaining]
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        remaining.name: _cached(remaining, [KEY_LINE_NODE1]),
                        gone.name: _cached(gone, [KEY_LINE_WEB1]),
                    }
                )
            }
        )
        revoke = mocker.patch(
            "remo_cli.core.web_adopt.revoke_service_key", return_value=(True, "")
        )

        result = run_push(URL, CODE, interactive=False)

        revoke.assert_called_once()
        revoked_host = revoke.call_args.args[0]
        assert revoked_host.host == "5.6.7.8"
        assert [(r.name, r.result) for r in result.revocations] == [("gone", REVOKE_OK)]
        # Removed instance drops out of the rebuilt cache.
        assert set(load_push_cache()[DEPLOYMENT_ID].instances) == {remaining.name}

    def test_ssm_removed_instance_is_not_revoked(self, mocker):
        remaining = _make_host()
        ssm = _ssm_host()
        self.registry.return_value = [remaining]
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        remaining.name: _cached(remaining, [KEY_LINE_NODE1]),
                        ssm.name: CachedInstance(
                            "s" * 64, host=ssm.host, user=ssm.user, access="ssm", type="aws"
                        ),
                    }
                )
            }
        )
        revoke = mocker.patch("remo_cli.core.web_adopt.revoke_service_key")

        result = run_push(URL, CODE, interactive=False)

        revoke.assert_not_called()
        outcome = {r.name: r.result for r in result.revocations}
        assert outcome[ssm.name] == REVOKE_FAILED

    def test_removed_without_connection_tuple_could_not_revoke(self, mocker):
        remaining = _make_host()
        self.registry.return_value = [remaining]
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        remaining.name: _cached(remaining, [KEY_LINE_NODE1]),
                        # No connection tuple (older-style entry) -> can't revoke.
                        "legacy-gone": CachedInstance("c" * 64, [KEY_LINE_WEB1]),
                    }
                )
            }
        )
        revoke = mocker.patch("remo_cli.core.web_adopt.revoke_service_key")

        result = run_push(URL, CODE, interactive=False)

        revoke.assert_not_called()
        assert {r.name: r.result for r in result.revocations}["legacy-gone"] == REVOKE_FAILED

    def test_revocation_failure_is_non_fatal(self, mocker, capsys):
        remaining = _make_host()
        gone = _make_host(type_="hetzner", name="gone", host="5.6.7.8")
        self.registry.return_value = [remaining]
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        remaining.name: _cached(remaining, [KEY_LINE_NODE1]),
                        gone.name: _cached(gone, [KEY_LINE_WEB1]),
                    }
                )
            }
        )
        mocker.patch(
            "remo_cli.core.web_adopt.revoke_service_key",
            return_value=(False, "SSH timed out after 30s"),
        )

        result = run_push(URL, CODE, interactive=False)

        # Push still completed (PUT happened), revocation reported could_not_revoke.
        self.client.put_registry.assert_called_once()
        assert {r.name: r.result for r in result.revocations} == {"gone": REVOKE_FAILED}
        out = capsys.readouterr().out
        assert "could_not_revoke" in out and "revoke manually" in out


class TestBuildRevokeCommand:
    def test_filters_on_marker_only(self):
        cmd = build_revoke_command()
        assert "grep -vF" in cmd and "remo-web@" in cmd
        # Never appends a key — pure removal.
        assert "printf" not in cmd

    def test_tolerates_missing_file_and_is_atomic(self):
        cmd = build_revoke_command()
        assert "[ -f ~/.ssh/authorized_keys ] || exit 0" in cmd
        assert "mktemp ~/.ssh/.authorized_keys.remo.XXXXXX" in cmd
        assert 'mv "$tmp" ~/.ssh/authorized_keys' in cmd
        assert 'chmod 600 "$tmp"' in cmd


# ---------------------------------------------------------------------------
# Multi-workstation flap detection (T033, US5)
# ---------------------------------------------------------------------------


class TestFlapDetection:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_config_dir, api_client, registry, mocker):
        self.client = api_client
        self.registry = registry
        self.registry.return_value = [_make_host()]
        _fake_process_instance(mocker)

    def _seed_generation(self, generation):
        host = _make_host()
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={host.name: _cached(host, [KEY_LINE_NODE1])},
                    mirror_generation=generation,
                )
            }
        )

    def _server_generation(self, generation, workstation="hostA/paul"):
        self.client.get_status.return_value = {
            "state": "adopted",
            "registry_instances": 1,
            "payload_versions": [1, 2],
            "mirror_generation": generation,
            "last_push": {"at": "2026-07-26T12:00:00Z", "workstation": workstation},
        }

    def test_no_warning_when_server_has_no_generation(self, capsys):
        # First-ever push to a fresh service: no marker -> no warning.
        run_push(URL, CODE, interactive=False)
        assert "last updated elsewhere" not in capsys.readouterr().out

    def test_no_warning_when_server_not_ahead(self, capsys):
        self._seed_generation(5)
        self._server_generation(5)
        run_push(URL, CODE, interactive=False)
        assert "last updated elsewhere" not in capsys.readouterr().out

    def test_warns_when_server_ahead_and_proceeds_non_interactive(self, capsys):
        # Workstation B: no cache entry (cached_gen=0), server advanced to 3.
        self._server_generation(3, workstation="hostA/alice")
        run_push(URL, CODE, interactive=False)  # --yes semantics: warn + proceed
        out = capsys.readouterr().out
        assert "last updated elsewhere" in out
        assert "hostA/alice" in out
        self.client.put_registry.assert_called_once()  # proceeded

    def test_warns_when_server_ahead_of_cached_generation(self, capsys):
        self._seed_generation(2)
        self._server_generation(4)
        run_push(URL, CODE, interactive=False)
        assert "last updated elsewhere" in capsys.readouterr().out

    def test_interactive_abort_declines_the_push(self, mocker):
        self._server_generation(3)
        mocker.patch("remo_cli.core.web_adopt.confirm", return_value=False)
        with pytest.raises(AdoptError, match="another"):
            run_push(URL, CODE, interactive=True)
        self.client.put_registry.assert_not_called()

    def test_interactive_confirm_proceeds(self, mocker):
        self._server_generation(3)
        mocker.patch("remo_cli.core.web_adopt.confirm", return_value=True)
        run_push(URL, CODE, interactive=True)
        self.client.put_registry.assert_called_once()

    def test_cached_generation_updated_from_put_response(self):
        self._seed_generation(1)
        self._server_generation(1)
        self.client.put_registry.return_value = {
            "registry_instances": 1,
            "host_key_instances": 1,
            "mirror_generation": 2,
        }
        run_push(URL, CODE, interactive=False)
        assert load_push_cache()[DEPLOYMENT_ID].mirror_generation == 2


# ---------------------------------------------------------------------------
# Hard failures via run_push
# ---------------------------------------------------------------------------


class TestPushErrors:
    def test_client_built_from_supplied_url_and_code(
        self, tmp_config_dir, api_client, registry, mocker
    ):
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
        api_client.get_identity.side_effect = SetupNotFoundError("dormant", status=404)
        with pytest.raises(SetupNotFoundError):
            run_push(URL, CODE, interactive=False)
