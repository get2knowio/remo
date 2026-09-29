"""Structural checks for the deacon role's pin and idempotency (spec 026).

The role installs a pinned deacon release and decides whether to (re)install
by comparing the exact version token it extracts from ``deacon --version``
with ``deacon_version``. These tests pin three facts a version bump could
silently break: the pin is a stable release (no release-candidate suffix),
the extraction regex still yields an exact token for both the stable and the
legacy rc forms, and the download URL renders for both architectures with
the musl suffix the release actually publishes.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE = REPO_ROOT / "ansible" / "roles" / "deacon"
DEFAULTS = yaml.safe_load((ROLE / "defaults" / "main.yml").read_text())
TASKS = yaml.safe_load((ROLE / "tasks" / "main.yml").read_text())

STABLE_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _task(name: str) -> dict[str, Any]:
    matches = [t for t in TASKS if t.get("name") == name]
    assert len(matches) == 1, f"expected exactly one task named {name!r}"
    return matches[0]


def _regex_from_role() -> str:
    expr = _task("Determine installed deacon version")["ansible.builtin.set_fact"][
        "deacon_installed_version"
    ]
    match = re.search(r"regex_search\('([^']+)'\)", expr)
    assert match, expr
    return match.group(1)


def test_pin_is_a_stable_release() -> None:
    assert STABLE_SEMVER.match(DEFAULTS["deacon_version"]), DEFAULTS["deacon_version"]


def test_version_regex_extracts_exact_tokens() -> None:
    regex = re.compile(_regex_from_role())
    assert regex.search("deacon 0.4.0").group(0) == "0.4.0"
    assert regex.search("deacon 0.2.0-rc.11").group(0) == "0.2.0-rc.11"
    # The equality check must not false-match an rc prefix (the role's own comment).
    assert regex.search("deacon 0.2.0-rc.11").group(0) != "0.2.0-rc.1"


def test_needs_install_is_exact_equality() -> None:
    expr = _task("Determine whether deacon needs installing")["ansible.builtin.set_fact"][
        "deacon_needs_install"
    ]
    assert "deacon_installed_version != deacon_version" in expr
    assert "deacon_current_version.rc | default(1)" in expr


def test_download_url_renders_for_both_architectures() -> None:
    download = next(
        t for t in _task("Install deacon from binary release")["block"]
        if t.get("name") == "Download deacon tarball"
    )
    url_template = download["ansible.builtin.get_url"]["url"]
    env = Environment(autoescape=False)
    for arch in ("x86_64", "aarch64"):
        url = env.from_string(url_template).render(
            deacon_version=DEFAULTS["deacon_version"], deacon_arch=arch
        ).strip()
        assert url == (
            "https://github.com/get2knowio/deacon/releases/download/"
            f"v{DEFAULTS['deacon_version']}/deacon-v{DEFAULTS['deacon_version']}-{arch}-unknown-linux-musl.tar.gz"
        )


def test_registered_accesses_are_defensive() -> None:
    text = (ROLE / "tasks" / "main.yml").read_text()
    for needle in ("deacon_current_version.rc", "deacon_current_version.stdout", "deacon_final_version.stdout"):
        for line in text.splitlines():
            if needle in line:
                assert "default(" in line, line
