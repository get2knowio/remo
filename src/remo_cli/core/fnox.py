"""Laptop-side fnox subprocess wrapper.

`fnox` is the developer's local secret store (https://github.com/jdx/fnox).
Remo never reads provisioning credentials from environment variables; instead
it shells out to `fnox get <name>` and surfaces the resulting value as a
short-lived in-memory string.

The broker daemon on the instance side talks to its backend directly; this
module is for the laptop only.
"""

from __future__ import annotations

import shutil
import subprocess


class FnoxError(RuntimeError):
    """Raised when a `fnox` subprocess call fails or the binary is missing."""


def is_installed() -> bool:
    """Return True if `fnox` is on PATH."""
    return shutil.which("fnox") is not None


def get(name: str) -> str:
    """Fetch a secret from fnox by name.

    Returns the value with trailing newline stripped. Raises FnoxError on
    any non-zero exit or if `fnox` is not on PATH.
    """
    if not is_installed():
        raise FnoxError(
            "`fnox` is not installed. Install it from https://github.com/jdx/fnox "
            "and re-run."
        )
    try:
        result = subprocess.run(
            ["fnox", "get", name],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise FnoxError(f"failed to invoke fnox: {exc}") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "(no stderr)"
        raise FnoxError(
            f"`fnox get {name}` failed with exit code {result.returncode}: {stderr}"
        )
    return result.stdout.rstrip("\n")


def version() -> str:
    """Return the `fnox --version` output (best-effort). Empty string if missing."""
    if not is_installed():
        return ""
    try:
        result = subprocess.run(
            ["fnox", "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return ""
    return (result.stdout or "").strip()
