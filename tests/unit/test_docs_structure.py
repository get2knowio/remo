"""Documentation-structure drift gate (feature 019, User Story 4).

Parses the fenced ``## Project Structure`` block in each orientation
document (``CLAUDE.md``, ``AGENTS.md``) and diffs it against the real
``src/remo_cli/**/*.py`` tree. Fails the build if they drift.

Normative spec: specs/019-hygiene-deps-docs/contracts/docs-structure-check.md
(parsing rules R-P1..R-P7, format-error codes F-1/F-2/F-3, assertions
A-1..A-4, failure-message requirements M-1..M-6). This module implements
that contract exactly; do not deviate from it without updating the
contract first.

Contributor how-to for fixing a failure: docs/maintaining-claude-md.md.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "remo_cli"
SRC_PREFIX = "src/remo_cli/"

ENTRY_MARKER = "── "
ROOT_RE = re.compile(r"^([A-Za-z0-9_./-]+/)\s")
HEADING = "## Project Structure"
FENCE_OPEN = "```text"
FENCE_CLOSE = "```"

# ---------------------------------------------------------------------------
# EXCLUDED_FROM_DOCS -- the FR-020 escape hatch.
#
# Exactly the seven package-marker __init__.py files under src/remo_cli/
# that carry no content worth diagramming. src/remo_cli/__init__.py itself
# is NOT here: it is meaningfully documented ("Version from
# importlib.metadata") and stays a real entry in the diagram.
# ---------------------------------------------------------------------------

EXCLUDED_FROM_DOCS: frozenset[str] = frozenset(
    {
        "src/remo_cli/cli/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/cli/providers/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/core/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/models/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/providers/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/web/__init__.py",  # empty package marker, nothing to document
        "src/remo_cli/web/api/__init__.py",  # empty package marker, nothing to document
    }
)


# ---------------------------------------------------------------------------
# Parser (contract §2, rules R-P1..R-P7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatIssue:
    """A format error (F-1/F-2/F-3): reported instead of, not alongside,
    drift findings, because an unparseable block would otherwise
    silently under-report drift."""

    code: str  # "F-1" | "F-2" | "F-3"
    line: int | None
    detail: str


@dataclass
class StructureParseResult:
    has_heading: bool
    documented: dict[str, int] = field(default_factory=dict)  # path -> 1-based doc line
    format_issues: list[FormatIssue] = field(default_factory=list)


def parse_project_structure(text: str) -> StructureParseResult:
    """Parse the first fenced ```text block following a "## Project
    Structure" heading, per contract rules R-P1..R-P7."""
    lines = text.splitlines()

    heading_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == HEADING:
            heading_idx = i
            break
    if heading_idx is None:
        return StructureParseResult(has_heading=False)

    fence_start: int | None = None
    for i in range(heading_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == FENCE_OPEN:
            fence_start = i + 1
            break
        if stripped.startswith("## "):
            break  # hit the next heading before finding a fence: F-3
    if fence_start is None:
        return StructureParseResult(
            has_heading=True,
            format_issues=[
                FormatIssue(
                    "F-3",
                    heading_idx + 1,
                    f'"{HEADING}" heading found but no fenced {FENCE_OPEN!r} block '
                    "follows it before the next heading.",
                )
            ],
        )

    fence_end = len(lines)
    for i in range(fence_start, len(lines)):
        if lines[i].strip() == FENCE_CLOSE:
            fence_end = i
            break

    documented: dict[str, int] = {}
    format_issues: list[FormatIssue] = []
    seen_full_paths: dict[str, int] = {}
    stack: dict[int, str] = {}

    for offset, line in enumerate(lines[fence_start:fence_end]):
        doc_lineno = fence_start + offset + 1  # 1-based line number in the document

        if ENTRY_MARKER in line:  # R-P1: entry line
            idx = line.index(ENTRY_MARKER)
            prefix = line[:idx]  # R-P2
            depth = len(prefix) // 4
            rest = line[idx + len(ENTRY_MARKER):]
            name_stripped = rest.split("#", 1)[0].strip()  # R-P4 (truncate + strip)
            if not name_stripped:
                continue
            is_dir = name_stripped.endswith("/")  # R-P6 (checked pre-strip)
            name = name_stripped[:-1] if is_dir else name_stripped  # R-P4 (trailing / removed)

            if " / " in name:
                format_issues.append(
                    FormatIssue(
                        "F-1",
                        doc_lineno,
                        f'entry "{name_stripped}" groups multiple files on one line.',
                    )
                )
                continue  # cannot reconstruct a path unambiguously; nothing further to do

            parent = stack.get(depth - 1, "")  # R-P5
            full_path = f"{parent}/{name}" if parent else name

            if full_path in seen_full_paths:
                format_issues.append(
                    FormatIssue(
                        "F-2",
                        doc_lineno,
                        f'entry "{full_path}" was already reconstructed at line '
                        f"{seen_full_paths[full_path]}.",
                    )
                )
                continue

            seen_full_paths[full_path] = doc_lineno

            if is_dir:  # R-P6: updates parent stack, not itself reported
                stack[depth] = full_path
            elif full_path.startswith(SRC_PREFIX) and full_path.endswith(".py"):  # R-P7
                documented[full_path] = doc_lineno

        elif ROOT_RE.match(line):  # R-P3: root line resets the parent stack
            match = ROOT_RE.match(line)
            assert match is not None
            stack = {-1: match.group(1).rstrip("/")}

        # else: blank line or prose -- ignored

    return StructureParseResult(
        has_heading=True, documented=documented, format_issues=format_issues
    )


# ---------------------------------------------------------------------------
# Actual tree discovery
# ---------------------------------------------------------------------------


def discover_actual_py_paths() -> frozenset[str]:
    return frozenset(
        p.relative_to(REPO_ROOT).as_posix() for p in SRC_ROOT.rglob("*.py")
    )


# ---------------------------------------------------------------------------
# Assertions (contract §3, A-1..A-4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    kind: str  # "phantom" | "undocumented" | "stale_exclusion"
    path: str
    doc_line: int | None  # only meaningful for "phantom"


def compute_findings(
    documented: dict[str, int], actual: frozenset[str], excluded: frozenset[str]
) -> list[Finding]:
    doc_paths = set(documented)
    seen: dict[tuple[str, str], Finding] = {}

    for path in doc_paths - actual:  # A-1: D - A = empty -> phantom
        seen[("phantom", path)] = Finding("phantom", path, documented[path])
    for path in actual - doc_paths - excluded:  # A-2: A - D - X = empty -> undocumented
        seen[("undocumented", path)] = Finding("undocumented", path, None)
    for path in excluded - actual:  # A-3: X subset of A -> stale_exclusion
        seen[("stale_exclusion", path)] = Finding("stale_exclusion", path, None)
    for path in excluded & doc_paths:  # A-4: X intersect D = empty -> stale_exclusion
        seen[("stale_exclusion", path)] = Finding("stale_exclusion", path, None)

    return sorted(seen.values(), key=lambda f: (f.kind, f.path))


# ---------------------------------------------------------------------------
# Failure messages (contract §4, M-1..M-6)
# ---------------------------------------------------------------------------

_KIND_LABELS = {
    "phantom": "Documented but missing from the tree",
    "undocumented": "Present in the tree but undocumented",
    "stale_exclusion": "In EXCLUDED_FROM_DOCS but stale (file missing, or also documented)",
}
_KIND_ORDER = ("phantom", "undocumented", "stale_exclusion")


def render_failure_message(doc_name: str, findings: list[Finding]) -> str:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        grouped[f.kind].append(f)

    parts = [f'{doc_name}: "## Project Structure" section is out of sync with the source tree.']

    for kind in _KIND_ORDER:
        items = grouped.get(kind)
        if not items:
            continue
        parts.append(f"\n  {_KIND_LABELS[kind]} ({len(items)}):")
        width = max(len(f.path) for f in items)
        for f in items:
            if f.kind == "phantom":
                parts.append(f"    - {f.path.ljust(width)}  ({doc_name} line {f.doc_line})")
            else:
                parts.append(f"    - {f.path}")

    parts.append(
        f'\nTo fix: add or remove the corresponding line in the "## Project Structure" block of '
        f"{doc_name}, one path per line. If a file is intentionally undocumented, add it to "
        "EXCLUDED_FROM_DOCS in tests/unit/test_docs_structure.py with a reason.\n"
        "See docs/maintaining-claude-md.md."
    )
    return "\n".join(parts)


def render_format_issue_message(doc_name: str, issues: list[FormatIssue]) -> str:
    parts = [
        f'{doc_name}: "## Project Structure" section has a format problem that would cause '
        "drift detection to silently under-report."
    ]
    for issue in issues:
        where = f"{doc_name} line {issue.line}" if issue.line is not None else doc_name
        parts.append(f"\n  [{issue.code}] {where}: {issue.detail}")
    parts.append(
        "\nFormat error codes: F-1 = a grouped multi-file line ('a.py / b.py' -- list one path "
        "per line); F-2 = two lines reconstruct to the same path; F-3 = a \"## Project "
        'Structure" heading with no fenced ```text block beneath it.\n'
        "See docs/maintaining-claude-md.md."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Orchestration used by both the real-repository tests and the synthetic
# in-memory tests.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentCheckResult:
    skipped: bool
    failure_message: str | None


def check_document(
    text: str,
    doc_name: str,
    actual_paths: frozenset[str] | None = None,
    excluded: frozenset[str] | None = None,
) -> DocumentCheckResult:
    parsed = parse_project_structure(text)
    if not parsed.has_heading:
        return DocumentCheckResult(skipped=True, failure_message=None)

    if parsed.format_issues:
        return DocumentCheckResult(
            skipped=False,
            failure_message=render_format_issue_message(doc_name, parsed.format_issues),
        )

    actual = actual_paths if actual_paths is not None else discover_actual_py_paths()
    excl = excluded if excluded is not None else EXCLUDED_FROM_DOCS
    findings = compute_findings(parsed.documented, actual, excl)
    if not findings:
        return DocumentCheckResult(skipped=False, failure_message=None)

    return DocumentCheckResult(
        skipped=False, failure_message=render_failure_message(doc_name, findings)
    )


# ---------------------------------------------------------------------------
# T-1: the real repository, post-fix. Must pass with zero findings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("doc_name", ["CLAUDE.md", "AGENTS.md"])
def test_real_repository_structure_matches_docs(doc_name: str) -> None:
    text = (REPO_ROOT / doc_name).read_text(encoding="utf-8")
    result = check_document(text, doc_name)
    # NOT pytest.skip: `skipped` is the right answer for an *arbitrary*
    # document (T-8), but these two are hardcoded governed documents. Letting
    # them skip would silently disable the gate the moment someone renamed or
    # dropped the heading -- CI would stay green with zero coverage, which
    # contradicts docs/maintaining-claude-md.md's "no way to turn this off".
    assert not result.skipped, (
        f'{doc_name} has no "{HEADING}" heading, so the structure drift check '
        f"cannot run against it. This document is required to carry that "
        f"section -- restore the heading rather than removing the check. "
        f"See docs/maintaining-claude-md.md."
    )
    assert result.failure_message is None, result.failure_message


# ---------------------------------------------------------------------------
# T-2 .. T-9: synthetic in-memory documents. Hermetic -- no tracked file is
# ever mutated.
# ---------------------------------------------------------------------------


def _doc(*body_lines: str) -> str:
    """Wrap tree lines in a minimal '## Project Structure' section."""
    fenced = "\n".join(body_lines)
    return f"# Some Doc\n\n{HEADING}\n\n{FENCE_OPEN}\n{fenced}\n{FENCE_CLOSE}\n"


def test_t2_phantom_entry_reports_path_and_line() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── nope.py             # does not exist",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/config.py"}),
        excluded=frozenset(),
    )
    assert result.failure_message is not None
    assert "nope.py" in result.failure_message
    # The phantom entry is on line 8 of the synthetic document (1-based):
    # 1 "# Some Doc" / 2 "" / 3 heading / 4 "" / 5 fence / 6 root /
    # 7 "core/" / 8 "nope.py"
    assert "line 8" in result.failure_message


def test_t3_omitted_module_reports_undocumented() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── config.py           # documented",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset(
            {"src/remo_cli/core/config.py", "src/remo_cli/core/errors.py"}
        ),
        excluded=frozenset(),
    )
    assert result.failure_message is not None
    assert "src/remo_cli/core/errors.py" in result.failure_message


def test_t4_stale_exclusion_nonexistent_path() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── config.py           # documented",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/config.py"}),
        excluded=frozenset({"src/remo_cli/core/ghost.py"}),
    )
    assert result.failure_message is not None
    assert "src/remo_cli/core/ghost.py" in result.failure_message
    assert "stale" in result.failure_message.lower()


def test_t5_excluded_and_documented_is_contradictory() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── config.py           # documented",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/config.py"}),
        excluded=frozenset({"src/remo_cli/core/config.py"}),
    )
    assert result.failure_message is not None
    assert "src/remo_cli/core/config.py" in result.failure_message
    assert "stale" in result.failure_message.lower()


def test_t6_grouped_line_fails_as_format_error_not_drift() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── a.py / b.py         # grouped, not allowed",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/a.py", "src/remo_cli/core/b.py"}),
        excluded=frozenset(),
    )
    assert result.failure_message is not None
    assert "F-1" in result.failure_message
    assert "phantom" not in result.failure_message.lower()
    assert "undocumented" not in result.failure_message.lower()


def test_t7_duplicate_reconstructed_path_fails_as_format_error() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── config.py           # first",
        "│   ├── config.py           # duplicate",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/config.py"}),
        excluded=frozenset(),
    )
    assert result.failure_message is not None
    assert "F-2" in result.failure_message


def test_t8_document_without_heading_is_skipped_not_failed() -> None:
    text = "# Some Doc\n\nNo structure section here.\n"
    result = check_document(text, "TEST.md", actual_paths=frozenset(), excluded=frozenset())
    assert result.skipped is True
    assert result.failure_message is None


def test_t9_out_of_scope_trees_are_parsed_and_ignored() -> None:
    text = _doc(
        "src/remo_cli/              # root",
        "├── core/",
        "│   ├── config.py           # documented",
        "",
        "frontend/                  # root",
        "├── src/",
        "│   ├── api/client.ts       # not python, not in scope",
        "│   └── phantom-frontend-file.ts   # also not in scope, even though it doesn't exist",
        "",
        "docker/                    # root",
        "├── Dockerfile               # not python, not in scope",
        "",
        "ansible/                   # root",
        "├── roles/",
        "│   └── incus_bootstrap/",
    )
    result = check_document(
        text,
        "TEST.md",
        actual_paths=frozenset({"src/remo_cli/core/config.py"}),
        excluded=frozenset(),
    )
    assert result.failure_message is None
    assert result.skipped is False
