"""Tests for dev-build.yml's pre-release inputs.

A pre-release should be a *complete* artifact: the wheel and the container image
for one version, or neither. Publishing the image used to be opt-in
(`image: default: false`), so an RC could carry a wheel with no
`ghcr.io/get2knowio/remo-web:<version>` behind it — and nothing on the GitHub
pre-release said which half you had. Whoever pinned a Compose deployment to it
found out when the pull 404'd.

Workflows are read as text rather than with PyYAML, following
tests/unit/test_workflow_action_pins.py: PyYAML is present here only
transitively, via ansible-core.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_BUILD_YML = REPO_ROOT / ".github" / "workflows" / "dev-build.yml"
RC_IMAGE_YML = REPO_ROOT / ".github" / "workflows" / "rc-image.yml"


def _input_block(name: str) -> str:
    """The YAML block for one `workflow_dispatch` input, by name."""
    lines = DEV_BUILD_YML.read_text(encoding="utf-8").splitlines()

    start = next((i for i, line in enumerate(lines) if line.strip() == f"{name}:"), None)
    assert start is not None, f"input {name!r} not found in dev-build.yml"

    indent = len(lines[start]) - len(lines[start].lstrip())
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) <= indent:
            break
        block.append(line)
    return "\n".join(block)


def _code_only(block: str) -> str:
    return "\n".join(
        line for line in block.splitlines() if not line.lstrip().startswith("#")
    )


class TestImageIsOnByDefault:
    def test_image_defaults_to_true(self) -> None:
        """So `-f prerelease=true` alone yields both halves of the artifact."""
        code = _code_only(_input_block("image"))
        assert re.search(r"^\s*default:\s*true\s*$", code, re.MULTILINE), (
            "dev-build.yml's `image` input must default to true — a pre-release "
            f"wheel without its image is a partial artifact. Got:\n{code}"
        )

    def test_the_opt_out_is_documented(self) -> None:
        """Skipping the emulated arm64 build has to remain discoverable, or the
        only way to avoid ~15 minutes of QEMU is to know the input exists."""
        description = _input_block("image")
        assert "false" in description, "the input description should name the opt-out"
        source = DEV_BUILD_YML.read_text(encoding="utf-8")
        assert "-f image=false" in source, (
            "the header's trigger examples should show the opt-out"
        )

    def test_prerelease_still_defaults_off(self) -> None:
        """Flipping `image` must not make a plain dev build start tagging things.
        `prerelease` is what creates the git tag and the GitHub release."""
        code = _code_only(_input_block("prerelease"))
        assert re.search(r"^\s*default:\s*false\s*$", code, re.MULTILINE), (
            f"`prerelease` must stay opt-in. Got:\n{code}"
        )


class TestImageStillGatedOnPrerelease:
    def test_image_job_requires_both_inputs(self) -> None:
        """With `image` now defaulting true, this condition is the only thing
        keeping a plain dev build (no version, no tag) from publishing an image
        for a `+g<sha>` local version that could never be deployed anyway."""
        source = DEV_BUILD_YML.read_text(encoding="utf-8")
        assert "if: ${{ inputs.prerelease && inputs.image }}" in source, (
            "the image job must remain gated on prerelease AND image"
        )

    def test_image_job_delegates_to_rc_image(self) -> None:
        source = DEV_BUILD_YML.read_text(encoding="utf-8")
        assert "uses: ./.github/workflows/rc-image.yml" in source

    def test_rc_image_never_moves_latest(self) -> None:
        """The reason an RC image is safe to publish by default: it can never
        become what docker/compose.example.yml pulls."""
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        assert "latest=false" in source, (
            "rc-image.yml must keep `flavor: latest=false` — an RC image that "
            "moved `latest` would be pulled by every default Compose deployment"
        )

    def test_rc_image_is_still_dispatchable_alone(self) -> None:
        """For an `rc-*` tag cut before this default changed — e.g. rc-4.4.0rc3,
        which has a wheel and no image."""
        source = RC_IMAGE_YML.read_text(encoding="utf-8")
        assert "workflow_dispatch:" in source
        assert "workflow_call:" in source
