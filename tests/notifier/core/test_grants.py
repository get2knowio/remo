"""Tests for standing grants reworked for agentsh (spec 008 — kind/target/session)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from remo_cli.notifier.grants import (
    CandidateGrant,
    Grant,
    GrantLimitReached,
    GrantPredicate,
    GrantScope,
    GrantScopeType,
    GrantStore,
    TargetMatchType,
)

from ..conftest import make_request


def _store(max_grants: int = 100, allow_global: bool = True) -> GrantStore:
    return GrantStore(max_grants=max_grants, instance_id="inst-1", allow_global_scope=allow_global)


def _grant(**pred) -> Grant:
    predicate = GrantPredicate(**({"kind": "command"} | pred))
    return Grant.create(
        predicate=predicate,
        scope=GrantScope(type=GrantScopeType.glob),
        ttl_seconds=3600,
        created_by="t",
        source_approval_id="x",
    )


# --- predicate matching ------------------------------------------------------
def test_predicate_kind_must_match() -> None:
    p = GrantPredicate(kind="command")
    assert p.matches(make_request(kind="command")) is True
    assert p.matches(make_request(kind="network")) is False


def test_predicate_any_target() -> None:
    p = GrantPredicate(kind="file_delete", target_match=TargetMatchType.any)
    assert p.matches(make_request(kind="file_delete", target="/a/b")) is True


def test_predicate_exact_target() -> None:
    p = GrantPredicate(kind="file_delete", target="/a/b", target_match=TargetMatchType.exact)
    assert p.matches(make_request(kind="file_delete", target="/a/b")) is True
    assert p.matches(make_request(kind="file_delete", target="/a/c")) is False


def test_predicate_prefix_target() -> None:
    p = GrantPredicate(kind="file_delete", target="/a/", target_match=TargetMatchType.prefix)
    assert p.matches(make_request(kind="file_delete", target="/a/b/c")) is True
    assert p.matches(make_request(kind="file_delete", target="/z/b")) is False


def test_predicate_suffix_target() -> None:
    p = GrantPredicate(kind="net_connect", target=".github.com", target_match=TargetMatchType.suffix)
    assert p.matches(make_request(kind="net_connect", target="api.github.com")) is True
    assert p.matches(make_request(kind="net_connect", target="github.com")) is False
    assert p.matches(make_request(kind="net_connect", target="api.gitlab.com")) is False


def test_predicate_glob_target_matches_subdomains() -> None:
    p = GrantPredicate(kind="net_connect", target="*.github.com", target_match=TargetMatchType.glob)
    assert p.matches(make_request(kind="net_connect", target="api.github.com")) is True
    assert p.matches(make_request(kind="net_connect", target="raw.github.com")) is True
    # A bare apex domain has no subdomain, so "*." does not match it.
    assert p.matches(make_request(kind="net_connect", target="github.com")) is False
    # Exfil destination must not match a known-good wildcard.
    assert p.matches(make_request(kind="net_connect", target="evil.attacker.com")) is False


def test_predicate_glob_is_case_sensitive() -> None:
    p = GrantPredicate(kind="net_connect", target="*.GitHub.com", target_match=TargetMatchType.glob)
    assert p.matches(make_request(kind="net_connect", target="api.github.com")) is False


def test_predicate_empty_value_never_matches() -> None:
    for mt in (TargetMatchType.suffix, TargetMatchType.glob, TargetMatchType.prefix):
        p = GrantPredicate(kind="net_connect", target="", target_match=mt)
        assert p.matches(make_request(kind="net_connect", target="api.github.com")) is False


# --- scope -------------------------------------------------------------------
def test_glob_scope_matches_any_session() -> None:
    s = GrantScope(type=GrantScopeType.glob)
    assert s.matches(make_request(session_id="anything")) is True


def test_session_scope_exact() -> None:
    s = GrantScope(type=GrantScopeType.session, value="sess-1")
    assert s.matches(make_request(session_id="sess-1")) is True
    assert s.matches(make_request(session_id="sess-2")) is False
    assert s.matches(make_request(session_id="")) is False


# --- store match / lifecycle -------------------------------------------------
async def test_match_increments_usage() -> None:
    store = _store()
    g = _grant(target_match=TargetMatchType.any)
    await store.create(g)
    matched = store.match(make_request(kind="command"))
    assert matched is g
    assert g.uses_count == 1


async def test_no_match_returns_none() -> None:
    store = _store()
    await store.create(_grant(target="git", target_match=TargetMatchType.exact))
    assert store.match(make_request(kind="command", target="rm")) is None


async def test_paused_store_never_matches() -> None:
    store = _store()
    await store.create(_grant(target_match=TargetMatchType.any))
    store.set_paused(True)
    assert store.match(make_request(kind="command")) is None


async def test_expired_grant_does_not_match() -> None:
    store = _store()
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    g = Grant(
        grant_id="g1",
        created_at=past - timedelta(seconds=10),
        expires_at=past,
        scope=GrantScope(type=GrantScopeType.glob),
        predicate=GrantPredicate(kind="command", target_match=TargetMatchType.any),
    )
    store._grants[g.grant_id] = g  # noqa: SLF001 - seed an expired grant directly
    assert store.match(make_request(kind="command")) is None


async def test_create_respects_limit() -> None:
    store = _store(max_grants=1)
    await store.create(_grant(target_match=TargetMatchType.any))
    with pytest.raises(GrantLimitReached):
        await store.create(_grant(kind="network", target_match=TargetMatchType.any))


async def test_revoke_and_list_active() -> None:
    store = _store()
    g = _grant(target_match=TargetMatchType.any)
    await store.create(g)
    assert len(store.list_active()) == 1
    assert await store.revoke(g.grant_id) is True
    assert store.count() == 0
    assert await store.revoke("nope") is False


# --- proposal ----------------------------------------------------------------
def test_propose_offers_exact_prefix_and_any() -> None:
    store = _store()
    cands = store.propose(make_request(kind="file_delete", target="/ws/logs/a.txt", session_id="s1"))
    assert all(isinstance(c, CandidateGrant) for c in cands)
    matches = [c.predicate.target_match for c in cands]
    assert TargetMatchType.exact in matches
    assert TargetMatchType.prefix in matches
    assert TargetMatchType.any in matches


def test_propose_without_global_scope_is_session_only() -> None:
    store = _store(allow_global=False)
    cands = store.propose(make_request(kind="command", target="rm", session_id="s1"))
    assert cands  # still offers a session-scoped candidate
    assert all(c.scope.type is GrantScopeType.session for c in cands)


def test_propose_caps_at_four() -> None:
    store = _store()
    cands = store.propose(make_request(kind="file_delete", target="/a/b/c", session_id="s1"))
    assert len(cands) <= 4


def test_propose_offers_domain_glob_for_host_target() -> None:
    store = _store()
    cands = store.propose(make_request(kind="net_connect", target="api.github.com"))
    globs = [c.predicate for c in cands if c.predicate.target_match is TargetMatchType.glob]
    assert any(p.target == "*.github.com" for p in globs)
    # And the proposed wildcard actually matches the originating request.
    proposed = next(p for p in globs if p.target == "*.github.com")
    assert proposed.matches(make_request(kind="net_connect", target="api.github.com")) is True


def test_propose_no_host_glob_for_paths_or_single_label() -> None:
    store = _store()
    # Path-style targets get a prefix candidate, never a host glob.
    paths = store.propose(make_request(kind="file_delete", target="/ws/a.txt"))
    assert all(c.predicate.target_match is not TargetMatchType.glob for c in paths)
    # Single-label hosts (no dot) cannot be generalized to a domain wildcard.
    single = store.propose(make_request(kind="net_connect", target="localhost"))
    assert all(c.predicate.target_match is not TargetMatchType.glob for c in single)
