# Maintaining the `## Project Structure` sections

`CLAUDE.md` and `AGENTS.md` each contain a `## Project Structure` section: a fenced
` ```text ` block that diagrams the repository as an ASCII tree. An automated,
CI-gating check — `tests/unit/test_docs_structure.py` — parses that block and diffs it
against the real `src/remo_cli/**/*.py` tree on every `uv run pytest`. If a `.py` file is
added, removed, or the diagram falls out of sync, the build fails naming the exact file.

This doc is the fix-it guide. The full parsing/format rules the check enforces live in
`specs/019-hygiene-deps-docs/contracts/docs-structure-check.md`; you shouldn't need to
read that (or the test module) to fix a normal failure — this page should be enough.

## Scope

Only paths under `src/remo_cli/` ending in `.py` are checked. The `frontend/`, `docker/`,
`ansible/`, and `specs/` sections of the diagram are illustrative — they're parsed but
never diffed, so you can edit them freely without tripping the check.

## Format rules (read this before editing)

- **One path per line.** Never write `a.py / b.py` on one line — the check treats a
  `" / "` inside an entry as a format error (F-1) and refuses to guess. Give each file
  its own line.
- **Indentation is 4 spaces per depth level**, expressed with the usual tree-drawing
  characters (`├──`, `└──`, `│   `). The check derives an entry's nesting depth purely
  from how many characters precede `── ` on the line, divided by 4 — so keep each
  directory's children indented one full level (4 characters) deeper than the directory
  itself. Example:

  ```text
  src/remo_cli/
  ├── core/                  # a directory (ends in "/" — not itself checked)
  │   ├── config.py          # a file, one level under core/
  │   └── errors.py
  └── models/
      └── host.py
  ```

- **A trailing `#` starts the description.** Everything before the first `#` on an entry
  line is its name; everything after is free-text commentary the check ignores. Keep
  descriptions accurate, but you can write anything there — it's not verified.
- **Don't reconstruct the same path twice.** Two lines whose directory nesting plus name
  produce the identical path is a format error (F-2), usually caused by a copy/paste
  slip or a directory line miscounted at the wrong depth.

## Adding a structure entry

1. Find (or create) the directory line for the file's parent directory in the diagram,
   indented at the right depth.
2. Add a new line for the file, one level deeper than its parent directory, with a short
   `#` description of what it does.
3. Do this in **both** `CLAUDE.md` and `AGENTS.md` — they're hand-maintained in parallel
   and the check runs against each independently.
4. Run `uv run pytest tests/unit/test_docs_structure.py -q` to confirm the entry
   resolves.

## Removing a structure entry

Delete the line for the file that no longer exists. The check's failure message tells
you exactly which document and line number to delete — for example:

```text
CLAUDE.md: "## Project Structure" section is out of sync with the source tree.

  Documented but missing from the tree (1):
    - src/remo_cli/core/init.py             (CLAUDE.md line 62)
```

Open `CLAUDE.md`, delete line 62, and re-run the check.

## Deliberately excluding a file

Some files (package-marker `__init__.py`s, for example) genuinely have nothing worth
documenting. Rather than adding a hollow diagram entry, add the file's full repo-relative
path to `EXCLUDED_FROM_DOCS` in `tests/unit/test_docs_structure.py`, with a short `#`
reason:

```python
EXCLUDED_FROM_DOCS: frozenset[str] = frozenset(
    {
        "src/remo_cli/cli/__init__.py",  # empty package marker, nothing to document
        ...
    }
)
```

Rules the check enforces on this set, so it can't rot into a place to hide real drift:

- Every excluded path must still exist (`X ⊆ A`) — if the file is deleted, remove its
  exclusion entry too, or the check fails as a stale exclusion.
- A path can't be both excluded **and** documented in the diagram (`X ∩ D = ∅`) — pick
  one.

This is the only escape hatch. There is no flag or environment variable that disables
the check, and deleting or renaming a document's `## Project Structure` heading does not
work either — `CLAUDE.md` and `AGENTS.md` are asserted to carry that section, so removing
it fails the build rather than quietly skipping the document.

## Where the check lives

`tests/unit/test_docs_structure.py`. It runs automatically as part of `uv run pytest`,
which already gates every pull request via the `test` job in
`.github/workflows/ci.yml` (Python 3.11/3.12/3.13) — no separate CI step is needed.

To run just this check locally:

```bash
uv run pytest tests/unit/test_docs_structure.py -v
```
