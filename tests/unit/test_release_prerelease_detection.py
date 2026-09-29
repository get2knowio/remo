"""Tests for release.yml's pre-release detection.

This step decides whether a published release is marked a pre-release, which in
turn decides whether `latest` moves on GHCR. It used to be a bare substring
test::

    if [[ "$TAG" == *"rc"* ]] || [[ "$TAG" == *"beta"* ]] || ...

so any tag merely *containing* those letters was classified a pre-release —
`v1.0.0-hotfix-search` matches on the "rc" inside "search". Nothing in CI could
catch that, because the step only ever runs on a real tag push.

Rather than re-implement the condition, these tests extract the actual `run:`
block from the workflow and execute it under bash. A copy would drift; this
cannot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RELEASE_YML = REPO_ROOT / ".github" / "workflows" / "release.yml"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

# (tag, is_prerelease)
#
# The canonical PEP 440 forms are what remo actually produces: pyproject.toml
# carries `4.4.0rc3`, so a promoted RC is tagged `v4.4.0rc3`. The dashed forms
# are the git-tag convention other projects in the portfolio use, and are
# accepted so a hand-pushed tag in either style behaves.
TAG_CASES: list[tuple[str, bool]] = [
    # --- stable releases: every one of these must publish as stable ---
    ("v4.3.6", False),
    ("v4.4.0", False),
    ("v10.20.30", False),
    ("v1.0.0", False),
    # --- canonical PEP 440 pre-releases (no separator) ---
    ("v4.4.0rc1", True),
    ("v4.4.0rc3", True),
    ("v4.4.0rc10", True),
    ("v4.4.0a1", True),
    ("v4.4.0b2", True),
    ("v4.4.0c1", True),
    ("v4.4.0.dev5", True),
    ("v4.4.0.dev12", True),
    # --- dashed convenience forms ---
    ("v4.4.0-rc1", True),
    ("v4.4.0-beta2", True),
    ("v4.4.0-alpha1", True),
    ("v4.4.0-dev3", True),
    # --- the regressions the old substring test got wrong ---
    # "rc" inside "search": was a pre-release, must be stable.
    ("v1.0.0-hotfix-search", False),
    # "dev" inside "device": was a pre-release, must be stable.
    ("v2.0.0-device-support", False),
    # "alpha" inside "alphabet", "beta" inside "betamax".
    ("v3.0.0-alphabetical", False),
    ("v3.0.0-betamax", False),
    # A trailing digit alone is not a pre-release marker.
    ("v1.2.3-4", False),
]


def _extract_run_block(step_name: str) -> str:
    """Return the dedented shell of the named step's `run: |` block.

    Text parsing rather than PyYAML on purpose: PyYAML is only present here
    transitively (via ansible-core), and tests/unit/test_workflow_action_pins.py
    already set the precedent of reading workflows as text.
    """
    lines = RELEASE_YML.read_text(encoding="utf-8").splitlines()

    start = next(
        (i for i, line in enumerate(lines) if line.strip() == f"- name: {step_name}"),
        None,
    )
    assert start is not None, f"step {step_name!r} not found in release.yml"

    run_at = next(
        (i for i in range(start, len(lines)) if lines[i].strip() == "run: |"), None
    )
    assert run_at is not None, f"no `run: |` block under {step_name!r}"

    body_indent = len(lines[run_at]) - len(lines[run_at].lstrip())
    body: list[str] = []
    for line in lines[run_at + 1 :]:
        if line.strip() and (len(line) - len(line.lstrip())) <= body_indent:
            break
        body.append(line)

    script = textwrap.dedent("\n".join(body))
    assert script.strip(), f"empty run block for {step_name!r}"
    return script


def _code_only(script: str) -> str:
    """The script with comment-only lines dropped.

    The workflow comments deliberately quote the old buggy patterns to explain
    what was replaced, so an assertion that a pattern is *gone* has to look at
    the code alone or it matches the explanation instead.
    """
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _classify(tag: str, tmp_path: Path) -> bool:
    """Run the real step against `tag` and return its is_prerelease output."""
    script = _extract_run_block("Check if pre-release")
    # The only workflow expression in the block is the tag; everything else is
    # plain shell. Substituting it keeps the condition itself untouched.
    script = script.replace('"${{ steps.tag.outputs.tag }}"', '"$TEST_TAG"')
    assert "${{" not in script, f"unsubstituted workflow expression: {script!r}"

    output = tmp_path / "gh_output"
    output.write_text("", encoding="utf-8")

    assert BASH is not None
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "TEST_TAG": tag, "GITHUB_OUTPUT": str(output)},
    )
    assert result.returncode == 0, f"step failed for {tag!r}: {result.stderr}"

    written = output.read_text(encoding="utf-8")
    if "is_prerelease=true" in written:
        return True
    if "is_prerelease=false" in written:
        return False
    raise AssertionError(f"step wrote no is_prerelease for {tag!r}: {written!r}")


class TestPrereleaseDetection:
    @pytest.mark.parametrize(("tag", "expected"), TAG_CASES)
    def test_tag_classification(self, tag: str, expected: bool, tmp_path: Path) -> None:
        assert _classify(tag, tmp_path) is expected, (
            f"{tag} should {'' if expected else 'NOT '}be a pre-release"
        )

    def test_the_substring_test_is_gone(self) -> None:
        """The specific shape of the original bug."""
        code = _code_only(_extract_run_block("Check if pre-release"))
        assert '== *"rc"*' not in code
        assert '== *"beta"*' not in code

    def test_detection_is_anchored(self) -> None:
        """Every alternative must match at the tag's end, or a random substring
        match creeps back in."""
        code = _code_only(_extract_run_block("Check if pre-release"))
        conditions = [line for line in code.splitlines() if "=~" in line]
        assert conditions, "no regex conditions found"
        for line in conditions:
            assert "$ ]]" in line, f"unanchored condition: {line!r}"


class TestPreviousStableTagFilter:
    """The adjacent step picks the previous *stable* tag for the release-notes
    range. It carried the same unanchored filter, so it is checked against the
    same table."""

    def _pre_re(self) -> str:
        script = _extract_run_block("Get previous tag")
        match = re.search(r"PRE_RE='([^']+)'", script)
        assert match, "PRE_RE not found in the Get previous tag step"
        return match.group(1)

    @pytest.mark.parametrize(("tag", "expected"), TAG_CASES)
    def test_filter_agrees_with_detection(self, tag: str, expected: bool) -> None:
        """A tag the detection step calls a pre-release must also be excluded
        from the stable-tag candidates, and vice versa. If the two drift, the
        release notes silently compare against the wrong tag."""
        assert BASH is not None
        result = subprocess.run(
            [BASH, "-c", 'printf "%s\\n" "$1" | grep -qE "$2"', "_", tag, self._pre_re()],
            capture_output=True,
            text=True,
            timeout=30,
        )
        matched = result.returncode == 0
        assert matched is expected, (
            f"PRE_RE {'matched' if matched else 'did not match'} {tag}, "
            f"but the detection step says is_prerelease={expected}"
        )

    def test_restricted_to_the_release_namespace(self) -> None:
        """`git tag -l` also lists dev-build.yml's `rc-*` GitHub-pre-release
        tags, which are not releases and must never be a `prev_tag` candidate."""
        script = _extract_run_block("Get previous tag")
        assert "git tag -l 'v*'" in script

    def test_old_unanchored_filter_is_gone(self) -> None:
        code = _code_only(_extract_run_block("Get previous tag"))
        assert "grep -v -E '(rc|beta|alpha|dev)'" not in code
