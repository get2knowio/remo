# Contract: Documentation Structure Drift Check

**Implements**: FR-017, FR-018, FR-019, FR-019a, FR-020, FR-021
**Location**: `tests/unit/test_docs_structure.py`
**Runs in CI via**: the existing `test` job in `.github/workflows/ci.yml` (Python 3.11/3.12/3.13). **No
workflow file change.**

This contract is normative. The prose in `CLAUDE.md` is authoritative for *content*; this document is
authoritative for *format and enforcement*.

---

## 1. Inputs

| Input | Source |
|---|---|
| Documented structure | The first fenced ` ```text ` block following a `## Project Structure` heading, in each orientation document |
| Orientation documents | `CLAUDE.md`, `AGENTS.md` — parameterized; a document lacking the heading is **skipped, not failed**. ("Orientation document" is the spec's term; used throughout this contract for the same concept.) |
| Actual tree | `src/remo_cli/**/*.py`, repo-relative POSIX paths |
| Exclusions | `EXCLUDED_FROM_DOCS: frozenset[str]` declared in the test module |

**Scope boundary**: only paths under `src/remo_cli/` ending in `.py` participate. The diagram's
`frontend/`, `docker/`, `ansible/`, and `specs/` sections are illustrative and explicitly **out of
scope** — entries there are parsed and ignored, never reported.

---

## 2. Parsing rules

Given a line from the block:

```
│   ├── registry.py         # Registry v2 accessor: parse/serialize/...
└── prefix ──┘└─ name ─┘    └──────── description ────────┘
```

| Rule | Definition |
|---|---|
| **R-P1** | A line is an *entry line* iff it contains `── `. Others are root lines or blank. |
| **R-P2** | `prefix` is everything before the first `── `; `depth = len(prefix) // 4`. |
| **R-P3** | A *root line* matches `^([A-Za-z0-9_./-]+/)\s` and resets the parent stack at depth `-1`. |
| **R-P4** | `name` is the text after `── `, truncated at the first `#`, stripped, with any trailing `/` removed. |
| **R-P5** | A line's parent is the entry most recently seen at `depth - 1`. Full path is `parent + "/" + name`. |
| **R-P6** | An entry whose `name` ends in `/` (pre-strip) is a directory: it updates the parent stack but is not itself reported. |
| **R-P7** | Only entries whose reconstructed path starts with `src/remo_cli/` and ends with `.py` are collected. |

### Format errors (distinct from drift findings)

These fail the build with their own message, because they cause the check to **silently under-report**:

| Code | Condition | Rationale |
|---|---|---|
| **F-1** | An entry line's `name` contains `" / "` | Grouped multi-file lines cannot be path-reconstructed unambiguously. `CLAUDE.md` has two today (`incus.py / hetzner.py / aws.py / proxmox.py` and the `*_descriptor.py` equivalent); this feature splits them to one per line. |
| **F-2** | Two entries in one document reconstruct to the same path | A duplicate hides a drift. |
| **F-3** | A document has a `## Project Structure` heading but no fenced ` ```text ` block | Structure silently uncovered. |

**One path per line is mandatory.** Splitting on `" / "` was rejected: it is ambiguous with the
directory separator that R-P5 depends on.

---

## 3. Assertions

Let `D` = documented paths, `A` = actual paths, `X` = `EXCLUDED_FROM_DOCS`.

| ID | Assertion | Finding kind on failure |
|---|---|---|
| **A-1** | `D − A = ∅` | `phantom` — documented file does not exist |
| **A-2** | `A − D − X = ∅` | `undocumented` — real module nobody documented |
| **A-3** | `X ⊆ A` | `stale_exclusion` — exclusion outlived its file |
| **A-4** | `X ∩ D = ∅` | `stale_exclusion` — file both excluded and documented |

A-3 and A-4 are the anti-rot guards, mirroring `tests/unit/test_architecture.py`'s rule that an
allowlist entry with no corresponding site also fails. Without them the exclusion set becomes a place to
hide drift.

### `EXCLUDED_FROM_DOCS` policy

- Initial contents: the package-marker `__init__.py` files under `src/remo_cli/`, **except**
  `src/remo_cli/__init__.py`, which is meaningfully documented and stays in the diagram.
- Every entry carries a `#` comment giving the reason.
- This set is the FR-020 escape hatch. Adding to it is a reviewable one-line change; there is no flag or
  env var that disables the check wholesale.

---

## 4. Failure output

Required shape (FR-018, FR-019). Must name every offending path — never a bare `assert False` or a count.

```
CLAUDE.md: project-structure section is out of sync with the source tree.

  Documented but missing from the tree (2):
    - src/remo_cli/cli/init_cmd.py          (CLAUDE.md line 34)
    - src/remo_cli/core/init.py             (CLAUDE.md line 62)

  Present in the tree but undocumented (3):
    - src/remo_cli/cli/added.py
    - src/remo_cli/providers/added.py
    - src/remo_cli/web/operator_auth.py

To fix: add or remove the corresponding line in the "## Project Structure" block of CLAUDE.md,
one path per line. If a file is intentionally undocumented, add it to EXCLUDED_FROM_DOCS in
tests/unit/test_docs_structure.py with a reason.
See docs/maintaining-claude-md.md.
```

Requirements on the message:

| ID | Requirement |
|---|---|
| **M-1** | Names the document and the section |
| **M-2** | Lists every path, grouped by finding kind, with counts |
| **M-3** | Gives the document line number for `phantom` findings (the line to delete) |
| **M-4** | States the remediation for both directions **and** the exclusion escape hatch |
| **M-5** | Points at `docs/maintaining-claude-md.md` (FR-019a) |
| **M-6** | Is readable without opening the test module |

---

## 5. Test cases

Covering all four A-assertions plus the format errors — Principle II requires both branches of each
condition.

| ID | Setup | Expected |
|---|---|---|
| **T-1** | The real repository, post-fix | Passes. Zero findings. (FR-021, SC-003) |
| **T-2** | Synthetic block naming `src/remo_cli/core/nope.py` | Fails; message contains `nope.py` and its line number |
| **T-3** | Synthetic block omitting a real module | Fails; message contains the omitted path |
| **T-4** | `EXCLUDED_FROM_DOCS` containing a nonexistent path | Fails as `stale_exclusion` |
| **T-5** | A path both excluded and documented | Fails as `stale_exclusion` |
| **T-6** | A grouped `a.py / b.py` line | Fails as format error **F-1**, not as drift |
| **T-7** | Two lines reconstructing to the same path | Fails as format error **F-2** |
| **T-8** | A document with no `## Project Structure` heading | Skipped, not failed |
| **T-9** | Entries under `frontend/` and `ansible/` | Ignored; do not appear in any finding |

T-2 through T-7 operate on **synthetic in-memory document text**, not by mutating tracked files, so the
suite stays hermetic and parallel-safe.

**SC-008 acceptance** is a manual one-off distinct from T-3: add a real throwaway module to
`src/remo_cli/`, run the suite, confirm CI-shaped failure naming the file, then resolve it using only
`docs/maintaining-claude-md.md`.

---

## 6. Non-goals

- Verifying the *description* text after `#` — unenforceable, and reviewers catch it.
- Covering non-Python files or directories outside `src/remo_cli/`.
- Auto-fixing. The check reports; a human edits. An autofixer would let contributors merge structure
  changes without reading what they document.
- Validating `README.md`. Its content is prose, not a tree diagram.
