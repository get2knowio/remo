"""Business logic for ``remo projects`` — any host's project index (spec 030, R8).

The index itself is owned by the host scripts (``remo-host projects
add|remove|list``); this module only reaches them through whichever transport
the host uses (:func:`remo_cli.core.host_access.get_host_access`: SSH, or this
very machine for the ``local`` host). ``providers/local.py`` keeps thin
wrappers over these for ``remo local add|remove|list``.

No Click, no ``sys.exit``: failures raise the ``core/errors.py`` taxonomy.
"""

from __future__ import annotations

import os
from typing import Any

from remo_cli.core.config import LOCAL_HOST_TYPE
from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.core.host_access import get_host_access
from remo_cli.core.known_hosts import find_remo_host_by_name, upgrade_command_for
from remo_cli.core.output import print_success
from remo_cli.core.remo_host_client import (
    RemoHostClientError,
    RemoHostCommandError,
    run_remo_host_json,
)
from remo_cli.models.host import KnownHost

#: remo-host's exit status for an unsupported subcommand (older host tools), and
#: the shell's for a missing command.
EXIT_UNSUPPORTED = 4
EXIT_NOT_FOUND = 127
#: A remo-host from before the ``projects`` verb existed at all rejects it as a
#: usage error (exit 2) with this wording, not as an unsupported subcommand.
EXIT_USAGE = 2
_UNKNOWN_VERB = "unrecognized subcommand"


def _host_tools_too_old(e: RemoHostCommandError) -> bool:
    if e.returncode in (EXIT_UNSUPPORTED, EXIT_NOT_FOUND):
        return True
    return e.returncode == EXIT_USAGE and _UNKNOWN_VERB in (e.stderr or "")


def resolve_host(name: str) -> KnownHost:
    """Resolve *name* exactly as ``remo shell NAME`` does, or raise."""
    host = find_remo_host_by_name(name)
    if host is None:
        raise PreconditionError(
            f"No host named '{name}' is registered. List them with: remo shell"
        )
    return host


def _run(host: KnownHost, verb: str, **kwargs: str | None) -> dict[str, Any]:
    prefix = get_host_access(host).command_prefix(host, batch=True)
    try:
        return run_remo_host_json(prefix, verb, **kwargs)  # type: ignore[arg-type, return-value]
    except RemoHostCommandError as e:
        if e.returncode == 3:
            raise PreconditionError(e.stderr.strip() or str(e)) from e
        if _host_tools_too_old(e):
            raise PreconditionError(
                f"{host.name}'s host tools are too old for project management — "
                f"run: {upgrade_command_for(host)}"
            ) from e
        raise OperationFailedError(str(e)) from e
    except RemoHostClientError as e:
        raise OperationFailedError(str(e)) from e


def add_project(host: KnownHost, path: str, name: str | None = None) -> dict[str, Any]:
    # The local host's PATH is resolved here, against this shell's directory; a
    # remote PATH is sent verbatim and the host expands a leading ``~/``.
    target = (
        os.path.abspath(os.path.expanduser(path)) if host.type == LOCAL_HOST_TYPE else path
    )
    result = _run(host, "projects add", path=target, name=name)
    print_success(f"Added '{result.get('name', name)}' → {result.get('path', target)}")
    return result


def remove_project(host: KnownHost, name: str) -> dict[str, Any]:
    result = _run(host, "projects remove", project=name)
    print_success(f"Removed '{name}' (files untouched)")
    return result


def list_projects(host: KnownHost) -> list[dict[str, Any]]:
    payload = _run(host, "projects list")
    projects = payload.get("projects", [])
    return projects if isinstance(projects, list) else []


def _add_command(host: KnownHost) -> str:
    if host.type == LOCAL_HOST_TYPE:
        return "remo local add"
    return f"remo projects add {host.name}"


def render_projects(projects: list[dict[str, Any]], host: KnownHost | None = None) -> str:
    add = _add_command(host) if host is not None else "remo local add"
    rows = []
    for p in projects:
        source = "added" if p.get("source") == "index" else "folder"
        status = str(p.get("status", "ok"))
        if status == "missing":
            status = f"missing — re-add: {add} <new-path> --name {p.get('name', '')}"
        rows.append((str(p.get("name", "")), str(p.get("path", "")), source, status))
    if not rows:
        return f"No projects yet. Add one with: {add} PATH"
    w1 = max(len("NAME"), *(len(r[0]) for r in rows))
    w2 = max(len("PATH"), *(len(r[1]) for r in rows))
    w3 = max(len("SOURCE"), *(len(r[2]) for r in rows))
    lines = [f"{'NAME':<{w1}}  {'PATH':<{w2}}  {'SOURCE':<{w3}}  STATUS"]
    lines += [f"{a:<{w1}}  {b:<{w2}}  {c:<{w3}}  {d}" for a, b, c, d in rows]
    return "\n".join(lines)
