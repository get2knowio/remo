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
  replaced. Run the local build + smoke gate (Step V below) before anything that
  reaches PyPI — a stable release PR merge, or RC outcome **D**.
- **A `v*` tag publishes to PyPI. An `rc-*` tag does not.** `release.yml`
  triggers on `tags: v*`; the GitHub-pre-release path (RC outcome **B**) tags
  `rc-<version>` and never uploads. Check which namespace you are in before
  pushing anything.
- **Never** `git push` a tag, `git push` a version-bump commit, or `gh pr merge`
  a release PR **without explicit user approval in the current turn.** Approval
  from an earlier task does not carry over. Dispatching `dev-build.yml` is not in
  this class — it neither commits nor uploads — but say what it will tag.
- Work only from a **clean working tree** on an **up-to-date `main`**. If the
  tree is dirty or `main` is behind `origin/main`, stop and surface it. A dirty
  file the operator owns and you did not touch is theirs: report it, never
  revert it.
- Prefer testing **without** publishing. "Publish a pre-release" in this repo
  means outcome **B** (GitHub) unless the user says PyPI. Never infer PyPI from
  the word "publish".

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

**Two tag namespaces, and confusing them is the one unrecoverable mistake here:**

| Tag | Created by | Publishes to |
|-----|-----------|--------------|
| `rc-X.Y.ZrcN` | `dev-build.yml` (`prerelease=true`) | **GitHub pre-release only** |
| `vX.Y.Z*` | release-please, or a hand-pushed tag | **PyPI + GHCR** (`release.yml`) |

`release.yml` triggers on `tags: v*`. A `v`-prefixed RC tag therefore uploads to
PyPI, which is irreversible; `dev-build.yml` refuses to create one for exactly
that reason. Outcome B below is what "publish a pre-release so we can install it
by version" means in this repo — **not** a `v` tag.

1. `git checkout main && git pull --ff-only`.
2. Determine the RC version `X.Y.ZrcN` (PEP 440 form, **no separator**):
   - `X.Y.Z` is the next target version (feat → minor, fix → patch over the last
     stable tag).
   - `N` increments from the last RC for the same `X.Y.Z`. The tags are
     `rc`-prefixed, so: `git tag --list "rc-X.Y.Zrc*"`, else `1`.
   - Confirm the chosen version with the user.
3. Ask which outcome they want, then follow only that one. **A** is the default;
   **B** is what to reach for when someone wants to install the RC by version on
   another machine.

### A — Local validation only (nothing tagged, nothing published)

Bump `pyproject.toml` `[project].version` to `X.Y.ZrcN` and run `uv lock` so the
lockfile agrees (`tests/unit/test_lockfile_version.py` gates that pair). First
confirm neither file already carries edits — the revert below discards whatever
is in them, and a full test run separates the two moments:

```bash
git diff --quiet -- pyproject.toml uv.lock \
  || { echo "pyproject.toml/uv.lock already modified — STOP"; }
```

Run the validation gate (**Step V**), then hand off `dist/*.whl` or the Tier 1
one-liner
(`uv tool install --force "git+https://github.com/get2knowio/remo.git@<branch>"`).

Then revert. Look before discarding — never `git restore` these paths blind:

```bash
git diff -- pyproject.toml uv.lock   # MUST show only the X.Y.ZrcN bump
git restore --source=HEAD --worktree -- pyproject.toml uv.lock
```

If that diff contains anything else, stop and surface it: something edited these
files during the run, and discarding it destroys unrecoverable work.

### B — GitHub pre-release, installable by version (no PyPI)

The usual way to get an RC onto other machines. `dev-build.yml` stamps the
version in CI, so **no local bump is needed** and nothing is committed. The
`prerelease` job attaches the *same* wheel the build job produced — it does not
rebuild — to a GitHub pre-release tagged `rc-X.Y.ZrcN`.

```bash
gh workflow run dev-build.yml --ref main -f version=X.Y.ZrcN -f prerelease=true
# This also publishes ghcr.io/get2knowio/remo-web:X.Y.ZrcN (never `latest`) for
# Compose-based deployments — `image` defaults to true, so an RC is a complete
# artifact rather than a wheel with no image behind it. Add -f image=false for a
# CLI-only RC, which skips the emulated arm64 build.

# `gh run watch` needs an explicit run id outside a TTY. Give the dispatch a
# couple of seconds to register before listing.
RUN_ID="$(gh run list --workflow=dev-build.yml --limit 1 \
            --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID"
```

Then **validate the published artifact**, which is stronger than a pre-build
local check because it is the wheel testers will actually fetch. Use a throwaway
venv so the operator's own install is untouched:

```bash
URL="https://github.com/get2knowio/remo/releases/download/rc-X.Y.ZrcN/remo_cli-X.Y.ZrcN-py3-none-any.whl"
uv venv /tmp/remo-rc && uv pip install --python /tmp/remo-rc/bin/python "remo-cli @ $URL"
/tmp/remo-rc/bin/remo --version    # MUST print X.Y.ZrcN
/tmp/remo-rc/bin/remo --help
```

Confirm with `gh release view rc-X.Y.ZrcN` that it is marked pre-release, and
report that PyPI is unchanged. Testers install it with no `gh` auth:

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --prerelease X.Y.ZrcN
# or directly:
uv tool install --force "remo-cli @ <URL above>"
```

A GitHub pre-release is deletable and the version re-cuttable, so this outcome is
recoverable — unlike D.

### C — Dev build artifact (no version reserved, no tag)

For cross-machine testing when no version should be claimed at all. Omit
`version` and the workflow stamps `X.Y.Z.devN+g<sha>`; that PEP 440 **local
segment** is what PyPI rejects outright, making publication structurally
impossible. Note this applies only to the auto version — an explicit
`-f version=X.Y.ZrcN` is stamped **verbatim with no local segment**, precisely so
it stays promotable.

```bash
gh workflow run dev-build.yml            # auto dev version
RUN_ID="$(gh run list --workflow=dev-build.yml --limit 1 \
            --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID"
gh run download "$RUN_ID" -n remo-wheel -D ./dl   # needs gh auth on that machine
uv tool install --force ./dl/remo_cli-*.whl
```

### D — Publish the RC to PyPI (exceptional; explicit approval required)

Constitution IX permits this, but as **the exception**: "taken only when the
tester cannot install off-index." Outcome B needs no `gh` auth and no index, so
that condition is now rarely met — before proposing D, state why B will not work
for this tester. Never take D merely because the word "publish" was used.

Requires **Step V** on the bumped tree first (this is irreversible), then:

```bash
git commit -am "chore(release): X.Y.ZrcN"
git tag vX.Y.ZrcN          # a v* tag: this is what triggers release.yml
git push origin main vX.Y.ZrcN
```

`release.yml` marks it a prerelease off the tag suffix, publishes to PyPI + GHCR,
and never moves `latest`. Offer to watch CI. Warn the operator that this reserves
`X.Y.ZrcN` on PyPI permanently.

---

## Step V — Validation gate (test the exact wheel before PyPI)

**Required for anything that reaches PyPI:** the stable lane, and RC outcome
**D**. Also used by RC outcome **A**, whose whole purpose it is.

Outcomes **B** and **C** build in clean CI rather than locally, so Step V does
not apply as written — their equivalent is the post-publish check in **B**, which
validates the artifact testers will actually fetch. If a full local run was
already green on the same commit, say so rather than repeating it; do not claim
Step V ran when CI did.

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

- The requested lane completed through the point the user approved (RC outcome
  A/B/C/D, or stable PR merged).
- No tag was pushed, commit was pushed, or PR merged without explicit approval.
- The outcome was reported clearly, naming **which index was touched**: for
  outcomes A–C, that PyPI is unchanged; for D or a stable release, the version
  now permanently reserved there.
