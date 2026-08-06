"""`.github/dependabot.yml` must point at directories that exist.

Dependabot silently does nothing useful for an entry whose directory is absent
— there is no build failure and no PR, just an ecosystem that quietly goes
unwatched. PR #39 proposed a `docker` entry covering `/` and `/notifier`, but
`/notifier` belongs to an unmerged feature branch and does not exist on main;
that entry would have looked like Docker coverage while providing none for the
path that was wrong.

The same trap applies to a Dockerfile that moves. These checks are cheap and
catch it at review time rather than months later when a base image turns out
never to have been tracked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".github" / "dependabot.yml"


def _updates() -> list[dict]:
    data = yaml.safe_load(CONFIG.read_text())
    assert data["version"] == 2, "dependabot config must declare version 2"
    updates = data["updates"]
    assert isinstance(updates, list) and updates
    return updates


def _entry_dirs(entry: dict) -> list[str]:
    """Both spellings: singular `directory` and plural `directories`."""
    if "directories" in entry:
        return list(entry["directories"])
    return [entry["directory"]]


def _ids() -> list[str]:
    return [
        f"{e['package-ecosystem']}:{d}" for e in _updates() for d in _entry_dirs(e)
    ]


def _pairs() -> list[tuple[str, str]]:
    return [(e["package-ecosystem"], d) for e in _updates() for d in _entry_dirs(e)]


@pytest.mark.parametrize(("ecosystem", "directory"), _pairs(), ids=_ids())
def test_configured_directory_exists(ecosystem: str, directory: str) -> None:
    target = REPO_ROOT / directory.lstrip("/")
    assert target.is_dir(), (
        f"dependabot watches {ecosystem} in {directory!r}, which does not exist — "
        "the entry is silently inert"
    )


@pytest.mark.parametrize(("ecosystem", "directory"), _pairs(), ids=_ids())
def test_docker_directories_actually_contain_a_dockerfile(
    ecosystem: str, directory: str
) -> None:
    if ecosystem != "docker":
        pytest.skip("only meaningful for the docker ecosystem")
    target = REPO_ROOT / directory.lstrip("/")
    assert (target / "Dockerfile").is_file(), (
        f"no Dockerfile in {directory!r}; dependabot would find nothing to update"
    )


def test_every_dockerfile_is_covered() -> None:
    """The inverse: a Dockerfile nobody watches.

    Base images are where container CVEs usually arrive, so an untracked one is
    the failure that matters — and it is invisible, because an unwatched image
    simply never produces a PR.
    """
    watched = {
        (REPO_ROOT / d.lstrip("/")).resolve()
        for eco, d in _pairs()
        if eco == "docker"
    }
    on_disk = {
        p.parent.resolve()
        for p in REPO_ROOT.rglob("Dockerfile")
        # Skip vendored trees: .venv, node_modules, and anything git ignores.
        if not any(
            part in {".venv", "node_modules", ".git", "site-packages"}
            for part in p.parts
        )
    }
    missing = on_disk - watched
    assert not missing, (
        "these Dockerfiles are not covered by any dependabot docker entry: "
        + ", ".join(sorted(str(p.relative_to(REPO_ROOT)) for p in missing))
    )


def test_cooldown_applies_to_every_ecosystem() -> None:
    """The supply-chain cooldown (#39) should not be half-applied.

    Note it delays *proposals* only — Dependabot exempts security updates from
    cooldown, which is precisely why it is the right layer for this and a
    resolver-level `exclude-newer` is not (see the PR that added this file).
    """
    for entry in _updates():
        cooldown = entry.get("cooldown")
        assert cooldown and cooldown.get("default-days"), (
            f"{entry['package-ecosystem']} has no cooldown; a new release could "
            "be proposed the moment it is published"
        )
