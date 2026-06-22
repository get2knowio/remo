"""Standing grants ("Always" auto-approval) — Addendum 001, reworked for agentsh.

In-memory, TTL-bounded, scoped, fail-closed. The matcher is deterministic and
exact — it is the only ``allow``-capable code path, so it never does fuzzy/
semantic matching. Reworked for spec 008 to match agentsh's approval shape
(``kind`` + ``target`` + ``session_id``) instead of the 007 structured
``Operation``. See contracts/agentsh-integration.md.
"""

from __future__ import annotations

import asyncio
import fnmatch
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict

from remo_cli.notifier.models import AgentshRequest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GrantScopeType(str, Enum):
    session = "session"  # a specific agentsh session_id
    glob = "global"  # value "global"; attr name avoids the reserved word


class TargetMatchType(str, Enum):
    any = "any"  # match any target of this kind
    exact = "exact"  # target == value
    prefix = "prefix"  # target startswith value
    suffix = "suffix"  # target endswith value (e.g. ".github.com")
    glob = "glob"  # fnmatch (case-sensitive) — e.g. "*.github.com", "api.*.example.com"


class GrantLimitReached(Exception):
    """Raised by GrantStore.create() when max_grants is already reached."""


class GrantScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: GrantScopeType = GrantScopeType.glob
    value: str = ""

    def matches(self, request: AgentshRequest) -> bool:
        """Exact-equality scope check; missing field → False (fail-closed)."""
        if self.type is GrantScopeType.glob:
            return True
        if self.type is GrantScopeType.session:
            return bool(request.session_id) and request.session_id == self.value
        return False


class GrantPredicate(BaseModel):
    """Deterministic, inspectable match rule over an agentsh ``Request``."""

    model_config = ConfigDict(extra="forbid")

    kind: str  # agentsh kind, exact match (e.g. "file_delete", "command")
    target: str = ""  # value for exact/prefix matching
    target_match: TargetMatchType = TargetMatchType.any

    def matches(self, request: AgentshRequest) -> bool:
        if not self.kind or request.kind != self.kind:
            return False
        if self.target_match is TargetMatchType.any:
            return True
        if self.target_match is TargetMatchType.exact:
            return request.target == self.target
        if self.target_match is TargetMatchType.prefix:
            return bool(self.target) and request.target.startswith(self.target)
        if self.target_match is TargetMatchType.suffix:
            return bool(self.target) and request.target.endswith(self.target)
        if self.target_match is TargetMatchType.glob:
            # Case-sensitive fnmatch so matching is deterministic across platforms.
            return bool(self.target) and fnmatch.fnmatchcase(request.target, self.target)
        return False


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str = ""
    created_at: datetime = None  # type: ignore[assignment]
    created_by: str = ""
    expires_at: datetime
    source_approval_id: str = ""
    scope: GrantScope
    predicate: GrantPredicate
    uses_count: int = 0
    last_used_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return now < self.expires_at

    def matches(self, request: AgentshRequest, now: datetime) -> bool:
        return self.active(now) and self.scope.matches(request) and self.predicate.matches(request)

    @classmethod
    def create(
        cls,
        *,
        predicate: GrantPredicate,
        scope: GrantScope,
        ttl_seconds: int,
        created_by: str,
        source_approval_id: str,
        now: datetime | None = None,
    ) -> Grant:
        now = now or _utcnow()
        return cls(
            grant_id=str(uuid.uuid4()),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_by=created_by,
            source_approval_id=source_approval_id,
            scope=scope,
            predicate=predicate,
        )


class CandidateGrant(BaseModel):
    """Transient picker option (label + predicate + scope). Not persisted."""

    model_config = ConfigDict(extra="forbid")

    label: str
    predicate: GrantPredicate
    scope: GrantScope


def _scope_label(scope: GrantScope) -> str:
    if scope.type is GrantScopeType.glob:
        return "everywhere"
    return f"this {scope.type.value}"


class GrantStore:
    """In-memory registry of standing grants. Fail-closed everywhere."""

    def __init__(self, *, max_grants: int, instance_id: str, allow_global_scope: bool = True) -> None:
        self._max = max_grants
        self._instance_id = instance_id
        self._allow_global = allow_global_scope
        self._grants: dict[str, Grant] = {}
        self._lock = asyncio.Lock()
        self.paused = False

    def count(self) -> int:
        return len(self._grants)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def match(self, request: AgentshRequest, now: datetime | None = None) -> Grant | None:
        """Return the first active grant matching the request, or None.

        Allow-capable — exact and deterministic. Paused/empty/expired/mismatch
        all yield None (fail-closed). Increments the matched grant's usage.
        """
        if self.paused:
            return None
        now = now or _utcnow()
        for grant in list(self._grants.values()):
            try:
                if grant.matches(request, now):
                    grant.uses_count += 1
                    grant.last_used_at = now
                    return grant
            except Exception:  # noqa: BLE001 - any match error is fail-closed (no match)
                continue
        return None

    async def create(self, grant: Grant) -> Grant:
        async with self._lock:
            # Drop expired entries opportunistically so the cap reflects live grants.
            self._sweep_locked(_utcnow())
            if len(self._grants) >= self._max:
                raise GrantLimitReached(f"max_grants={self._max} reached")
            self._grants[grant.grant_id] = grant
            return grant

    def list_active(self, now: datetime | None = None) -> list[Grant]:
        now = now or _utcnow()
        return [g for g in self._grants.values() if g.active(now)]

    async def revoke(self, grant_id: str) -> bool:
        async with self._lock:
            return self._grants.pop(grant_id, None) is not None

    def sweep(self, now: datetime | None = None) -> int:
        return self._sweep_locked(now or _utcnow())

    def _sweep_locked(self, now: datetime) -> int:
        expired = [gid for gid, g in self._grants.items() if not g.active(now)]
        for gid in expired:
            del self._grants[gid]
        return len(expired)

    # -- candidate proposal (pure) -----------------------------------------
    def propose(self, request: AgentshRequest) -> list[CandidateGrant]:
        """Tightest-first generalization candidates (≤4), each with a scope."""
        kind = request.kind or "operation"
        target = (request.target or "").strip()
        glob = GrantScope(type=GrantScopeType.glob)
        out: list[CandidateGrant] = []

        def add(label: str, predicate: GrantPredicate, sc: GrantScope) -> None:
            if len(out) < 4:
                out.append(
                    CandidateGrant(label=f"{label} · {_scope_label(sc)}", predicate=predicate, scope=sc)
                )

        if self._allow_global:
            if target:
                add(
                    f"{kind}: {target}",
                    GrantPredicate(kind=kind, target=target, target_match=TargetMatchType.exact),
                    glob,
                )
                prefix = self._target_prefix(target)
                if prefix and prefix != target:
                    add(
                        f"{kind}: {prefix}*",
                        GrantPredicate(kind=kind, target=prefix, target_match=TargetMatchType.prefix),
                        glob,
                    )
                # Host-like target (egress): collapse subdomains to a domain wildcard
                # so "Always…" yields e.g. "*.github.com" for credential-exfil defense.
                host_glob = self._host_glob(target)
                if host_glob and host_glob != target:
                    add(
                        f"{kind}: {host_glob}",
                        GrantPredicate(kind=kind, target=host_glob, target_match=TargetMatchType.glob),
                        glob,
                    )
            add(f"any {kind}", GrantPredicate(kind=kind, target_match=TargetMatchType.any), glob)

        # Session-scoped fallback (always offered when the request carries one).
        if request.session_id:
            sess = GrantScope(type=GrantScopeType.session, value=request.session_id)
            add(f"any {kind}", GrantPredicate(kind=kind, target_match=TargetMatchType.any), sess)

        return out[:4]

    @staticmethod
    def _target_prefix(target: str) -> str:
        """Directory-ish prefix for a path/host target ("/a/b/c" -> "/a/b/")."""
        if "/" in target:
            return target.rsplit("/", 1)[0] + "/"
        return ""

    @staticmethod
    def _host_glob(target: str) -> str:
        """Subdomain-collapsing glob for a host-like target ("api.github.com" -> "*.github.com").

        Heuristic for network egress: only fires for slash-free, dotted targets;
        strips any ``:port`` and collapses to the last two labels. Refine once the
        real agentsh network target shape is captured (issue #44) — e.g. multi-label
        public suffixes (``foo.co.uk``) would over-collapse under this simple rule.
        """
        if "/" in target:
            return ""
        host = target.split(":", 1)[0]
        labels = [label for label in host.split(".") if label]
        if len(labels) < 2:
            return ""
        return "*." + ".".join(labels[-2:])
