#!/usr/bin/env python3
"""Generate and verify the release manifest (specs/031-install-path).

Stdlib only: it runs in CI before anything is installed, and install.sh parses
its output with grep/sed, so the line layout is normative
(contracts/release-manifest.md). The file is still valid JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

CLI_DIST = "remo-cli"
PLUGIN_PREFIX = "remo-provider-"
KEYS = ("name", "dist", "version", "kind", "file", "url", "sha256")
HEADER_KEYS = ("format", "release", "tag", "channel", "repo")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NAME_RE = re.compile(r"^[a-z0-9-]+$")


class ManifestError(Exception):
    pass


def _dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def render_manifest(data: dict) -> str:
    head = ",".join(f'"{k}":{_dumps(data[k])}' for k in HEADER_KEYS)
    lines = [f"{{{head},\"components\":["]
    comps = data["components"]
    for i, c in enumerate(comps):
        body = ",".join(f'"{k}":{_dumps(c[k])}' for k in KEYS)
        lines.append("{" + body + "}" + ("," if i < len(comps) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


def _wheel_metadata(path: Path) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist()
                     if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
            if not names:
                raise ManifestError(f"{path.name}: no *.dist-info/METADATA")
            text = zf.read(names[0]).decode("utf-8", "replace")
    except zipfile.BadZipFile as exc:
        raise ManifestError(f"{path.name}: not a valid wheel ({exc})") from exc
    name = version = None
    for line in text.splitlines():
        if not line.strip():
            break
        if line.startswith("Name:") and name is None:
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version:") and version is None:
            version = line.split(":", 1)[1].strip()
    if not name or not version:
        raise ManifestError(f"{path.name}: METADATA lacks Name/Version")
    return name, version


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _channel_for(tag: str) -> str | None:
    if tag.startswith("rc-"):
        return "prerelease"
    if tag.startswith("v"):
        return "stable"
    return None


def _expected_tag(release: str, channel: str) -> str:
    """The one tag a release may carry: `v<release>` stable, `rc-<release>` prerelease.

    A manifest whose tag names a different version would point every url at
    another release's assets (and self-update at another release's install.sh).
    """
    return ("rc-" if channel == "prerelease" else "v") + release


def _wheels(dist: Path) -> list[Path]:
    if not dist.is_dir():
        raise ManifestError(f"{dist}: not a directory")
    return sorted(dist.glob("*.whl"))


def build(dist: Path, tag: str, channel: str, repo: str) -> dict:
    if _channel_for(tag) != channel:
        raise ManifestError(f"tag '{tag}' does not match channel '{channel}'")
    base = f"https://github.com/{repo}/releases/download/{tag}/"
    comps = []
    for whl in _wheels(dist):
        dist_name, version = _wheel_metadata(whl)
        n = _norm(dist_name)
        if n == CLI_DIST:
            name, kind = CLI_DIST, "cli"
        elif n.startswith(PLUGIN_PREFIX) and len(n) > len(PLUGIN_PREFIX):
            name, kind = n[len(PLUGIN_PREFIX):], "plugin"
        else:
            raise ManifestError(
                f"{whl.name}: unexpected distribution '{dist_name}' "
                f"(only {CLI_DIST} and {PLUGIN_PREFIX}* are released)")
        comps.append({"name": name, "dist": n, "version": version, "kind": kind,
                      "file": whl.name, "url": base + whl.name, "sha256": _sha256(whl)})
    clis = [c for c in comps if c["kind"] == "cli"]
    if len(clis) != 1:
        raise ManifestError(f"expected exactly one {CLI_DIST} wheel in {dist}, found {len(clis)}")
    release = clis[0]["version"]
    if tag != _expected_tag(release, channel):
        raise ManifestError(
            f"tag '{tag}' does not name release {release} "
            f"(expected '{_expected_tag(release, channel)}')")
    comps.sort(key=lambda c: (c["kind"] != "cli", c["name"]))
    return {"format": 1, "release": release, "tag": tag,
            "channel": channel, "repo": repo, "components": comps}


def verify(dist: Path, manifest: Path, tag: str | None) -> None:
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    try:
        ok = render_manifest(data) == text
    except (KeyError, TypeError, AttributeError):
        ok = False
    if not ok:
        raise ManifestError("manifest layout is not the normative line layout")
    if data["format"] != 1:
        raise ManifestError(f"unsupported manifest format {data['format']!r}")
    mtag, repo = data["tag"], data["repo"]
    if tag is not None and mtag != tag:
        raise ManifestError(f"manifest tag '{mtag}' differs from expected tag '{tag}'")
    if _channel_for(mtag) != data["channel"]:
        raise ManifestError(f"channel '{data['channel']}' disagrees with tag '{mtag}'")
    if mtag != _expected_tag(data["release"], data["channel"]):
        raise ManifestError(
            f"tag '{mtag}' does not name release {data['release']} "
            f"(expected '{_expected_tag(data['release'], data['channel'])}')")
    base = f"https://github.com/{repo}/releases/download/{mtag}/"
    seen: set[str] = set()
    listed: set[str] = set()
    clis = 0
    for c in data["components"]:
        n = c["name"]
        if n in seen:
            raise ManifestError(f"duplicate component name '{n}'")
        seen.add(n)
        if c["kind"] == "cli":
            clis += 1
            if n != CLI_DIST:
                raise ManifestError(f"cli component must be named {CLI_DIST}, got '{n}'")
            if c["version"] != data["release"]:
                raise ManifestError(
                    f"cli version {c['version']} differs from release {data['release']}")
        elif c["kind"] == "plugin":
            if n == "web":
                raise ManifestError("a plugin may not be named 'web' (reserved for the extra)")
            if not NAME_RE.match(n):
                raise ManifestError(f"invalid plugin name '{n}'")
        else:
            raise ManifestError(f"component '{n}': unknown kind '{c['kind']}'")
        if c["url"] != base + c["file"]:
            raise ManifestError(f"component '{n}': url {c['url']} is not {base}{c['file']}")
        if not SHA_RE.match(c["sha256"]):
            raise ManifestError(f"component '{n}': malformed sha256")
        path = dist / c["file"]
        if not path.is_file():
            raise ManifestError(f"{c['file']}: listed in the manifest but missing from {dist}")
        if _sha256(path) != c["sha256"]:
            raise ManifestError(f"{c['file']}: sha256 mismatch")
        listed.add(c["file"])
    if clis != 1:
        raise ManifestError(f"expected exactly one cli component, found {clis}")
    for whl in _wheels(dist):
        if whl.name not in listed:
            raise ManifestError(f"{whl.name}: present in {dist} but not in the manifest")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--dist", required=True, type=Path)
    g.add_argument("--tag", required=True)
    g.add_argument("--channel", required=True, choices=("stable", "prerelease"))
    g.add_argument("--repo", required=True)
    g.add_argument("--out", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("--dist", required=True, type=Path)
    v.add_argument("--manifest", required=True, type=Path)
    v.add_argument("--tag")
    args = p.parse_args(argv)
    try:
        if args.cmd == "generate":
            data = build(args.dist, args.tag, args.channel, args.repo)
            out = args.out or args.dist / "manifest.json"
            out.write_text(render_manifest(data), encoding="utf-8")
            print(f"wrote {out} ({len(data['components'])} components)")
            return 0
        verify(args.dist, args.manifest, args.tag)
        print("manifest OK")
        return 0
    except ManifestError as exc:
        print(f"release_manifest: {exc}", file=sys.stderr)
        return 2 if args.cmd == "generate" else 1


if __name__ == "__main__":
    sys.exit(main())
