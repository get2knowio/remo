"""Unit tests for `core/web_sync.py` (023): the three-way merge + sync driver.

Structure mirrors tests/unit/core/test_reconcile.py (pure plan first, driver
after) and test_web_push.py (mocked SetupApiClient, real temp registry).
"""

from __future__ import annotations

import json

import pytest

from remo_cli.core import registry as core_registry
from remo_cli.core import web_sync
from remo_cli.core.registry import replace_registry
from remo_cli.core.web_adopt import (
    OUTCOME_ADOPTED,
    OUTCOME_PULLED,
    CachedInstance,
    DeploymentCache,
    GenerationConflictError,
    InstanceOutcome,
    instance_fingerprint,
    load_push_cache,
    save_push_cache,
)
from remo_cli.core.web_sync import (
    RESOLUTION_LOCAL,
    RESOLUTION_REMOTE,
    RESOLUTION_SKIP,
    SyncActionKind,
    SyncNameCollisionError,
    build_sync_plan,
    gate_deletion_consent,
    local_mutations,
    merged_hosts,
    parse_remote_registry,
    remote_deletions,
    resolve_conflicts,
    run_web_sync,
)
from remo_cli.models.host import KnownHost

URL = "http://web.example:8080"
CODE = "ephemeral-pairing-code"
DEPLOYMENT_ID = "dep-1234abcd"
PUBLIC_KEY = f"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeServiceKey remo-web@{DEPLOYMENT_ID}"

KEY_LINE_A = "10.0.0.1 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyLocalA0000"
KEY_LINE_B = "10.0.0.2 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyRemoteB000"


def _host(name="dev", host="10.0.0.1", type_="incus", user="remo", **kw) -> KnownHost:
    return KnownHost(type=type_, name=name, host=host, user=user, **kw)


def _entry(h: KnownHost) -> dict:
    return core_registry.known_host_to_entry(h)


def _kinds(plan) -> dict[str, SyncActionKind]:
    return {a.name: a.kind for a in plan.actions}


# ---------------------------------------------------------------------------
# build_sync_plan — the 14-row case table
# ---------------------------------------------------------------------------


class TestCaseTable:
    A = _host.__wrapped__ if hasattr(_host, "__wrapped__") else None

    def test_no_base_local_only_is_push_add(self):
        plan = build_sync_plan({}, [_host()], [], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.PUSH_ADD}

    def test_no_base_remote_only_is_pull_add(self):
        plan = build_sync_plan({}, [], [_host()], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.PULL_ADD}

    def test_no_base_both_equal_is_in_sync(self):
        plan = build_sync_plan({}, [_host()], [_host()], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.IN_SYNC}

    def test_no_base_both_divergent_is_conflict(self):
        # The base-less degraded mode (post-cache-upgrade).
        plan = build_sync_plan({}, [_host()], [_host(host="10.9.9.9")], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.CONFLICT}

    def test_local_unchanged_remote_deleted_is_delete_local(self):
        h = _host()
        plan = build_sync_plan({"dev": _entry(h)}, [h], [], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.DELETE_LOCAL}

    def test_local_edited_remote_deleted_is_conflict(self):
        base = _host()
        edited = _host(host="10.9.9.9")
        plan = build_sync_plan({"dev": _entry(base)}, [edited], [], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.CONFLICT}

    def test_local_deleted_remote_unchanged_is_delete_remote(self):
        h = _host()
        plan = build_sync_plan({"dev": _entry(h)}, [], [h], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.DELETE_REMOTE}

    def test_local_deleted_remote_edited_is_conflict(self):
        base = _host()
        edited = _host(host="10.9.9.9")
        plan = build_sync_plan({"dev": _entry(base)}, [], [edited], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.CONFLICT}

    def test_deleted_on_both_sides_is_both_deleted(self):
        plan = build_sync_plan({"dev": _entry(_host())}, [], [], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.BOTH_DELETED}
        assert merged_hosts(plan) == []

    def test_unchanged_everywhere_is_in_sync(self):
        h = _host()
        plan = build_sync_plan({"dev": _entry(h)}, [h], [h], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.IN_SYNC}

    def test_local_edit_only_is_push_update(self):
        base = _host()
        edited = _host(host="10.9.9.9")
        plan = build_sync_plan({"dev": _entry(base)}, [edited], [base], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.PUSH_UPDATE}

    def test_remote_edit_only_is_pull_update(self):
        base = _host()
        edited = _host(host="10.9.9.9")
        plan = build_sync_plan({"dev": _entry(base)}, [base], [edited], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.PULL_UPDATE}

    def test_convergent_edits_are_in_sync(self):
        base = _host()
        edited = _host(host="10.9.9.9")
        plan = build_sync_plan({"dev": _entry(base)}, [edited], [edited], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.IN_SYNC}

    def test_divergent_edits_are_conflict(self):
        base = _host()
        plan = build_sync_plan(
            {"dev": _entry(base)}, [_host(host="10.1.1.1")], [_host(host="10.2.2.2")], 0
        )
        assert _kinds(plan) == {"dev": SyncActionKind.CONFLICT}

    def test_malformed_base_entry_degrades_to_baseless(self):
        # A cache entry whose `entry` was None: name merges base-less.
        h = _host()
        plan = build_sync_plan({"dev": None}, [h], [h], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.IN_SYNC}

    def test_purity_inputs_untouched(self):
        base = {"dev": _entry(_host())}
        local = [_host(host="10.1.1.1")]
        remote = [_host(host="10.2.2.2")]
        snapshot = json.dumps(base, sort_keys=True)
        build_sync_plan(base, local, remote, 7)
        assert json.dumps(base, sort_keys=True) == snapshot
        assert local[0].host == "10.1.1.1" and remote[0].host == "10.2.2.2"

    def test_cross_type_name_collision_aborts(self):
        two = [_host(type_="incus"), _host(type_="hetzner", host="5.6.7.8")]
        with pytest.raises(SyncNameCollisionError, match="incus"):
            build_sync_plan({}, two, [], 0)
        with pytest.raises(SyncNameCollisionError, match="hetzner"):
            build_sync_plan({}, [], two, 0)

    def test_same_name_type_change_between_sides_is_ordinary(self):
        # A type change is just an unequal entry: remote-only change -> pull,
        # divergent changes on both sides -> conflict.
        base = _host(type_="incus")
        remote_retyped = _host(type_="hetzner", host="5.6.7.8")
        plan = build_sync_plan({"dev": _entry(base)}, [base], [remote_retyped], 0)
        assert _kinds(plan) == {"dev": SyncActionKind.PULL_UPDATE}

        plan = build_sync_plan(
            {"dev": _entry(base)}, [_host(host="10.1.1.1")], [remote_retyped], 0
        )
        assert _kinds(plan) == {"dev": SyncActionKind.CONFLICT}


# ---------------------------------------------------------------------------
# Conflict resolution + consent + projections
# ---------------------------------------------------------------------------


def _conflict_plan():
    base = _host()
    return build_sync_plan(
        {"dev": _entry(base)}, [_host(host="10.1.1.1")], [_host(host="10.2.2.2")], 3
    )


class TestResolution:
    def test_prefer_local_resolves_all(self):
        plan = _conflict_plan()
        assert resolve_conflicts(plan, prefer="local", interactive=False, memo={})
        assert plan.conflicts[0].resolution == RESOLUTION_LOCAL
        assert [h.host for h in merged_hosts(plan)] == ["10.1.1.1"]

    def test_prefer_remote_resolves_all(self):
        plan = _conflict_plan()
        assert resolve_conflicts(plan, prefer="remote", interactive=False, memo={})
        assert [h.host for h in merged_hosts(plan)] == ["10.2.2.2"]

    def test_interactive_l_r_s(self):
        for answer, expected in (("l", RESOLUTION_LOCAL), ("r", RESOLUTION_REMOTE), ("s", RESOLUTION_SKIP)):
            plan = _conflict_plan()
            assert resolve_conflicts(
                plan, prefer=None, interactive=True, memo={}, input_fn=lambda _p, a=answer: a
            )
            assert plan.conflicts[0].resolution == expected

    def test_non_interactive_unresolved_returns_false(self):
        plan = _conflict_plan()
        assert not resolve_conflicts(plan, prefer=None, interactive=False, memo={})

    def test_memo_skips_reprompt(self):
        plan = _conflict_plan()
        calls = []

        def prompter(prompt):
            calls.append(prompt)
            return "l"

        memo = {"dev": RESOLUTION_REMOTE}
        assert resolve_conflicts(plan, prefer=None, interactive=True, memo=memo, input_fn=prompter)
        assert calls == []
        assert plan.conflicts[0].resolution == RESOLUTION_REMOTE

    def test_skip_projects_remote_into_payload_but_not_locally(self):
        plan = _conflict_plan()
        resolve_conflicts(plan, prefer=None, interactive=True, memo={}, input_fn=lambda _p: "s")
        # payload carries the REMOTE entry...
        assert [h.host for h in merged_hosts(plan)] == ["10.2.2.2"]
        # ...but nothing is written locally.
        to_set, to_remove = local_mutations(plan)
        assert to_set == {} and to_remove == set()

    def test_local_pick_on_remote_edit_local_delete_revokes(self):
        # (B, — , R≠B): "local" pick = delete remotely + revoke.
        base = _host()
        plan = build_sync_plan({"dev": _entry(base)}, [], [_host(host="10.2.2.2")], 0)
        resolve_conflicts(plan, prefer="local", interactive=False, memo={})
        assert merged_hosts(plan) == []
        assert [a.name for a in remote_deletions(plan)] == ["dev"]

    def test_remote_pick_on_local_edit_remote_delete_deletes_locally(self):
        # (B, L≠B, —): "remote" pick = delete locally.
        base = _host()
        plan = build_sync_plan({"dev": _entry(base)}, [_host(host="10.1.1.1")], [], 0)
        resolve_conflicts(plan, prefer="remote", interactive=False, memo={})
        to_set, to_remove = local_mutations(plan)
        assert to_remove == {"dev"} and to_set == {}


class TestConsent:
    def _deletion_plan(self):
        keep = _host()
        gone_remote = _host(name="a-gone", host="10.3.3.3")
        gone_local = _host(name="b-gone", host="10.4.4.4", type_="hetzner")
        base = {
            "dev": _entry(keep),
            "a-gone": _entry(gone_remote),
            "b-gone": _entry(gone_local),
        }
        # a-gone deleted locally (-> DELETE_REMOTE); b-gone deleted remotely.
        return build_sync_plan(base, [keep, gone_local], [keep, gone_remote], 0)

    def test_assume_yes_bypasses(self):
        plan = self._deletion_plan()
        assert gate_deletion_consent(plan, assume_yes=True, interactive=False, consented=set())

    def test_non_interactive_without_yes_aborts(self):
        plan = self._deletion_plan()
        assert not gate_deletion_consent(plan, assume_yes=False, interactive=False, consented=set())

    def test_interactive_prompt_lists_both_directions(self, mocker, capsys):
        plan = self._deletion_plan()
        mocker.patch("remo_cli.core.web_sync.confirm", return_value=True)
        consented = set()
        assert gate_deletion_consent(plan, assume_yes=False, interactive=True, consented=consented)
        out = capsys.readouterr().out
        assert "a-gone" in out and "DEPLOYMENT" in out
        assert "b-gone" in out and "WORKSTATION" in out
        assert consented == {"a-gone", "b-gone"}

    def test_decline_aborts(self, mocker):
        plan = self._deletion_plan()
        mocker.patch("remo_cli.core.web_sync.confirm", return_value=False)
        assert not gate_deletion_consent(plan, assume_yes=False, interactive=True, consented=set())

    def test_no_deletions_needs_no_consent(self):
        plan = build_sync_plan({}, [_host()], [], 0)
        assert gate_deletion_consent(plan, assume_yes=False, interactive=False, consented=set())


# ---------------------------------------------------------------------------
# parse_remote_registry
# ---------------------------------------------------------------------------


class TestParseRemote:
    def test_parses_entries_keys_generation(self):
        doc = {
            "entry_version": 2,
            "registry": [_entry(_host())],
            "host_keys": {"dev": [KEY_LINE_A]},
            "mirror_generation": 9,
            "last_change": {"at": "t", "origin": "web", "workstation": None},
        }
        remote = parse_remote_registry(doc)
        assert [h.name for h in remote.hosts] == ["dev"]
        assert remote.host_keys == {"dev": [KEY_LINE_A]}
        assert remote.generation == 9
        assert remote.last_change["origin"] == "web"

    def test_unknown_type_entries_are_invisible(self):
        doc = {
            "registry": [
                _entry(_host()),
                {"type": "martian", "name": "x", "host": "h", "user": "u", "access": "direct"},
            ],
            "host_keys": {},
            "mirror_generation": 1,
        }
        remote = parse_remote_registry(doc)
        assert [h.name for h in remote.hosts] == ["dev"]

    def test_garbage_degrades_to_empty(self):
        remote = parse_remote_registry({"registry": "nope", "host_keys": [], "mirror_generation": "x"})
        assert remote.hosts == [] and remote.host_keys == {} and remote.generation == 0


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(mocker):
    client = mocker.MagicMock()
    client.base_url = URL
    client.token = CODE
    client.get_status.return_value = {
        "state": "adopted",
        "registry_instances": 1,
        "payload_versions": [1, 2, 3],
    }
    client.get_identity.return_value = {
        "deployment_id": DEPLOYMENT_ID,
        "public_key": PUBLIC_KEY,
    }
    client.get_registry.return_value = {
        "entry_version": 2,
        "registry": [],
        "host_keys": {},
        "mirror_generation": 0,
        "last_change": None,
    }
    client.put_registry.return_value = {
        "registry_instances": 1,
        "host_key_instances": 1,
        "mirror_generation": 1,
    }
    client.post_verify.return_value = {"all_passed": True, "results": []}
    mocker.patch("remo_cli.core.web_sync.SetupApiClient", return_value=client)
    return client


@pytest.fixture
def process_instance(mocker):
    def fake(host, public_key, *, interactive, host_keys, known_hosts_file=None):
        host_keys[host.name] = [f"{host.host} ssh-ed25519 AAAAfake{host.name}"]
        return InstanceOutcome(host, OUTCOME_ADOPTED, detail="mocked")

    return mocker.patch("remo_cli.core.web_sync._process_instance", side_effect=fake)


@pytest.fixture(autouse=True)
def _non_tty(mocker):
    mocker.patch("sys.stdin.isatty", return_value=False)


class TestDriver:
    def test_bidirectional_happy_path(self, tmp_config_dir, api_client, process_instance):
        local_only = _host(name="localbox", host="10.0.0.1")
        remote_only = _host(name="webbox", host="10.0.0.2", type_="hetzner", user="remo")
        replace_registry([local_only])
        api_client.get_registry.return_value = {
            "registry": [_entry(remote_only)],
            "host_keys": {"webbox": [KEY_LINE_B]},
            "mirror_generation": 4,
        }

        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 0

        # PULL landed locally; PUSH stayed.
        names = {h.name for h in core_registry.read_registry(readonly=True).hosts}
        assert names == {"localbox", "webbox"}

        # PULL was never keyscanned/authorized.
        processed = [c.args[0].name for c in process_instance.call_args_list]
        assert processed == ["localbox"]

        # The PUT is v3 with base_generation threaded, both entries mirrored,
        # and the pulled lines round-tripped (wholesale known_hosts stays sound).
        payload = api_client.put_registry.call_args.args[0]
        assert payload["version"] == 3
        assert payload["base_generation"] == 4
        assert {e["name"] for e in payload["registry"]} == {"localbox", "webbox"}
        assert payload["host_keys"]["webbox"] == [KEY_LINE_B]
        assert payload["host_keys"]["localbox"]

        # Cache v4: both entries, pulled one carries the remote entry + lines.
        cache = load_push_cache()[DEPLOYMENT_ID]
        assert set(cache.instances) == {"localbox", "webbox"}
        assert cache.instances["webbox"].entry == _entry(remote_only)
        assert cache.instances["webbox"].host_keys == [KEY_LINE_B]
        assert cache.mirror_generation == 1

        # The pairing session was ended.
        api_client.post_end.assert_called_once()

    def test_dry_run_is_gets_only(self, tmp_config_dir, api_client, process_instance):
        replace_registry([_host()])
        before = core_registry.read_registry(readonly=True).hosts

        rc = run_web_sync(URL, CODE, dry_run=True)
        assert rc == 0
        api_client.put_registry.assert_not_called()
        api_client.post_verify.assert_not_called()
        api_client.post_end.assert_not_called()
        process_instance.assert_not_called()
        assert core_registry.read_registry(readonly=True).hosts == before
        assert load_push_cache() == {}

    def test_v3_unsupported_aborts_before_anything(self, tmp_config_dir, api_client, capsys):
        api_client.get_status.return_value = {
            "state": "adopted",
            "registry_instances": 1,
            "payload_versions": [1, 2],
        }
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 1
        api_client.get_registry.assert_not_called()
        api_client.put_registry.assert_not_called()
        assert "does not support bi-directional sync" in capsys.readouterr().err

    def test_unresolved_conflict_non_interactive_is_exit_3_nothing_applied(
        self, tmp_config_dir, api_client, process_instance
    ):
        local = _host(host="10.1.1.1")
        replace_registry([local])
        api_client.get_registry.return_value = {
            "registry": [_entry(_host(host="10.2.2.2"))],
            "host_keys": {},
            "mirror_generation": 2,
        }
        rc = run_web_sync(URL, CODE)
        assert rc == 3
        api_client.put_registry.assert_not_called()
        after = core_registry.read_registry(readonly=True).hosts
        assert [(h.name, h.host) for h in after] == [(local.name, local.host)]

    def test_deletion_without_consent_is_exit_3(self, tmp_config_dir, api_client, process_instance):
        gone = _host(name="gone", host="10.5.5.5")
        replace_registry([], allow_empty=True)
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        "gone": CachedInstance(
                            fingerprint=instance_fingerprint(gone),
                            entry=_entry(gone),
                            host=gone.host,
                            user=gone.user,
                            access="direct",
                            type=gone.type,
                        )
                    },
                    mirror_generation=2,
                )
            }
        )
        api_client.get_registry.return_value = {
            "registry": [_entry(gone)],
            "host_keys": {},
            "mirror_generation": 2,
        }
        rc = run_web_sync(URL, CODE, allow_empty=True)
        assert rc == 3
        api_client.put_registry.assert_not_called()

    def test_delete_remote_revokes_after_put(self, tmp_config_dir, api_client, process_instance, mocker):
        gone = _host(name="gone", host="10.5.5.5")
        keep = _host(name="keep", host="10.0.0.1")
        replace_registry([keep])
        save_push_cache(
            {
                DEPLOYMENT_ID: DeploymentCache(
                    instances={
                        "gone": CachedInstance(
                            fingerprint=instance_fingerprint(gone),
                            entry=_entry(gone),
                            host=gone.host,
                            user=gone.user,
                            access="direct",
                            type=gone.type,
                        ),
                        "keep": CachedInstance(
                            fingerprint=instance_fingerprint(keep),
                            host_keys=[KEY_LINE_A],
                            entry=_entry(keep),
                            host=keep.host,
                            user=keep.user,
                            access="direct",
                            type=keep.type,
                        ),
                    },
                    mirror_generation=2,
                )
            }
        )
        api_client.get_registry.return_value = {
            "registry": [_entry(gone), _entry(keep)],
            "host_keys": {"keep": [KEY_LINE_A]},
            "mirror_generation": 2,
        }
        revoke = mocker.patch(
            "remo_cli.core.web_sync.revoke_service_key", return_value=(True, "ok")
        )
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 0
        assert revoke.call_args.args[0].name == "gone"
        payload = api_client.put_registry.call_args.args[0]
        assert {e["name"] for e in payload["registry"]} == {"keep"}
        # `keep` was unchanged -> fast path, no processing at all.
        process_instance.assert_not_called()
        assert "gone" not in load_push_cache()[DEPLOYMENT_ID].instances

    def test_409_retries_then_converges(self, tmp_config_dir, api_client, process_instance):
        replace_registry([_host()])
        api_client.put_registry.side_effect = [
            GenerationConflictError("moved", current_generation=5, last_change=None),
            {"registry_instances": 1, "host_key_instances": 1, "mirror_generation": 6},
        ]
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 0
        assert api_client.get_registry.call_count == 2
        assert api_client.put_registry.call_count == 2

    def test_409_bounded_at_three_attempts(self, tmp_config_dir, api_client, process_instance, capsys):
        replace_registry([_host()])
        api_client.put_registry.side_effect = GenerationConflictError(
            "moved", current_generation=9, last_change={"origin": "web", "at": "t9"}
        )
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 1
        assert api_client.put_registry.call_count == 3
        err = capsys.readouterr().err
        assert "kept changing" in err and "origin=web" in err

    def test_local_cas_mismatch_is_exit_1_no_put(self, tmp_config_dir, api_client, process_instance, mocker):
        # The planning read sees a stale snapshot; the real file differs when
        # the CAS-guarded mutate runs -> abort before any PUT.
        stale = _host(name="stale", host="10.7.7.7")
        real = _host(name="real", host="10.8.8.8")
        replace_registry([real])
        mocker.patch(
            "remo_cli.core.registry.read_registry",
            return_value=core_registry.RegistryView(
                hosts=[stale], warnings=[], source_format="v2", unknown_entries=0
            ),
        )
        api_client.get_registry.return_value = {
            "registry": [_entry(_host(name="webbox", host="10.0.0.2"))],
            "host_keys": {},
            "mirror_generation": 1,
        }
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 1
        api_client.put_registry.assert_not_called()

    def test_skipped_conflict_keeps_old_cache_base(self, tmp_config_dir, api_client, process_instance, mocker):
        base = _host()
        old_cached = CachedInstance(
            fingerprint=instance_fingerprint(base),
            host_keys=[KEY_LINE_A],
            entry=_entry(base),
            host=base.host,
            user=base.user,
            access="direct",
            type=base.type,
        )
        save_push_cache(
            {DEPLOYMENT_ID: DeploymentCache(instances={"dev": old_cached}, mirror_generation=1)}
        )
        replace_registry([_host(host="10.1.1.1")])
        api_client.get_registry.return_value = {
            "registry": [_entry(_host(host="10.2.2.2"))],
            "host_keys": {"dev": [KEY_LINE_B]},
            "mirror_generation": 1,
        }
        mocker.patch("sys.stdin.isatty", return_value=True)
        mocker.patch("builtins.input", return_value="s")

        rc = run_web_sync(URL, CODE)
        assert rc == 0
        # Local keeps its own edit; the PUT carried the remote entry + lines.
        assert core_registry.read_registry(readonly=True).hosts[0].host == "10.1.1.1"
        payload = api_client.put_registry.call_args.args[0]
        assert payload["registry"][0]["host"] == "10.2.2.2"
        assert payload["host_keys"]["dev"] == [KEY_LINE_B]
        # The cache keeps the OLD base so the conflict re-surfaces next sync.
        assert load_push_cache()[DEPLOYMENT_ID].instances["dev"].entry == _entry(base)

    def test_pulled_identity_that_does_not_resolve_warns(
        self, tmp_config_dir, api_client, process_instance, capsys
    ):
        pulled = KnownHost(
            type="ssh",
            name="mbp",
            host="10.0.0.9",
            user="paul",
            instance_id="22",
            access_mode="direct",
            region="/home/elsewhere/.ssh/nonexistent",
        )
        replace_registry([_host()])
        api_client.get_registry.return_value = {
            "registry": [_entry(pulled)],
            "host_keys": {},
            "mirror_generation": 1,
        }
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 0
        captured = capsys.readouterr()
        assert "does not resolve on this workstation" in captured.out + captured.err

    def test_pulled_trust_prompt_persists_only_on_accept(
        self, tmp_config_dir, api_client, process_instance, mocker
    ):
        pulled = _host(name="webbox", host="10.0.0.2", type_="hetzner")
        replace_registry([_host()])
        api_client.get_registry.return_value = {
            "registry": [_entry(pulled)],
            "host_keys": {"webbox": [KEY_LINE_B]},
            "mirror_generation": 1,
        }
        mocker.patch("sys.stdin.isatty", return_value=True)
        mocker.patch("remo_cli.core.web_sync._render_fingerprints", return_value="FP")
        persist = mocker.patch(
            "remo_cli.core.web_sync._persist_confirmed_host_keys", return_value=None
        )

        confirm_mock = mocker.patch("remo_cli.core.web_sync.confirm", return_value=False)
        assert run_web_sync(URL, CODE) == 0
        persist.assert_not_called()

        # Reset both stores so the pull happens again, this time accepted.
        replace_registry([_host()])
        save_push_cache({})
        confirm_mock.return_value = True
        assert run_web_sync(URL, CODE) == 0
        persist.assert_called_once()
        assert persist.call_args.args[0] == [KEY_LINE_B]

    def test_mount_configured_is_exit_1(self, tmp_config_dir, api_client, capsys):
        api_client.get_status.return_value = {
            "state": "mount_configured",
            "registry_instances": 1,
            "payload_versions": [1, 2, 3],
        }
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 1
        api_client.get_registry.assert_not_called()

    def test_empty_merge_refused_without_allow_empty(self, tmp_config_dir, api_client, capsys):
        replace_registry([], allow_empty=True)
        rc = run_web_sync(URL, CODE, assume_yes=True)
        assert rc == 1
        api_client.put_registry.assert_not_called()
        assert "merged registry is empty" in capsys.readouterr().err
