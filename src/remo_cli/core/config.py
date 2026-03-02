"""Configuration path resolution and environment helpers for remo."""

from __future__ import annotations

import os
from pathlib import Path


def get_remo_home() -> Path:
    """Return the remo config directory path.

    Resolution order:
    1. REMO_HOME env var if set
    2. XDG_CONFIG_HOME/remo if XDG_CONFIG_HOME is set
    3. ~/.config/remo as fallback

    Creates the directory if it does not exist.
    """
    remo_home_env = os.environ.get("REMO_HOME")
    if remo_home_env:
        path = Path(remo_home_env)
    else:
        xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
        if xdg_config_home:
            path = Path(xdg_config_home) / "remo"
        else:
            path = Path.home() / ".config" / "remo"

    path.mkdir(parents=True, exist_ok=True)
    return path


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
        if ansible_candidate.is_dir():
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


def is_verbose() -> bool:
    """Return True if REMO_VERBOSE is set to '1'."""
    return os.environ.get("REMO_VERBOSE") == "1"
