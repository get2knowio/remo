"""Sub-actions from one repository must be pinned to one SHA.

`github/codeql-action/init` and `.../analyze` ship from the same repository and
are version-locked to each other: running `init` from v4 and `analyze` from v3
fails the scan outright. Dependabot proposes a bump per *sub-action path*, so
PR #100 raised only `init` to v4 and both Analyze jobs failed — the mismatch is
not a hypothetical, it already happened once and will be proposed again on the
next release.

Encoding the invariant here rather than in review: the check is mechanical and
the failure mode (a half-upgraded scanner) is easy to merge by accident when
each individual line looks reasonable.

Applies to any `owner/repo/sub@sha` action used more than once in a workflow —
`actions/upload-artifact` vs `actions/download-artifact` would be the same
trap if both were ever used here.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: `uses: owner/repo[/sub]@ref` with an optional `# comment` version marker.
USES = re.compile(
    r"^\s*(?:-\s+)?uses:\s*(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)(?P<sub>(?:/[\w.-]+)*)@(?P<ref>\S+)"
)


def _workflow_files() -> list[Path]:
    files = sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))
    assert files, f"no workflows found under {WORKFLOW_DIR}"
    return files


def _pins_by_source_repo() -> dict[str, set[tuple[str, str]]]:
    """`owner/repo` -> {(ref, "path where it is used"), ...} across all workflows."""
    pins: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for path in _workflow_files():
        for line_no, line in enumerate(path.read_text().splitlines(), start=1):
            m = USES.match(line)
            if not m:
                continue
            source = f"{m['owner']}/{m['repo']}"
            where = f"{path.name}:{line_no}{m['sub']}"
            pins[source].add((m["ref"], where))
    return pins


def test_some_actions_were_found() -> None:
    # Guard against the regex silently matching nothing, which would make every
    # assertion below vacuously true.
    assert _pins_by_source_repo(), "no `uses:` action pins parsed — regex is broken"


@pytest.mark.parametrize("source", sorted(_pins_by_source_repo()))
def test_one_ref_per_action_repository(source: str) -> None:
    entries = _pins_by_source_repo()[source]
    refs = {ref for ref, _ in entries}
    if len(refs) == 1:
        return
    detail = "\n".join(f"    {ref}  <- {where}" for ref, where in sorted(entries, key=lambda e: e[1]))
    pytest.fail(
        f"{source} is pinned to {len(refs)} different refs across the workflows:\n"
        f"{detail}\n"
        "  Sub-actions from one repository are version-locked to each other "
        "(codeql-action init/analyze fail outright when their majors differ). "
        "Bump every line together, not one per Dependabot PR."
    )


def test_codeql_init_and_analyze_share_a_pin() -> None:
    """The specific pair that broke, named explicitly so the failure is obvious."""
    codeql = _pins_by_source_repo().get("github/codeql-action")
    assert codeql, "github/codeql-action is no longer used; drop this test"
    assert len({ref for ref, _ in codeql}) == 1, (
        "codeql-action init/analyze must run the same version — a v4 init with a "
        "v3 analyze fails the scan (this is exactly what PR #100 proposed)"
    )
