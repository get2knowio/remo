---
name: release
description: Cut a remo release — drive the release-please stable release PR, or build/validate/publish a pre-release (RC) — with a mandatory local "test before PyPI" gate and explicit approval before anything is tagged, merged, or published.
argument-hint: "[stable | rc [X.Y.ZrcN]] — omit to be asked"
metadata:
  author: remo
---

# Release

Operationalizes remo's two-lane release process (see
[CONTRIBUTING.md](../../../CONTRIBUTING.md) → Release Process). Pairs with the
release-please integration.

**Non-negotiable safety rules — apply in every mode:**

- **Publishing to PyPI is irreversible.** A version, once uploaded, can never be
  replaced. Run the local build + smoke gate (Step V below) before any tag,
  merge, or publish — no exceptions.
- **Never** `git push` a tag, `git push` a version-bump commit, or `gh pr merge`
  a release PR **without explicit user approval in the current turn.** Approval
  from an earlier task does not carry over.
- Work only from a **clean working tree** on an **up-to-date `main`**. If the
  tree is dirty or `main` is behind `origin/main`, stop and surface it.
- Prefer testing **without** publishing. Only publish an RC to PyPI when the user
  explicitly asks for a public prerelease.

## Step 0 — Determine the mode

From the argument, or by asking the user:

- **`stable`** — drive the release-please release PR to a published stable release.
- **`rc`** — build/validate a pre-release, optionally publishing it.

Then run `git fetch origin` and verify `git status` is clean and `main` is
up to date (`git rev-parse HEAD` == `git rev-parse origin/main`).

---

## Stable lane (release-please owns the version + tag)

1. `git checkout main && git pull --ff-only`.
2. Find the open release PR (release-please titles it `chore(main): release X.Y.Z`
   and pushes it to a `release-please--…` branch). Match on either signal —
   do **not** pass the title to `--search`, whose parser silently drops
   `chore(main):` and returns zero results even when the PR exists:
   ```bash
   gh pr list --state open --limit 100 --json number,title,headRefName,url --jq \
     '.[] | select((.headRefName | startswith("release-please--"))
                   or (.title | startswith("chore(main): release")))
      | "\(.number)\t\(.title)\t\(.url)"'
   ```
   If none exists, tell the user release-please has not opened one (no
   releasable `feat:`/`fix:` commits since the last release) and **stop**.
   Before concluding that, sanity-check with a bare `gh pr list --state open`:
   a filter bug and a genuinely absent PR look identical from here, and
   wrongly reporting "no release PR" strands a real one.
3. Show the proposed bump + changelog for review. `gh pr diff` takes no
   pathspec (`gh pr diff <n> -- <files>` fails with "accepts at most 1 arg"),
   so select the files from the API instead:
   ```bash
   gh api repos/{owner}/{repo}/pulls/<number>/files --paginate --jq \
     '.[] | select(.filename == "pyproject.toml" or .filename == "CHANGELOG.md")
      | "=== \(.filename) ===\n\(.patch)"'
   ```
   Read the `⚠ BREAKING CHANGES` block carefully — it is what drives a major
   bump, and a spurious entry there is the usual cause of an unintended one.
4. **Run the validation gate (Step V) on the PR's head branch** so you build the
   exact version that will publish.
5. **Only on explicit approval**, merge it (release-please then tags `vX.Y.Z`,
   which triggers `release.yml` to publish to PyPI + GHCR):
   ```bash
   gh pr merge <number> --squash
   ```
   Before merging, confirm the `RELEASE_PLEASE_TOKEN` secret is set — without it
   the tag will not trigger the publish. If it's missing, warn the user and let
   them decide.
6. Return to `main`, `git pull --ff-only`, report the new tag, and offer to watch
   the release CI. `gh run watch` needs an explicit run id outside a TTY:
   ```bash
   gh run watch "$(gh run list --workflow=release.yml --limit 1 \
                     --json databaseId --jq '.[0].databaseId')"
   ```

---

## RC lane (manual — release-please stays out)

1. `git checkout main && git pull --ff-only`.
2. Determine the RC version `X.Y.ZrcN` (PEP 440 form, **no separator**):
   - `X.Y.Z` is the next target version (feat → minor, fix → patch over the last
     stable tag).
   - `N` increments from the last `vX.Y.Z-rcM` tag for the same `X.Y.Z`
     (`git tag --list "vX.Y.Z-rc*"`), else `1`.
   - Confirm the chosen version with the user.
3. Confirm these two files carry no pre-existing edits, then bump
   `pyproject.toml` `[project].version` to `X.Y.ZrcN` (optionally run `uv lock`
   to keep the lockfile's version in step):
   ```bash
   git diff --quiet -- pyproject.toml uv.lock \
     || { echo "pyproject.toml/uv.lock already modified — STOP"; }
   ```
   Step 0 already required a clean tree, but re-assert it here: the revert in
   Step 5 discards whatever is in these files, and the gap between Step 0 and
   Step 5 spans a full test run.
4. **Run the validation gate (Step V).**
5. Ask the user which outcome they want:
   - **Local test only (default, no PyPI):** revert the bump, then hand off the
     built wheel (`dist/*.whl`) or the git-install one-liner
     (`uv tool install --force "git+https://github.com/get2knowio/remo.git@<branch>"`).
     Nothing is committed, tagged, or published.

     Look before discarding — never `git checkout`/`git restore` these paths
     blind. Print the diff, confirm it is **only** the version bump you made in
     Step 3, and only then restore:
     ```bash
     git diff -- pyproject.toml uv.lock   # MUST show only the X.Y.ZrcN bump
     git restore --source=HEAD --worktree -- pyproject.toml uv.lock
     ```
     If that diff contains anything else, stop and surface it: something edited
     these files during the run, and discarding it destroys unrecoverable work.
   - **CI dev build for cross-machine testing (no PyPI):** trigger the
     `dev-build.yml` workflow, which builds the wheel in clean CI and uploads it
     as a run artifact. The stamped version carries a `+g<sha>` local segment, so
     it is unique and can never reach PyPI. Nothing is committed or tagged.
     ```bash
     gh workflow run dev-build.yml -f version=X.Y.ZrcN   # omit -f for an auto dev version
     # `gh run watch` with no argument fails outside a TTY ("run ID required
     # when not running interactively"), so resolve the run id first. Give the
     # dispatch a couple of seconds to register before listing.
     RUN_ID="$(gh run list --workflow=dev-build.yml --limit 1 \
                 --json databaseId --jq '.[0].databaseId')"
     gh run watch "$RUN_ID"
     # then, on ANY machine:
     gh run download "$RUN_ID" -n remo-wheel -D ./dl
     uv tool install --force ./dl/remo_cli-*.whl
     ```
   - **Publish a prerelease (only on explicit approval):**
     ```bash
     git commit -am "chore(release): X.Y.ZrcN"
     git tag vX.Y.Z-rcN
     git push origin main vX.Y.Z-rcN
     ```
     `release.yml` detects the `rc` suffix, publishes the prerelease to PyPI +
     GHCR, and never moves `latest`. Offer to watch CI.

---

## Step V — Validation gate (test the exact wheel before PyPI)

Run this on whatever ref will be released (the release-please PR head for stable,
or the bumped working tree for an RC). The published version comes from
`pyproject.toml` via `uv build`, so this builds the identical artifact:

```bash
uv run pytest -q                    # full suite must pass
uv build                            # -> dist/remo_cli-<version>-py3-none-any.whl
VENV="$(mktemp -d)/venv"
uv venv "$VENV"
uv pip install --python "$VENV/bin/python" ./dist/remo_cli-<version>-py3-none-any.whl
"$VENV/bin/remo" --version          # MUST equal <version>
"$VENV/bin/remo" --help             # sanity-check the CLI loads
```

Report the results. If tests fail, the wheel version doesn't match, or the CLI
doesn't load, **stop** and surface it — do not proceed to tag/merge/publish.

## Done when

- The requested lane completed through the point the user approved (validated
  only; RC wheel handed off; RC published; or stable PR merged).
- No tag was pushed, commit was pushed, or PR merged without explicit approval.
- The outcome (and, if published, the immutable version) was reported clearly.
