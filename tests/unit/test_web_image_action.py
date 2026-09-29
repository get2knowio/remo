"""The remo-web image has one build path.

`release.yml` (stable, from a `v*` tag) and `rc-image.yml` (pre-release, from an
`rc-*` tag) previously each carried their own copy of the same five `docker/*`
steps, pinned SHAs included. Dependabot proposes a bump per file, so #212 had to
raise the same three pins in both — and a bump landing in only one would leave
the two channels building with different tooling, with nothing to notice. Same
failure class as the codeql-action mismatch behind
tests/unit/test_workflow_action_pins.py.

Both now call `.github/actions/publish-web-image`. These tests hold that shape
in place, and hold the two properties that make sharing safe: the `latest` guard
rail, and rc-image.yml's two-checkout arrangement.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ACTION = REPO_ROOT / ".github" / "actions" / "publish-web-image" / "action.yml"
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"
RC_IMAGE_YML = REPO_ROOT / ".github" / "workflows" / "rc-image.yml"

CALLERS = [RELEASE_YML, RC_IMAGE_YML]

#: `uses: owner/repo@ref`, ignoring local `./…` references.
USES_RE = re.compile(r"uses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)@(\S+)")


def test_the_action_exists() -> None:
    assert ACTION.is_file(), f"{ACTION} is missing"


class TestSingleBuildPath:
    @pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.name)
    def test_caller_has_no_direct_docker_steps(self, workflow: Path) -> None:
        """If a `docker/*` step reappears in a workflow, the duplication is back
        and the two channels can drift again."""
        source = workflow.read_text(encoding="utf-8")
        direct = [
            line.strip()
            for line in source.splitlines()
            if re.search(r"uses:\s*docker/", line)
        ]
        assert not direct, (
            f"{workflow.name} builds images directly instead of going through "
            f".github/actions/publish-web-image: {direct}"
        )

    @pytest.mark.parametrize("workflow", CALLERS, ids=lambda p: p.name)
    def test_caller_uses_the_shared_action(self, workflow: Path) -> None:
        source = workflow.read_text(encoding="utf-8")
        assert "uses: ./.github/actions/publish-web-image" in source

    def test_the_action_owns_every_docker_step(self) -> None:
        """The five steps that were duplicated."""
        source = ACTION.read_text(encoding="utf-8")
        for step in (
            "docker/setup-qemu-action",
            "docker/setup-buildx-action",
            "docker/login-action",
            "docker/metadata-action",
            "docker/build-push-action",
        ):
            assert step in source, f"{step} missing from the shared action"

    def test_the_actions_pins_are_shas(self) -> None:
        """The repo pins every action to a full 40-character commit SHA. The
        shared action is outside tests/unit/test_workflow_action_pins.py's
        workflow glob, so the convention is enforced here instead."""
        for owner_repo, ref in USES_RE.findall(ACTION.read_text(encoding="utf-8")):
            assert re.fullmatch(r"[0-9a-f]{40}", ref), (
                f"{owner_repo} is pinned to {ref!r}, not a 40-character SHA"
            )


class TestLatestGuardRail:
    def test_flavor_latest_false_is_in_the_shared_action(self) -> None:
        """Hardcoded rather than an input, so a pre-release has no route to
        `latest` at all. That is what makes dev-build.yml's `image: default:
        true` safe — an RC image can never become what
        docker/compose.example.yml pulls."""
        assert "flavor: latest=false" in ACTION.read_text(encoding="utf-8")

    def test_only_the_stable_path_can_add_latest(self) -> None:
        release = RELEASE_YML.read_text(encoding="utf-8")
        assert "type=raw,value=latest,enable=" in release, (
            "release.yml should re-add `latest` through an explicit, conditional raw tag"
        )
        assert "is_prerelease == 'false'" in release, (
            "`latest` must be conditional on the release NOT being a pre-release"
        )

    def test_the_prerelease_path_never_mentions_latest(self) -> None:
        """Only its own version, never `latest` or `major.minor`."""
        rc = RC_IMAGE_YML.read_text(encoding="utf-8")
        tag_specs = [
            line for line in rc.splitlines() if line.strip().startswith("type=")
        ]
        assert tag_specs, "no tag-spec found in rc-image.yml"
        for line in tag_specs:
            assert "latest" not in line, f"pre-release tag-spec reaches latest: {line!r}"
            assert "{{major}}" not in line, f"pre-release tag-spec is a moving tag: {line!r}"


class TestRcImageCheckoutOrder:
    """A local `uses: ./…` action is read from the checked-out WORKING TREE, not
    from the commit the workflow came from. rc-image.yml builds an arbitrary
    `rc-*` tag, so if it checked that tag out over the workspace root, the shared
    action would vanish for any tag cut before the action existed — including
    rc-4.4.0rc1..rc3, which is exactly the backfill case the workflow is for.
    """

    def test_own_tree_is_checked_out_at_the_root_first(self) -> None:
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        checkouts = [
            i for i, line in enumerate(source.splitlines())
            if "uses: actions/checkout@" in line
        ]
        assert len(checkouts) == 2, (
            "rc-image.yml needs exactly two checkouts: its own tree at the root "
            f"(for the shared action) and the target ref beside it. Found {len(checkouts)}."
        )

        lines = source.splitlines()
        first_block = "\n".join(lines[checkouts[0] : checkouts[1]])
        assert "path:" not in first_block, (
            "the FIRST checkout must land at the workspace root, so "
            "./.github/actions/publish-web-image resolves"
        )
        assert "ref:" not in first_block, (
            "the first checkout must be this workflow's own ref, not the target tag"
        )

    def test_target_ref_is_checked_out_beside_it(self) -> None:
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        assert "path: src" in source
        assert "ref: ${{ steps.ver.outputs.ref }}" in source

    def test_the_build_reads_from_that_subdirectory(self) -> None:
        """Otherwise it would build this workflow's tree, not the rc tag's."""
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        assert "context: src" in source
        assert "dockerfile: src/docker/Dockerfile" in source

    def test_the_version_stamp_targets_the_subdirectory(self) -> None:
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        assert "src/pyproject.toml" in source, (
            "the stamp must edit the checked-out rc tag's pyproject.toml, not "
            "this workflow's own"
        )
