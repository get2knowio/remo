"""Standing grants ("Always" auto-approval) — Addendum 001.

In-memory, TTL-bounded, scoped, fail-closed. The matcher is deterministic and
exact — it is the only `allow`-capable code path, so it never does fuzzy/
semantic matching. See addendum-001-standing-grants.md and
contracts/grant-schema.md.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from fnmatch import fnmatchcase

from pydantic import BaseModel, ConfigDict, Field

from remo_cli.notifier.models import ApprovalRequest, OperationKind


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GrantScopeType(str, Enum):
    session = "session"
    workspace = "workspace"
    project = "project"
    instance = "instance"
    glob = "global"  # value "global"; attr name avoids the reserved word


class ArgMatchType(str, Enum):
    exact = "exact"
    prefix = "prefix"
    glob = "glob"


class HostMatchType(str, Enum):
    exact = "exact"
    suffix = "suffix"


class GrantLimitReached(Exception):
    """Raised by GrantStore.create() when max_grants is already reached."""


class GrantScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: GrantScopeType
    value: str = ""

    def matches(self, request: ApprovalRequest, *, instance_id: str) -> bool:
        """Exact-equality scope check; missing field → False (fail-closed).

        `instance` scope is the one intentional exception: a request lacking
        instance_id falls back to the notifier's configured instance id.
        """
        if self.type is GrantScopeType.glob:
            return True
        if self.type is GrantScopeType.session:
            return bool(request.session_id) and request.session_id == self.value
        if self.type is GrantScopeType.workspace:
            return bool(request.workspace) and request.workspace == self.value
        if self.type is GrantScopeType.project:
            return bool(request.project) and request.project == self.value
        if self.type is GrantScopeType.instance:
            effective = request.instance_id or instance_id
            return bool(effective) and effective == self.value
        return False


class GrantPredicate(BaseModel):
    """Deterministic, inspectable match rule (agentsh-rule-shaped)."""

    model_config = ConfigDict(extra="forbid")

    kind: OperationKind
    # command / signal
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    args_match: ArgMatchType = ArgMatchType.exact
    # file (path-only in v1 — the wire Operation carries no read/write/delete verb)
    paths: list[str] = Field(default_factory=list)
    operations: list[str] = Field(default_factory=list)  # reserved; not enforced in v1
    # network
    host: str | None = None
    host_match: HostMatchType = HostMatchType.exact
    port: int | None = None
    # signal
    signal: str | None = None
    # broadest rung (optional)
    policy_rule_name: str | None = None

    def matches(self, request: ApprovalRequest, *, workspace: str | None = None) -> bool:
        op = request.operation
        if op.kind is not self.kind:
            return False
        if self.policy_rule_name is not None:
            return request.policy_rule_name == self.policy_rule_name
        if self.kind is OperationKind.command:
            return op.command == self.command and self._args_ok(op.args)
        if self.kind is OperationKind.file:
            return self._path_ok(op.path, workspace or request.workspace)
        if self.kind is OperationKind.network:
            return self._host_ok(op.remote_host) and self._port_ok(op.remote_port)
        if self.kind is OperationKind.signal:
            return self.signal is None or self.signal == op.command
        return False

    def _args_ok(self, args: list[str]) -> bool:
        if self.args_match is ArgMatchType.exact:
            return args == self.args
        if self.args_match is ArgMatchType.prefix:
            return args[: len(self.args)] == self.args
        # glob: positional, equal length
        if len(args) != len(self.args):
            return False
        return all(fnmatchcase(a, p) for a, p in zip(args, self.args))

    def _path_ok(self, path: str | None, workspace: str | None) -> bool:
        if not path or not self.paths:
            return False
        for pattern in self.paths:
            expanded = pattern.replace("{workspace}", workspace) if workspace else pattern
            if "{workspace}" in expanded:  # placeholder unresolved → fail-closed
                continue
            if fnmatchcase(path, expanded):
                return True
        return False

    def _host_ok(self, host: str | None) -> bool:
        if not host or self.host is None:
            return False
        if self.host_match is HostMatchType.exact:
            return host == self.host
        return host == self.host.lstrip(".") or host.endswith(self.host)

    def _port_ok(self, port: int | None) -> bool:
        return self.port is None or self.port == port


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=_utcnow)
    created_by: str = ""
    expires_at: datetime
    source_approval_id: str = ""
    scope: GrantScope
    predicate: GrantPredicate
    eligible: bool = True  # always True in v1 (reserved for future denylist)
    uses_count: int = 0
    last_used_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return now < self.expires_at

    def matches(self, request: ApprovalRequest, now: datetime, *, instance_id: str) -> bool:
        return (
            self.active(now)
            and self.scope.matches(request, instance_id=instance_id)
            and self.predicate.matches(request)
        )

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
    return f"this {scope.type.value} ({scope.value})"


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

    def match(self, request: ApprovalRequest, now: datetime | None = None) -> Grant | None:
        """Return the first active grant matching the request, or None.

        Allow-capable — exact and deterministic. Paused/empty/expired/mismatch
        all yield None (fail-closed). Increments the matched grant's usage.
        """
        if self.paused:
            return None
        now = now or _utcnow()
        for grant in list(self._grants.values()):
            try:
                if grant.matches(request, now, instance_id=self._instance_id):
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
    def propose(self, request: ApprovalRequest) -> list[CandidateGrant]:
        """Tightest-first generalization candidates (≤4), each with a scope."""
        op = request.operation
        scope = self._default_scope(request)
        wide = GrantScope(type=GrantScopeType.glob) if self._allow_global else scope
        out: list[CandidateGrant] = []

        def add(label: str, predicate: GrantPredicate, sc: GrantScope) -> None:
            if len(out) < 4:
                out.append(
                    CandidateGrant(label=f"{label} · {_scope_label(sc)}", predicate=predicate, scope=sc)
                )

        if op.kind is OperationKind.command and op.command:
            cmd = op.command
            add(f"{cmd} {' '.join(op.args)}".strip(),
                GrantPredicate(kind=op.kind, command=cmd, args=list(op.args), args_match=ArgMatchType.exact), scope)
            if op.args:
                add(f"{cmd} {op.args[0]} *",
                    GrantPredicate(kind=op.kind, command=cmd, args=[op.args[0]], args_match=ArgMatchType.prefix), scope)
            add(f"{cmd} *",
                GrantPredicate(kind=op.kind, command=cmd, args=[], args_match=ArgMatchType.prefix), scope)
            if self._allow_global:
                add(f"{cmd} *",
                    GrantPredicate(kind=op.kind, command=cmd, args=[], args_match=ArgMatchType.prefix), wide)
        elif op.kind is OperationKind.network and op.remote_host:
            host, port = op.remote_host, op.remote_port
            add(f"{host}:{port}",
                GrantPredicate(kind=op.kind, host=host, host_match=HostMatchType.exact, port=port), scope)
            suffix = self._domain_suffix(host)
            if suffix != host:
                add(f"*{suffix}:{port}",
                    GrantPredicate(kind=op.kind, host=suffix, host_match=HostMatchType.suffix, port=port), scope)
            if self._allow_global:
                add(f"{host}:{port}",
                    GrantPredicate(kind=op.kind, host=host, host_match=HostMatchType.exact, port=port), wide)
        elif op.kind is OperationKind.file and op.path:
            add(f"file {op.path}",
                GrantPredicate(kind=op.kind, paths=[op.path]), scope)
            if request.workspace:
                add("files under {workspace}",
                    GrantPredicate(kind=op.kind, paths=["{workspace}/**"]), scope)
        elif op.kind is OperationKind.signal:
            add(f"signal {op.command or '*'}",
                GrantPredicate(kind=op.kind, signal=op.command), scope)

        # Broadest fallback rung: the policy rule (optional, FR-G6).
        add(f"rule: {request.policy_rule_name}",
            GrantPredicate(kind=op.kind, policy_rule_name=request.policy_rule_name), scope)
        return out[:4]

    def _default_scope(self, request: ApprovalRequest) -> GrantScope:
        # Narrowest scope whose field the request actually carries (practical
        # order: project → workspace → session → instance → global).
        if request.project:
            return GrantScope(type=GrantScopeType.project, value=request.project)
        if request.workspace:
            return GrantScope(type=GrantScopeType.workspace, value=request.workspace)
        if request.session_id:
            return GrantScope(type=GrantScopeType.session, value=request.session_id)
        if request.instance_id:
            return GrantScope(type=GrantScopeType.instance, value=request.instance_id)
        return GrantScope(type=GrantScopeType.instance, value=self._instance_id)

    @staticmethod
    def _domain_suffix(host: str) -> str:
        parts = host.split(".")
        if len(parts) <= 2:
            return host
        return "." + ".".join(parts[1:])
