"""Tests for standing grants — store, matcher, proposer (TA008, Addendum 001)."""

from __future__ import annotations

import asyncio
from datetime import timedelta, timezone

import pytest

from remo_cli.notifier.grants import (
    ArgMatchType,
    Grant,
    GrantLimitReached,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    GrantStore,
    HostMatchType,
    _utcnow,
)
from remo_cli.notifier.models import OperationKind

from .conftest import make_request

INSTANCE = "inst-1"


def _store(max_grants: int = 100, allow_global: bool = True) -> GrantStore:
    return GrantStore(max_grants=max_grants, instance_id=INSTANCE, allow_global_scope=allow_global)


def _grant(predicate: GrantPredicate, scope: GrantScope, ttl: int = 3600) -> Grant:
    return Grant.create(
        predicate=predicate, scope=scope, ttl_seconds=ttl,
        created_by="telegram:t", source_approval_id="a",
    )


# --- predicate matching -----------------------------------------------------
def test_command_exact_match() -> None:
    req = make_request(project="p", operation={"kind": "command", "command": "git", "args": ["push", "origin"]})
    pred = GrantPredicate(kind=OperationKind.command, command="git", args=["push", "origin"], args_match=ArgMatchType.exact)
    assert pred.matches(req) is True
    req2 = make_request(operation={"kind": "command", "command": "git", "args": ["status"]})
    assert pred.matches(req2) is False


def test_command_prefix_match() -> None:
    pred = GrantPredicate(kind=OperationKind.command, command="git", args=["push"], args_match=ArgMatchType.prefix)
    assert pred.matches(make_request(operation={"kind": "command", "command": "git", "args": ["push", "origin", "main"]})) is True
    assert pred.matches(make_request(operation={"kind": "command", "command": "git", "args": ["pull"]})) is False


def test_command_only_prefix_empty_matches_any_args() -> None:
    pred = GrantPredicate(kind=OperationKind.command, command="npm", args=[], args_match=ArgMatchType.prefix)
    assert pred.matches(make_request(operation={"kind": "command", "command": "npm", "args": ["install", "x"]})) is True


def test_command_glob_match() -> None:
    pred = GrantPredicate(kind=OperationKind.command, command="git", args=["push", "*"], args_match=ArgMatchType.glob)
    assert pred.matches(make_request(operation={"kind": "command", "command": "git", "args": ["push", "origin"]})) is True
    assert pred.matches(make_request(operation={"kind": "command", "command": "git", "args": ["push"]})) is False  # len mismatch


def test_network_exact_and_suffix() -> None:
    req = make_request(operation={"kind": "network", "remote_host": "api.github.com", "remote_port": 443})
    assert GrantPredicate(kind=OperationKind.network, host="api.github.com", port=443).matches(req) is True
    assert GrantPredicate(kind=OperationKind.network, host=".github.com", host_match=HostMatchType.suffix, port=443).matches(req) is True
    assert GrantPredicate(kind=OperationKind.network, host=".gitlab.com", host_match=HostMatchType.suffix, port=443).matches(req) is False


def test_network_port_none_means_any() -> None:
    req = make_request(operation={"kind": "network", "remote_host": "h", "remote_port": 8080})
    assert GrantPredicate(kind=OperationKind.network, host="h", port=None).matches(req) is True


def test_file_path_glob_with_workspace() -> None:
    req = make_request(workspace="/home/me/proj", operation={"kind": "file", "path": "/home/me/proj/a/b.txt"})
    assert GrantPredicate(kind=OperationKind.file, paths=["{workspace}/**"]).matches(req) is True
    req2 = make_request(workspace="/home/me/proj", operation={"kind": "file", "path": "/etc/passwd"})
    assert GrantPredicate(kind=OperationKind.file, paths=["{workspace}/**"]).matches(req2) is False


def test_file_unresolved_workspace_fails_closed() -> None:
    req = make_request(operation={"kind": "file", "path": "/x"})  # no workspace on request
    assert GrantPredicate(kind=OperationKind.file, paths=["{workspace}/**"]).matches(req) is False


def test_kind_mismatch_is_false() -> None:
    pred = GrantPredicate(kind=OperationKind.command, command="git")
    assert pred.matches(make_request(operation={"kind": "network", "remote_host": "h"})) is False


def test_policy_rule_rung() -> None:
    req = make_request(policy_rule_name="vcs-push", operation={"kind": "command", "command": "git", "args": ["push"]})
    pred = GrantPredicate(kind=OperationKind.command, policy_rule_name="vcs-push")
    assert pred.matches(req) is True
    req2 = make_request(policy_rule_name="other", operation={"kind": "command", "command": "git", "args": ["push"]})
    assert pred.matches(req2) is False


# --- scope matching ---------------------------------------------------------
def test_scope_project_equality_and_missing() -> None:
    sc = GrantScope(type=GrantScopeType.project, value="p")
    assert sc.matches(make_request(project="p"), instance_id=INSTANCE) is True
    assert sc.matches(make_request(project="q"), instance_id=INSTANCE) is False
    assert sc.matches(make_request(), instance_id=INSTANCE) is False  # missing → fail-closed


def test_scope_global_always() -> None:
    assert GrantScope(type=GrantScopeType.glob).matches(make_request(), instance_id=INSTANCE) is True


def test_scope_instance_fallback() -> None:
    sc = GrantScope(type=GrantScopeType.instance, value=INSTANCE)
    # request lacks instance_id → falls back to configured instance id (exception to fail-closed)
    assert sc.matches(make_request(), instance_id=INSTANCE) is True


# --- store ------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_match_and_usage() -> None:
    store = _store()
    g = _grant(GrantPredicate(kind=OperationKind.command, command="git", args=[], args_match=ArgMatchType.prefix),
               GrantScope(type=GrantScopeType.project, value="p"))
    await store.create(g)
    req = make_request(project="p", operation={"kind": "command", "command": "git", "args": ["status"]})
    matched = store.match(req)
    assert matched is g
    assert g.uses_count == 1 and g.last_used_at is not None


@pytest.mark.asyncio
async def test_paused_returns_none() -> None:
    store = _store()
    await store.create(_grant(GrantPredicate(kind=OperationKind.command, command="git", args=[], args_match=ArgMatchType.prefix),
                              GrantScope(type=GrantScopeType.glob)))
    store.set_paused(True)
    assert store.match(make_request(operation={"kind": "command", "command": "git", "args": ["x"]})) is None


@pytest.mark.asyncio
async def test_expired_never_matches() -> None:
    store = _store()
    g = _grant(GrantPredicate(kind=OperationKind.command, command="git", args=[], args_match=ArgMatchType.prefix),
               GrantScope(type=GrantScopeType.glob), ttl=-1)  # already expired
    await store.create(g)
    assert store.match(make_request(operation={"kind": "command", "command": "git", "args": ["x"]})) is None


@pytest.mark.asyncio
async def test_revoke() -> None:
    store = _store()
    g = _grant(GrantPredicate(kind=OperationKind.command, command="git", args=[], args_match=ArgMatchType.prefix),
               GrantScope(type=GrantScopeType.glob))
    await store.create(g)
    assert await store.revoke(g.grant_id) is True
    assert await store.revoke(g.grant_id) is False
    assert store.match(make_request(operation={"kind": "command", "command": "git", "args": ["x"]})) is None


@pytest.mark.asyncio
async def test_capacity_raises() -> None:
    store = _store(max_grants=1)
    await store.create(_grant(GrantPredicate(kind=OperationKind.command, command="a"), GrantScope(type=GrantScopeType.glob)))
    with pytest.raises(GrantLimitReached):
        await store.create(_grant(GrantPredicate(kind=OperationKind.command, command="b"), GrantScope(type=GrantScopeType.glob)))


@pytest.mark.asyncio
async def test_concurrent_create_respects_cap() -> None:
    store = _store(max_grants=5)

    async def mk(i: int) -> bool:
        try:
            await store.create(_grant(GrantPredicate(kind=OperationKind.command, command=f"c{i}"), GrantScope(type=GrantScopeType.glob)))
            return True
        except GrantLimitReached:
            return False

    results = await asyncio.gather(*(mk(i) for i in range(20)))
    assert sum(results) == 5
    assert store.count() == 5


def test_sweep_drops_expired() -> None:
    store = _store()
    now = _utcnow()
    g = _grant(GrantPredicate(kind=OperationKind.command, command="a"), GrantScope(type=GrantScopeType.glob), ttl=10)
    g.expires_at = now - timedelta(seconds=1)
    store._grants[g.grant_id] = g
    assert store.sweep(now) == 1
    assert store.count() == 0


def test_fresh_store_is_empty_restart_fail_closed() -> None:
    # SC-G5: a new process holds no grants → re-prompts.
    assert _store().count() == 0
    assert _store().match(make_request(operation={"kind": "command", "command": "git", "args": ["x"]})) is None


# --- proposer ---------------------------------------------------------------
def test_propose_command_tightest_first_capped() -> None:
    req = make_request(project="p", operation={"kind": "command", "command": "git", "args": ["push", "origin"]})
    cands = _store().propose(req)
    assert 1 <= len(cands) <= 4
    # tightest first = exact args
    assert cands[0].predicate.args_match is ArgMatchType.exact
    assert cands[0].predicate.args == ["push", "origin"]
    # default scope is project (narrow), not global
    assert cands[0].scope.type is GrantScopeType.project
    # a widened (global) rung is offered when allowed
    assert any(c.scope.type is GrantScopeType.glob for c in cands)


def test_propose_network_includes_suffix() -> None:
    req = make_request(project="p", operation={"kind": "network", "remote_host": "api.github.com", "remote_port": 443})
    cands = _store().propose(req)
    assert any(c.predicate.host_match is HostMatchType.suffix and c.predicate.host == ".github.com" for c in cands)


def test_propose_default_scope_not_global() -> None:
    req = make_request(project="p", operation={"kind": "command", "command": "ls"})
    assert _store().propose(req)[0].scope.type is not GrantScopeType.glob
