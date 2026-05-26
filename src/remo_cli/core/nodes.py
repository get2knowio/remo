"""Read/write surface for ~/.config/remo/nodes.yml — the Incus/Proxmox node registry.

Mode 0600 is enforced on read and write. The file never contains secret values;
admin SA tokens live only in laptop fnox under the `admin_sa_fnox_key` reference.
"""

from __future__ import annotations

import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from remo_cli.core.config import (
    NODES_DIR_MODE,
    NODES_FILE_MODE,
    get_nodes_file_path,
    get_remo_home,
)
from remo_cli.models.node import Node, NodeValidationError

NODES_FILE_VERSION = 1


class NodesError(RuntimeError):
    """Raised on nodes.yml read/write failures (perms, conflicts, parse errors)."""


def _ensure_dir_perms() -> None:
    home = get_remo_home()
    try:
        os.chmod(home, NODES_DIR_MODE)
    except PermissionError:
        # Best-effort; user owns the dir.
        pass


def _check_file_perms(path: Path) -> None:
    """Refuse to read nodes.yml if mode is wider than 0600 (per contract)."""
    st = path.stat()
    mode_bits = stat.S_IMODE(st.st_mode)
    if mode_bits & 0o077:
        raise NodesError(
            f"refusing to read {path} with permissions {mode_bits:#o} wider than 0600 — "
            f"run `chmod 0600 {path}`"
        )


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": NODES_FILE_VERSION, "nodes": []}
    _check_file_perms(path)
    with path.open("r", encoding="utf-8") as fp:
        data = yaml.safe_load(fp) or {}
    if not isinstance(data, dict):
        raise NodesError(f"{path}: top-level must be a mapping")
    version = data.get("version", 1)
    if version != NODES_FILE_VERSION:
        raise NodesError(
            f"{path}: unsupported nodes.yml version {version!r}; "
            f"this remo build supports version {NODES_FILE_VERSION}"
        )
    nodes_list = data.get("nodes") or []
    if not isinstance(nodes_list, list):
        raise NodesError(f"{path}: 'nodes' must be a list")
    data["nodes"] = nodes_list
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    _ensure_dir_perms()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(parent)
    )
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, NODES_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            yaml.safe_dump(data, fp, sort_keys=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    os.chmod(path, NODES_FILE_MODE)


def list_nodes() -> list[Node]:
    """Return all registered nodes, or [] if the file does not exist."""
    path = get_nodes_file_path()
    data = _load(path)
    out: list[Node] = []
    for entry in data["nodes"]:
        if not isinstance(entry, dict):
            raise NodesError(f"{path}: each node entry must be a mapping")
        try:
            out.append(Node.from_dict(entry))
        except NodeValidationError as exc:
            raise NodesError(f"{path}: invalid node entry: {exc}") from exc
    return out


def get_node(name: str) -> Node | None:
    """Return the node with the given name, or None if not found."""
    for n in list_nodes():
        if n.name == name:
            return n
    return None


def add_node(
    name: str,
    provider: str,
    host: str,
    ssh_user: str,
    admin_sa_fnox_key: str,
    registered_at: str | None = None,
) -> Node:
    """Insert a node entry. Idempotent: returns the existing entry if all fields match.

    Raises NodesError if `name` is already present with conflicting fields.
    """
    if registered_at is None:
        registered_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = Node(
        name=name,
        provider=provider,
        host=host,
        ssh_user=ssh_user,
        admin_sa_fnox_key=admin_sa_fnox_key,
        registered_at=registered_at,
    )
    path = get_nodes_file_path()
    data = _load(path)
    nodes = data["nodes"]
    for idx, entry in enumerate(nodes):
        existing = Node.from_dict(entry)
        if existing.name == name:
            mutable = {
                "provider": existing.provider,
                "host": existing.host,
                "ssh_user": existing.ssh_user,
                "admin_sa_fnox_key": existing.admin_sa_fnox_key,
            }
            incoming = {
                "provider": new.provider,
                "host": new.host,
                "ssh_user": new.ssh_user,
                "admin_sa_fnox_key": new.admin_sa_fnox_key,
            }
            if mutable == incoming:
                return existing
            raise NodesError(
                f"node {name!r} already registered with different fields; "
                "remove it first with `remo {provider} remove-node` "
                "(manual edit of nodes.yml for this release)"
            )
    nodes.append(new.to_dict())
    data["nodes"] = nodes
    _atomic_write(path, data)
    return new


def remove_node(name: str) -> bool:
    """Remove a node entry by name. Returns True if removed, False if absent."""
    path = get_nodes_file_path()
    data = _load(path)
    nodes = data["nodes"]
    new_nodes = [n for n in nodes if n.get("name") != name]
    if len(new_nodes) == len(nodes):
        return False
    data["nodes"] = new_nodes
    _atomic_write(path, data)
    return True
