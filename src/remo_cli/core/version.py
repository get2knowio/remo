"""Version detection and update checking for remo."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

from remo_cli.core.config import get_remo_home
from remo_cli.core.install_record import InstallOrigin, detect_origin, load_record

# PyPI JSON API for version checks
_PYPI_URL = "https://pypi.org/pypi/remo-cli/json"

# PEP 440 version pattern: optional v prefix, major.minor.patch,
# optional pre-release suffix (rc/beta/alpha/dev + number)
_SEMVER_RE = re.compile(
    r"v?(\d+)\.(\d+)\.(\d+)"
    r"(?:[.-]?(?:rc|beta|alpha|dev|a|b)\.?(\d+))?"
)

# Pre-release indicators (PEP 440 formats used by PyPI)
_PRERELEASE_MARKERS = ("rc", "beta", "alpha", "dev")

# The short PEP 440 forms (5.0.0a1, 5.0.0b2), which a substring test for a
# bare "a"/"b" would over-match.
_SHORT_PRERELEASE_RE = re.compile(r"\d(?:a|b)\d")


def _is_prerelease(version: str) -> bool:
    return any(marker in version for marker in _PRERELEASE_MARKERS) or bool(
        _SHORT_PRERELEASE_RE.search(version)
    )

_GITHUB_API = "https://api.github.com/repos"

_SWITCH_ONE_LINER = (
    "curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash"
)


class ReleaseLookupError(Exception):
    """The newest GitHub release could not be determined (network, rate limit, shape)."""


def _parse_version(tag: str) -> tuple[int, int, int, int]:
    """Parse a version string into a comparable tuple.

    The fourth element is the pre-release number if present, or 999999 for a
    full release (so releases always sort above pre-release versions).
    """
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return (0, 0, 0, 0)
    if not m.group(4):
        pre = 999999
    else:
        # rc/dev keep their bare number; alpha and beta are offset below zero
        # so a < b < rc < final without changing the tuple's shape.
        pre = int(m.group(4))
        # Searched in the matched release text only: a dev build's local
        # segment (``4.4.0.dev5+g3f9a27``) would otherwise read as an alpha.
        kind = re.search(r"(alpha|beta|a|b)(?=\.?\d+$)", m.group(0))
        if kind:
            pre -= 2_000_000 if kind.group(1) in ("a", "alpha") else 1_000_000
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), pre)


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
            if not _is_prerelease(v)
        ]

    if not all_versions:
        return ""

    return max(all_versions, key=_parse_version)


def _github_json(url: str) -> object:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "remo-cli"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and exc.headers and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise ReleaseLookupError(
                "GitHub rate limit exceeded (60 unauthenticated requests/hour); try again later"
            ) from exc
        raise ReleaseLookupError(f"GitHub returned HTTP {exc.code} for {url}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ReleaseLookupError(f"could not reach GitHub: {exc}") from exc
    except ValueError as exc:
        raise ReleaseLookupError(f"unexpected response from GitHub: {exc}") from exc


def _has_manifest(rel: dict[str, object]) -> bool:
    assets = rel.get("assets")
    return isinstance(assets, list) and any(
        isinstance(a, dict) and a.get("name") == "manifest.json" for a in assets
    )


def get_latest_github_release(repo: str, channel: str) -> str:
    """Newest *installable* release version on *channel* (``stable`` or ``prerelease``).

    Stable tracks non-draft, non-prerelease ``v<ver>`` releases, prerelease the
    ``rc-<ver>`` pre-releases. Only a release that already carries its
    ``manifest.json`` asset counts: release-please publishes ``vX.Y.Z`` before
    release.yml attaches the assets, and never attaches them if release.yml
    fails, so ``releases/latest`` could name a version that cannot be installed
    (#290). The API list is newest-first, so the first match is the newest.
    Raises :class:`ReleaseLookupError` rather than returning empty, so
    self-update can name the cause.
    """
    stable = channel == "stable"
    prefix = "v" if stable else "rc-"
    data = _github_json(f"{_GITHUB_API}/{repo}/releases?per_page=20")
    if not isinstance(data, list):
        raise ReleaseLookupError(f"unexpected response from GitHub for {repo}'s releases")
    pending: list[str] = []
    for rel in data:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = rel.get("tag_name")
        if not isinstance(tag, str) or not tag.startswith(prefix):
            continue
        if bool(rel.get("prerelease")) == stable:
            continue
        if _has_manifest(rel):
            return tag.removeprefix(prefix)
        pending.append(tag)
    what = "stable release" if stable else "pre-release"
    if pending:
        raise ReleaseLookupError(
            f"no {what} of {repo} carries a manifest.json yet "
            f"({pending[0]} is still being published)"
        )
    raise ReleaseLookupError(f"no {what} found for {repo}")


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
        include_prerelease = _is_prerelease(current)

        origin = detect_origin()
        record, _ = load_record() if origin is InstallOrigin.RECORDED else (None, None)
        if record is not None:
            include_prerelease = record.channel == "prerelease"

        remo_home = get_remo_home()
        # A recorded install caches per channel: a value looked up on PyPI or
        # on the other channel would announce an update `remo self-update`
        # then cannot find, for up to a day after a channel switch.
        cache_name = (
            "latest_version_cache"
            if record is None
            else f"latest_version_cache.{record.channel}"
        )
        cache_file = remo_home / cache_name
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
                    head = f"Update available: v{current} \u2192 v{cached_clean}. "
                    if record is not None:
                        return head + "Run 'remo self-update' to upgrade."
                    if origin is InstallOrigin.PYPI:
                        return head + f"Switch to the installer once: {_SWITCH_ONE_LINER}"
                    return head + "Run 'uv tool upgrade remo-cli' to upgrade."
                return None

        # Cache is stale or missing — refresh in background
        def _refresh_cache() -> None:
            try:
                if record is not None:
                    latest = get_latest_github_release(record.repo, record.channel)
                else:
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
