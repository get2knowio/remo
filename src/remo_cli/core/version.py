"""Version detection, update checking, and self-update logic for remo."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

from remo_cli.core.config import get_remo_home
from remo_cli.core.output import print_error, print_info, print_success, print_warning

# PyPI JSON API for version checks
_PYPI_URL = "https://pypi.org/pypi/remo-cli/json"

# Semver pattern: optional v prefix, major.minor.patch, optional -rc.N
_SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?")

# Pre-release tag indicators
_PRERELEASE_MARKERS = ("-rc", "-beta", "-alpha")


def _parse_version(tag: str) -> tuple[int, int, int, int]:
    """Parse a version string into a comparable tuple.

    The fourth element is the rc number if present, or 999999 for a
    full release (so releases always sort above rc versions).
    """
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return (0, 0, 0, 0)
    return (
        int(m.group(1)),
        int(m.group(2)),
        int(m.group(3)),
        int(m.group(4)) if m.group(4) else 999999,
    )


# ------------------------------------------------------------------
# Core version functions
# ------------------------------------------------------------------


def get_current_version() -> str:
    """Return the current installed version from package metadata.

    Uses ``importlib.metadata`` which works for both pip and uv installs.
    Returns ``"unknown"`` if the package is not installed.
    """
    try:
        from importlib.metadata import version

        return version("remo-cli")
    except Exception:
        return "unknown"


def get_latest_release(include_prerelease: bool = False) -> str:
    """Determine the latest remo-cli release version from PyPI.

    Returns the version string of the latest release (e.g. ``"1.2.3"``),
    or an empty string if nothing could be determined.
    """
    try:
        req = urllib.request.Request(
            _PYPI_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return ""

    all_versions = list(data.get("releases", {}).keys())
    if not all_versions:
        return ""

    if not include_prerelease:
        all_versions = [
            v for v in all_versions
            if not any(marker in v for marker in _PRERELEASE_MARKERS)
        ]

    if not all_versions:
        return ""

    return max(all_versions, key=_parse_version)


def version_is_newer(candidate: str, current: str) -> bool:
    """Return True if *candidate* is a newer version than *current*.

    Both strings are semver-ish, with optional ``v`` prefix.
    rc versions sort below their corresponding release.
    """
    return _parse_version(candidate) > _parse_version(current)


def check_for_updates_passive() -> str | None:
    """Perform a non-blocking update check and return a hint string if available.

    * If the current version is ``"unknown"``, returns ``None`` immediately.
    * Detects whether the user is on a pre-release track.
    * Uses a cache file at ``{remo_home}/latest_version_cache`` with a 24-hour TTL.
    * When the cache is fresh and a newer version exists, returns an
      informational hint string.
    * When the cache is stale or missing, spawns a background thread to
      refresh it and returns ``None``.
    * Never raises exceptions.
    """
    try:
        current = get_current_version()
        if current == "unknown":
            return None

        # Detect pre-release track
        include_prerelease = any(marker in current for marker in _PRERELEASE_MARKERS)

        remo_home = get_remo_home()
        cache_file = remo_home / "latest_version_cache"
        cache_ttl = 86400  # 24 hours

        # Check cache freshness
        if cache_file.is_file():
            try:
                file_mtime = cache_file.stat().st_mtime
                cache_age = time.time() - file_mtime
            except OSError:
                cache_age = cache_ttl + 1  # treat as stale

            if cache_age < cache_ttl:
                # Cache is fresh — check if cached version is newer
                try:
                    cached_version = cache_file.read_text().strip()
                    cached_clean = cached_version.lstrip("v")
                except OSError:
                    return None

                if cached_clean and version_is_newer(cached_clean, current):
                    return (
                        f"Update available: v{current} \u2192 v{cached_clean}. "
                        f"Run 'remo self-update' or 'uv tool upgrade remo-cli' to upgrade."
                    )
                return None

        # Cache is stale or missing — refresh in background
        def _refresh_cache() -> None:
            try:
                latest = get_latest_release(include_prerelease)
                if latest:
                    remo_home.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(latest)
            except Exception:
                pass

        thread = threading.Thread(target=_refresh_cache, daemon=True)
        thread.start()

        return None
    except Exception:
        return None


# ------------------------------------------------------------------
# Self-update logic
# ------------------------------------------------------------------


def _detect_installer() -> str:
    """Detect whether remo-cli was installed via uv or pip."""
    if shutil.which("uv"):
        return "uv"
    return "pip"


def handle_self_update(
    version: str | None = None,
    check_only: bool = False,
    pre_release: bool = False,
) -> None:
    """Update remo-cli to a specified version or the latest release.

    Parameters
    ----------
    version:
        Specific version to install (e.g. ``"1.2.3"``).
    check_only:
        If ``True``, only report whether an update is available.
    pre_release:
        If ``True``, include pre-release versions when determining the
        latest release.
    """
    current_version = get_current_version()
    print_info(f"Current version: {current_version}")

    if current_version == "unknown":
        print_error("Could not determine current version")
        sys.exit(1)

    # ---- Determine target version ----
    target_version = ""
    if version:
        target_version = version.lstrip("v")
    elif pre_release:
        print_info("Checking PyPI for latest pre-release...")
        target_version = get_latest_release(include_prerelease=True)
    else:
        print_info("Checking PyPI for latest stable release...")
        target_version = get_latest_release(include_prerelease=False)

    if not target_version:
        print_error("Could not determine target version")
        sys.exit(1)

    target_clean = target_version.lstrip("v")
    current_clean = current_version.lstrip("v")

    print_info(f"Latest version: {target_clean}")

    if target_clean == current_clean:
        print_success("Already up to date!")
        return

    if check_only:
        print()
        print_warning(f"Update available: {current_clean} -> {target_clean}")
        installer = _detect_installer()
        if installer == "uv":
            print("Run 'remo self-update' or 'uv tool upgrade remo-cli' to install")
        else:
            print("Run 'remo self-update' or 'pip install --upgrade remo-cli' to install")
        return

    print()
    print_info(f"Updating remo-cli: {current_clean} -> {target_clean}")
    print()

    installer = _detect_installer()
    pkg_spec = f"remo-cli=={target_clean}"

    if installer == "uv":
        cmd: list[str] = ["uv", "tool", "upgrade", "remo-cli"]
        if version:
            # Pin to specific version via reinstall
            cmd = ["uv", "tool", "install", "--force", pkg_spec]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", pkg_spec]

    print_info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print_error("Update failed")
        sys.exit(1)

    print()
    print_success("==============================================")
    print_success(f"  remo-cli updated to {target_clean}")
    print_success("==============================================")
    print()

    _clear_version_cache()


def _clear_version_cache() -> None:
    """Remove the version cache file so passive checks start fresh."""
    try:
        cache_file = get_remo_home() / "latest_version_cache"
        cache_file.unlink(missing_ok=True)
    except Exception:
        pass
