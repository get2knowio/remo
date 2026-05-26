"""Devcontainer helpers — socket mount and auto-synthesis.

The broker socket is project-scoped; the path on the instance is
`/run/remo-broker/<project>-<hash>.sock`, bind-mounted as `/run/remo-broker/sock`
inside the devcontainer (FR-014, FR-015, data-model.md ProjectSocket).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

_LANGUAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("package.json", "mcr.microsoft.com/devcontainers/javascript-node:20"),
    ("pyproject.toml", "mcr.microsoft.com/devcontainers/python:3.12"),
    ("requirements.txt", "mcr.microsoft.com/devcontainers/python:3.12"),
    ("Pipfile", "mcr.microsoft.com/devcontainers/python:3.12"),
    ("Cargo.toml", "mcr.microsoft.com/devcontainers/rust:1"),
    ("go.mod", "mcr.microsoft.com/devcontainers/go:1.22"),
    ("Gemfile", "mcr.microsoft.com/devcontainers/ruby:3"),
)
_DEFAULT_IMAGE = "mcr.microsoft.com/devcontainers/base:ubuntu-24.04"


def socket_name(project_dir: Path) -> str:
    """Return `<project>-<sha256(abs_path)[:8]>.sock` for a project directory.

    Per data-model.md ProjectSocket: pathhash suffix avoids collisions when
    two projects share the same basename.
    """
    abs_path = str(project_dir.resolve())
    digest = hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:8]
    name = project_dir.resolve().name
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", name) or "project"
    return f"{safe}-{digest}.sock"


def _instance_socket_path(project_dir: Path) -> str:
    return f"/run/remo-broker/{socket_name(project_dir)}"


def _socket_mount_entry(project_dir: Path) -> str:
    return (
        f"source={_instance_socket_path(project_dir)},"
        "target=/run/remo-broker/sock,"
        "type=bind"
    )


def _strip_jsonc(text: str) -> str:
    """Remove `//` line comments and `/* */` block comments from JSONC text.

    devcontainer.json files routinely use comments; tomllib is wrong here and
    Python stdlib doesn't ship a JSONC parser. Walks char-by-char tracking
    string-literal state so content inside `"..."` is never touched. Preserves
    newlines inside stripped block comments so JSON line numbers don't shift.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            out.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
                i += 2 if i < n else 0
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def ensure_socket_mount(devcontainer_json_path: Path, project_dir: Path) -> bool:
    """Idempotently add the broker socket bind-mount to a devcontainer.json.

    Returns True if the file was modified, False if already correct.
    """
    if not devcontainer_json_path.exists():
        return False
    raw = devcontainer_json_path.read_text(encoding="utf-8")
    try:
        data = json.loads(_strip_jsonc(raw))
    except json.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    mount_entry = _socket_mount_entry(project_dir)
    mounts = data.get("mounts")
    if mounts is None:
        data["mounts"] = [mount_entry]
    elif isinstance(mounts, list):
        for existing in mounts:
            if isinstance(existing, str) and "/run/remo-broker/sock" in existing:
                return False
        mounts.append(mount_entry)
        data["mounts"] = mounts
    else:
        # Unexpected shape — bail rather than corrupt the file.
        return False
    devcontainer_json_path.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    return True


def detect_language_image(project_dir: Path) -> str:
    """Return the devcontainer base image to use for `project_dir`.

    Priority: language-marker scan first match wins (research R5); default
    Ubuntu base image when nothing matches.
    """
    for marker, image in _LANGUAGE_MARKERS:
        if (project_dir / marker).exists():
            return image
    return _DEFAULT_IMAGE


def synthesize_devcontainer_json(project_dir: Path) -> Path:
    """Write `.remo/devcontainer.json` with the right base image + broker mount.

    Idempotent: if the synthesized file exists, returns its path without
    overwriting (the user may have edited it). Also ensures `.remo/` is
    gitignored — caller will typically have done this already via
    `core.manifest.synthesize_default`, but doing it here too is safe.
    """
    remo_dir = project_dir / ".remo"
    remo_dir.mkdir(parents=True, exist_ok=True)
    target = remo_dir / "devcontainer.json"
    if target.exists():
        return target

    image = detect_language_image(project_dir)
    payload = {
        "name": project_dir.resolve().name,
        "image": image,
        "mounts": [_socket_mount_entry(project_dir)],
        "remoteEnv": {
            "REMO_BROKER_SOCKET": "/run/remo-broker/sock",
        },
    }
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target
