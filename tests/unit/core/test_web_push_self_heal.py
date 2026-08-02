"""`remo web push` self-heals a host-side loss of the service key (#122).

The `unchanged` fast path decides "skip keyscan/authorize" from the local push
cache — a record of what this workstation last *sent*. That is a correct answer
to "did the registry entry change" and a wrong proxy for "is the service still
authorized on this instance", which is the question the skip actually turns on.
When an instance's `remo-web@…` line is removed host-side (a provisioning pass
rewriting `authorized_keys`, see #121), push reported `unchanged`, changed
nothing, and then printed the `auth_failed` its own verification step had just
found — so the one command that repairs the instance could not.

Push now treats verification as the authority: an instance skipped as
`unchanged` that verifies `auth_failed` is re-authorized, re-PUT and re-verified
within the same run. Covered here:

* skip-then-verify-passes: the fast path is preserved, nothing is reprocessed;
* skip-then-verify-fails: the instance is reprocessed and reported `repaired`;
* an instance that was genuinely processed this run is not reprocessed;
* a still-failing instance is kept out of the push cache, so the next push
  retries it in full instead of skipping it again;
* the remediation names `remo web push --force <url>` verbatim.
"""

from __future__ import annotations

import pytest

from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    OUTCOME_REPAIRED,
    OUTCOME_SKIPPED_UNREACHABLE,
    OUTCOME_UNCHANGED,
    CachedInstance,
    DeploymentCache,
    InstanceOutcome,
    auth_failed_labels,
    instance_fingerprint,
    load_push_cache,
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
CACHED_LINE = "10.0.0.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICachedKeyForTests"
RESCANNED_LINE = "10.0.0.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIRescannedKeyForTests"


def _host(name: str = "node1/dev", host: str = "10.0.0.1") -> KnownHost:
    return KnownHost(type="incus", name=name, host=host, user="remo")


def _cached(h: KnownHost, lines: list[str]) -> CachedInstance:
    return CachedInstance(
        fingerprint=instance_fingerprint(h),
        host_keys=list(lines),
        host=h.host,
        user=h.user,
        access=h.access_mode or "direct",
        type=h.type,
    )


def _instance_check(label: str, *, passed: bool, detail: str = "ok") -> dict:
    return {
        "name": f"instance {label}",
        "passed": passed,
        "detail": detail,
        "remediation": None if passed else "Re-authorize the service on this instance.",
    }


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = CODE
    client.get_status.return_value = {
        "state": "adopted",
        "registry_instances": 1,
        "payload_versions": [1, 2],
    }
    client.get_identity.return_value = {"deployment_id": DEPLOYMENT_ID, "public_key": PUBLIC_KEY}
    client.put_registry.return_value = {
        "registry_instances": 1,
        "host_key_instances": 1,
        "mirror_generation": 2,
    }
    mocker.patch("remo_cli.core.web_adopt.SetupApiClient", return_value=client)
    return client


@pytest.fixture
def registry(mocker):
    return mocker.patch("remo_cli.core.web_adopt.get_known_hosts", return_value=[])


def _seed_cache(host: KnownHost, lines: list[str] = [CACHED_LINE]) -> None:
    cache = load_push_cache()
    cache[DEPLOYMENT_ID] = DeploymentCache(
        instances={host.name: _cached(host, lines)}, mirror_generation=1
    )
    save_push_cache(cache)


def _patch_process(mocker, outcome: str = OUTCOME_ADOPTED, line: str = RESCANNED_LINE):
    """Stand in for keyscan+authorize, recording which hosts it was asked to do."""
    calls: list[str] = []

    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        calls.append(host.name)
        if outcome == OUTCOME_ADOPTED:
            host_keys[host.name] = [line]
        return InstanceOutcome(host, outcome, detail="")

    mocker.patch("remo_cli.core.web_adopt._process_instance", side_effect=fake)
    return calls


class TestAuthFailedLabels:
    def test_picks_out_failed_instance_checks(self):
        verify = {
            "all_passed": False,
            "results": [
                {"name": "registry", "passed": True, "detail": "readable"},
                _instance_check("incus/node1/dev", passed=False, detail="auth_failed"),
                _instance_check("incus/other", passed=False, detail="unreachable"),
                _instance_check("incus/fine", passed=True),
            ],
        }
        assert auth_failed_labels(verify) == {"incus/node1/dev"}

    def test_non_instance_failures_are_ignored(self):
        """Only per-instance checks name something push can re-authorize."""
        verify = {"results": [{"name": "ssh_identity", "passed": False, "detail": "auth_failed"}]}
        assert auth_failed_labels(verify) == set()

    @pytest.mark.parametrize("verify", [{}, {"results": None}, {"results": []}])
    def test_missing_or_malformed_results_are_tolerated(self, verify):
        assert auth_failed_labels(verify) == set()


class TestFastPathPreserved:
    """The optimization survives: a healthy `unchanged` instance is not touched."""

    def test_verify_passes_so_nothing_is_reprocessed(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        api_client.post_verify.return_value = {
            "all_passed": True,
            "results": [_instance_check("incus/node1/dev", passed=True)],
        }
        calls = _patch_process(mocker)

        result = run_push(URL, CODE, assume_yes=True)

        assert calls == []
        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]
        assert api_client.put_registry.call_count == 1
        assert api_client.post_verify.call_count == 1

    def test_unrelated_failure_does_not_trigger_a_repair(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        """`unreachable` is not something re-authorizing can fix."""
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        api_client.post_verify.return_value = {
            "all_passed": False,
            "results": [
                _instance_check("incus/node1/dev", passed=False, detail="unreachable")
            ],
        }
        calls = _patch_process(mocker)

        result = run_push(URL, CODE, assume_yes=True)

        assert calls == []
        assert [o.outcome for o in result.outcomes] == [OUTCOME_UNCHANGED]


class TestSelfHeal:
    """skip-then-verify-fails: the acceptance case from #122."""

    @pytest.fixture
    def _failing_then_passing(self, api_client):
        api_client.post_verify.side_effect = [
            {
                "all_passed": False,
                "results": [
                    _instance_check("incus/node1/dev", passed=False, detail="auth_failed")
                ],
            },
            {"all_passed": True, "results": [_instance_check("incus/node1/dev", passed=True)]},
        ]

    def test_skipped_instance_is_reauthorized(
        self, tmp_config_dir, api_client, registry, mocker, _failing_then_passing
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        calls = _patch_process(mocker)

        result = run_push(URL, CODE, assume_yes=True)

        assert calls == [host.name], "the instance verification rejected was not reprocessed"
        assert [o.outcome for o in result.outcomes] == [OUTCOME_REPAIRED]

    def test_mirror_is_re_put_with_rescanned_host_keys(
        self, tmp_config_dir, api_client, registry, mocker, _failing_then_passing
    ):
        """A changed host key is one of the ways an instance reads auth_failed,
        so the cached lines are dropped and the rescan's lines are what ship."""
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        assert api_client.put_registry.call_count == 2
        second_payload = api_client.put_registry.call_args_list[1][0][0]
        assert second_payload["host_keys"] == {host.name: [RESCANNED_LINE]}

    def test_report_reflects_the_repaired_state(
        self, tmp_config_dir, api_client, registry, mocker, _failing_then_passing
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        _patch_process(mocker)

        result = run_push(URL, CODE, assume_yes=True)

        assert api_client.post_verify.call_count == 2
        assert result.all_verified, "the returned verify must be the post-repair one"

    def test_repaired_instance_is_cached_for_the_next_push(
        self, tmp_config_dir, api_client, registry, mocker, _failing_then_passing
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        entry = load_push_cache()[DEPLOYMENT_ID].instances[host.name]
        assert entry.host_keys == [RESCANNED_LINE]

    def test_only_the_skipped_instance_is_reprocessed(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        """An instance already processed this run keeps its own outcome: redoing
        authorize would not change anything, and its failure is a real one."""
        skipped, fresh = _host(), _host(name="node2/dev", host="10.0.0.2")
        registry.return_value = [skipped, fresh]
        _seed_cache(skipped)
        api_client.post_verify.side_effect = [
            {
                "all_passed": False,
                "results": [
                    _instance_check("incus/node1/dev", passed=False, detail="auth_failed"),
                    _instance_check("incus/node2/dev", passed=False, detail="auth_failed"),
                ],
            },
            {"all_passed": True, "results": []},
        ]
        calls = _patch_process(mocker)

        result = run_push(URL, CODE, assume_yes=True)

        # node2 processed once on the main pass; node1 once on the repair pass.
        assert calls == [fresh.name, skipped.name]
        by_name = {o.host.name: o.outcome for o in result.outcomes}
        assert by_name == {skipped.name: OUTCOME_REPAIRED, fresh.name: OUTCOME_ADOPTED}

    def test_failed_reauthorization_keeps_the_skip_outcome_honest(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        """If the repair can't reach the instance, say so — don't claim repaired."""
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        api_client.post_verify.side_effect = [
            {
                "all_passed": False,
                "results": [
                    _instance_check("incus/node1/dev", passed=False, detail="auth_failed")
                ],
            },
            {
                "all_passed": False,
                "results": [
                    _instance_check("incus/node1/dev", passed=False, detail="auth_failed")
                ],
            },
        ]
        _patch_process(mocker, outcome=OUTCOME_SKIPPED_UNREACHABLE)

        result = run_push(URL, CODE, assume_yes=True)

        assert [o.outcome for o in result.outcomes] == [OUTCOME_SKIPPED_UNREACHABLE]
        assert not result.all_verified


class TestKnownFailedInstanceIsNotCached:
    """The cache must not re-arm the fast path over a known-broken instance."""

    def test_still_failing_instance_drops_out_of_the_cache(
        self, tmp_config_dir, api_client, registry, mocker
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        failing = {
            "all_passed": False,
            "results": [_instance_check("incus/node1/dev", passed=False, detail="auth_failed")],
        }
        api_client.post_verify.side_effect = [failing, failing]
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        assert load_push_cache()[DEPLOYMENT_ID].instances == {}, (
            "a cached entry would make the next push skip this instance as unchanged, "
            "which is exactly the loop #122 describes"
        )

    def test_generation_is_still_recorded(self, tmp_config_dir, api_client, registry, mocker):
        """Dropping the instance must not lose the flap-detection marker."""
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        failing = {
            "all_passed": False,
            "results": [_instance_check("incus/node1/dev", passed=False, detail="auth_failed")],
        }
        api_client.post_verify.side_effect = [failing, failing]
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        assert load_push_cache()[DEPLOYMENT_ID].mirror_generation == 2


class TestRemediationNamesTheCommand:
    def test_auth_failed_line_names_force_push_with_the_url(
        self, tmp_config_dir, api_client, registry, mocker, capsys
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        failing = {
            "all_passed": False,
            "results": [_instance_check("incus/node1/dev", passed=False, detail="auth_failed")],
        }
        api_client.post_verify.side_effect = [failing, failing]
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        assert f"remo web push --force {URL}" in capsys.readouterr().out

    def test_other_failures_do_not_suggest_force(
        self, tmp_config_dir, api_client, registry, mocker, capsys
    ):
        host = _host()
        registry.return_value = [host]
        _seed_cache(host)
        api_client.post_verify.return_value = {
            "all_passed": False,
            "results": [
                _instance_check("incus/node1/dev", passed=False, detail="unreachable")
            ],
        }
        _patch_process(mocker)

        run_push(URL, CODE, assume_yes=True)

        assert "--force" not in capsys.readouterr().out
