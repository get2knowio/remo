"""Builds fake GitHub releases (manifest + placeholder wheels) for install tests.

Digests are computed here, never hard-coded, so a test can tamper with a wheel
after the fact and the manifest still describes the original bytes.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

REPO = "get2knowio/remo"
PLUGINS = ("ec2", "hetzner")
PLUGIN_VERSION = "0.1.0"


def _wheel_name(dist: str, version: str) -> str:
    return f"{dist.replace('-', '_')}-{version}-py3-none-any.whl"


def release_version(tag: str) -> str:
    for prefix in ("rc-", "v"):
        if tag.startswith(prefix):
            return tag[len(prefix):]
    raise ValueError(f"unexpected tag shape: {tag}")


def make_release(
    root: Path,
    tag: str,
    *,
    plugins: tuple[str, ...] = PLUGINS,
    repo: str = REPO,
    tamper: str | None = None,
    foreign_url: bool = False,
    manifest_tag: str | None = None,
    omit_manifest: bool = False,
    manifest_release: str | None = None,
    cli_version: str | None = None,
) -> Path:
    """Create ``root/<tag>/`` and return it.

    ``tamper`` names a component (``remo-cli`` or a plugin name) whose wheel is
    altered after the manifest was written. ``foreign_url`` points the first
    plugin (or the cli) outside the release. ``manifest_tag`` writes a manifest
    that claims a different tag. ``manifest_release`` writes a header
    ``release`` other than the tag's version; ``cli_version`` builds the
    remo-cli wheel at a version other than the release.
    """
    release = release_version(tag)
    channel = "stable" if tag.startswith("v") else "prerelease"
    folder = root / tag
    folder.mkdir(parents=True, exist_ok=True)

    comps: list[tuple[str, str, str, str]] = [
        ("remo-cli", "remo-cli", cli_version or release, "cli")]
    comps += [(p, f"remo-provider-{p}", PLUGIN_VERSION, "plugin") for p in sorted(plugins)]

    lines = []
    for index, (name, dist, version, kind) in enumerate(comps):
        fname = _wheel_name(dist, version)
        data = f"placeholder wheel {dist} {version}\n".encode()
        (folder / fname).write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        base = f"https://github.com/{repo}/releases/download/{manifest_tag or tag}"
        if foreign_url and index == 0:
            base = "https://evil.example.com/releases/download/x"
        url = f"{base}/{fname}"
        sep = "," if index < len(comps) - 1 else ""
        lines.append(
            f'{{"name":"{name}","dist":"{dist}","version":"{version}","kind":"{kind}",'
            f'"file":"{fname}","url":"{url}","sha256":"{digest}"}}{sep}'
        )
    head = (
        f'{{"format":1,"release":"{manifest_release or release}","tag":"{manifest_tag or tag}",'
        f'"channel":"{channel}","repo":"{repo}","components":['
    )
    if not omit_manifest:
        (folder / "manifest.json").write_text(
            "\n".join([head, *lines, "]}"]) + "\n", encoding="utf-8"
        )

    if tamper is not None:
        dist = "remo-cli" if tamper == "remo-cli" else f"remo-provider-{tamper}"
        version = (cli_version or release) if tamper == "remo-cli" else PLUGIN_VERSION
        (folder / _wheel_name(dist, version)).write_bytes(b"tampered\n")
    return folder


def publish_latest(root: Path, tag: str) -> None:
    """What ``releases/latest/download/`` serves: a copy of the stable release."""
    target = root / "latest"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(root / tag, target)


def write_api_releases(root: Path, tags: list[str]) -> None:
    """The newest-first /releases listing, pretty-printed the way GitHub does."""
    api = root / "api"
    api.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(f'  {{\n    "tag_name": "{t}",\n    "prerelease": true\n  }}' for t in tags)
    (api / "releases.json").write_text(f"[\n{body}\n]\n", encoding="utf-8")


def rate_limit_api(root: Path) -> None:
    api = root / "api"
    api.mkdir(parents=True, exist_ok=True)
    (api / "rate_limited").write_text("1", encoding="utf-8")
