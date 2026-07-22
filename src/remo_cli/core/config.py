"""Configuration path resolution and environment helpers for remo."""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Managed-instance marker (feature 013-managed-instance-tags)
#
# Fixed, built-in constants — NOT user-configurable. These are the hypervisor
# analog of the AWS ``remo=true`` tag and the Hetzner ``remo`` label, applied to
# remo-created Incus/Proxmox containers so ``sync`` can filter on them by
# default. The single definition site keeps both providers consistent.
# ---------------------------------------------------------------------------

INCUS_MANAGED_CONFIG_KEY = "user.remo"
INCUS_MANAGED_CONFIG_VALUE = "true"
PROXMOX_MANAGED_TAG = "remo"


def _resolve_remo_home() -> Path:
    """Resolve the remo config directory path with no filesystem side effects.

    Resolution order:
    1. REMO_HOME env var if set
    2. XDG_CONFIG_HOME/remo if XDG_CONFIG_HOME is set
    3. ~/.config/remo as fallback

    Pure path resolution — does not create the directory. Shared by
    :func:`get_remo_home` (which does create it) and
    :func:`get_remo_home_readonly` (which does not), so both stay in sync.
    """
    remo_home_env = os.environ.get("REMO_HOME")
    if remo_home_env:
        return Path(remo_home_env)

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home) / "remo"

    return Path.home() / ".config" / "remo"


def get_remo_home() -> Path:
    """Return the remo config directory path.

    Resolution order:
    1. REMO_HOME env var if set
    2. XDG_CONFIG_HOME/remo if XDG_CONFIG_HOME is set
    3. ~/.config/remo as fallback

    Creates the directory if it does not exist.
    """
    path = _resolve_remo_home()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_remo_home_readonly() -> Path:
    """Return the remo config directory path WITHOUT creating it.

    Same resolution order as :func:`get_remo_home` (REMO_HOME env var ->
    XDG_CONFIG_HOME/remo -> ~/.config/remo), but performs pure path
    resolution with no filesystem side effects. Safe to call against a
    read-only bind mount (e.g. a Docker ``:ro`` mount of ``~/.config/remo``)
    where ``.mkdir()`` would fail or be unsafe.
    """
    return _resolve_remo_home()


def get_ansible_dir() -> Path:
    """Resolve the ansible/ directory relative to the project root.

    Walks up from this file's location until finding a directory that contains
    either an ansible/ subdirectory or a pyproject.toml file. Returns the
    ansible/ path under that root.

    Raises RuntimeError if the ansible/ directory cannot be found.
    """
    current = Path(__file__).resolve().parent
    while True:
        ansible_candidate = current / "ansible"
        if ansible_candidate.is_dir() and not (ansible_candidate / "__init__.py").is_file():
            return ansible_candidate
        if (current / "pyproject.toml").is_file():
            ansible_in_project = current / "ansible"
            if ansible_in_project.is_dir():
                return ansible_in_project
            raise RuntimeError(
                f"Found pyproject.toml in {current} but no ansible/ directory alongside it"
            )
        parent = current.parent
        if parent == current:
            raise RuntimeError(
                "Could not find ansible/ directory or pyproject.toml walking up from "
                f"{Path(__file__).resolve()}"
            )
        current = parent


def get_project_root() -> Path:
    """Return the project root directory (where pyproject.toml lives).

    Walks up from this file's location until finding a directory that
    contains pyproject.toml.  Raises RuntimeError if not found.
    """
    current = Path(__file__).resolve().parent
    while True:
        if (current / "pyproject.toml").is_file():
            return current
        parent = current.parent
        if parent == current:
            raise RuntimeError(
                "Could not find pyproject.toml walking up from "
                f"{Path(__file__).resolve()}"
            )
        current = parent


def get_known_hosts_path() -> Path:
    """Return the path to the remo known_hosts file."""
    return get_remo_home() / "known_hosts"


def get_known_hosts_path_readonly() -> Path:
    """Return the path to the remo known_hosts file WITHOUT creating its parent.

    Mirrors :func:`get_known_hosts_path` but is built on
    :func:`get_remo_home_readonly`, so it never triggers a ``mkdir`` side
    effect. This is what read-only callers (e.g. the web service's discovery
    layer, which only ever reads the registry) should use.
    """
    return get_remo_home_readonly() / "known_hosts"


def is_verbose() -> bool:
    """Return True if REMO_VERBOSE is set to '1'."""
    return os.environ.get("REMO_VERBOSE") == "1"


# Supported devcontainer runtimes. "deacon" is an experimental single-binary
# Rust reimplementation opted into per deployment; "devcontainer" is the
# default Node-based @devcontainers/cli.
DEVCONTAINER_RUNTIMES: tuple[str, ...] = ("devcontainer", "deacon")
DEFAULT_DEVCONTAINER_RUNTIME = "devcontainer"


def get_devcontainer_runtime() -> str:
    """Return the default devcontainer runtime.

    Reads REMO_DEVCONTAINER_RUNTIME, falling back to "devcontainer". An empty
    or unset value resolves to the default. Callers may override this per
    deployment (e.g. the --devcontainer-runtime flag).
    """
    value = os.environ.get("REMO_DEVCONTAINER_RUNTIME", "").strip()
    return value or DEFAULT_DEVCONTAINER_RUNTIME
