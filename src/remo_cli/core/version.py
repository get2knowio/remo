"""Version detection and update checking for remo."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from remo_cli.core.config import get_remo_home

# PyPI JSON API for version checks
_PYPI_URL = "https://pypi.org/pypi/remo-cli/json"

# PEP 440 version pattern: optional v prefix, major.minor.patch,
# optional pre-release suffix (rc/beta/alpha/dev + number)
_SEMVER_RE = re.compile(
    r"v?(\d+)\.(\d+)\.(\d+)"
    r"(?:[.-]?(?:rc|beta|alpha|dev)\.?(\d+))?"
)

# Pre-release indicators (PEP 440 formats used by PyPI)
_PRERELEASE_MARKERS = ("rc", "beta", "alpha", "dev")


def _parse_version(tag: str) -> tuple[int, int, int, int]:
    """Parse a version string into a comparable tuple.

    The fourth element is the pre-release number if present, or 999999 for a
    full release (so releases always sort above pre-release versions).
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
                        f"Run 'uv tool upgrade remo-cli' to upgrade."
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
