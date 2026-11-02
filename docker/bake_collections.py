"""Bake the Galaxy collections the image's remo can need, and seed markers.

Run once at image build, after remo-cli and its first-party provider plugins
are installed. Installs remo's own ``ansible/requirements.yml`` and every
installed ``remo.providers`` distribution's ``ansible/requirements.yml`` into
the shared collections path, then writes the exact marker files
``core/ansible_runner._ensure_collections`` would write into ``SEED_DIR``.
docker/entrypoint.sh copies them into REMO_HOME, so a console-triggered
configure never reaches Galaxy at job time (023; plugins since #258).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

from remo_cli.core.ansible_runner import _plugin_marker_name
from remo_cli.core.config import get_ansible_dir

COLLECTIONS_PATH = "/usr/share/ansible/collections"


def _plugin_requirements() -> list[Path]:
    found: set[Path] = set()
    for ep in entry_points(group="remo.providers"):
        if ep.dist is None:
            continue
        for file in ep.dist.files or ():
            if file.as_posix().endswith("/ansible/requirements.yml"):
                found.add(Path(str(ep.dist.locate_file(file))).resolve())
    return sorted(found)


def _bake(requirements: Path, marker: Path) -> None:
    subprocess.run(
        ["ansible-galaxy", "collection", "install", "-r", str(requirements),
         "-p", COLLECTIONS_PATH],
        check=True,
    )
    marker.write_text(hashlib.sha256(requirements.read_bytes()).hexdigest())
    print(f"baked {requirements} -> {marker.name}")


def main(seed_dir: str) -> None:
    seed = Path(seed_dir)
    seed.mkdir(parents=True, exist_ok=True)
    _bake(get_ansible_dir() / "requirements.yml", seed / "collections.lock")
    for requirements in _plugin_requirements():
        _bake(requirements, seed / _plugin_marker_name(requirements.parent))


if __name__ == "__main__":
    main(sys.argv[1])
