---
name: release
description: Cut a remo release — drive the release-please stable release PR, or build/validate/publish a pre-release (RC) as a GitHub pre-release — with a mandatory local "test before a stable release" gate and explicit approval before anything is tagged, merged, or published. remo is never uploaded to PyPI.
argument-hint: "[stable | rc [X.Y.ZrcN]] — omit to be asked"
metadata:
  author: remo
---

# Release

Operationalizes remo's two-lane release process (see
[CONTRIBUTING.md](../../../CONTRIBUTING.md) → Release Process). Pairs with the
release-please integration.

**Non-negotiable safety rules — apply in every mode:**

- **remo is never uploaded to PyPI or TestPyPI** (constitution IX, v3.0.0) —
  not a stable release, not an RC, not a plugin. GitHub Releases (wheels +
  `manifest.json` + attestations, installed by `install.sh`) is the only package
  channel, beside the GHCR `remo-web` image. Never propose, run or script a PyPI
  upload; if asked for one, point at this rule.
- **A `v*` tag is a public, stable release.** `release.yml` triggers on
  `tags: v*`: it creates the public GitHub release with its wheels and manifest
  and pushes the GHCR image (moving `latest` for a stable version). Users and
  `remo self-update` pick it up at once, so it is effectively irreversible. Run
  the local build + smoke gate (Step V below) before the stable release PR is
  merged. `v*` tags come **only** from release-please; never hand-push one. The
  GitHub-pre-release path (RC outcome **B**) tags `rc-<version>`, which
  `release.yml` never sees. Check which namespace you are in before pushing
  anything.
- **Never** `git push` a tag, `git push` a version-bump commit, or `gh pr merge`
  a release PR **without explicit user approval in the current turn.** Approval
  from an earlier task does not carry over. Dispatching `dev-build.yml` is not in
  this class — it neither commits nor uploads — but say what it will tag.
- Work only from a **clean working tree** on an **up-to-date `main`**. If the
  tree is dirty or `main` is behind `origin/main`, stop and surface it. A dirty
  file the operator owns and you did not touch is theirs: report it, never
  revert it.
- Prefer testing **without** publishing. "Publish a pre-release" in this repo
  means outcome **B** (an `rc-` GitHub pre-release). There is no other
  pre-release publish path.

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
   which triggers `release.yml`: one build, the GitHub release assets —
   wheels, then `manifest.json`, then attestations — and the GHCR image):
   ```bash
   gh pr merge <number> --squash
   ```
   Before merging, confirm the `RELEASE_PLEASE_TOKEN` secret is set — without it
   the tag will not trigger the publish. If it's missing, warn the user and let
   them decide.
6. After the release CI finishes, verify the release carries **3 wheels
   (remo-cli, ec2, hetzner), `manifest.json` and attestations**
   (`gh release view vX.Y.Z --json assets --jq '.assets[].name'`;
   `gh attestation verify <wheel> -R get2knowio/remo`), and that
   `ghcr.io/get2knowio/remo-web:X.Y.Z` exists. The GitHub release is the whole
   release; nothing goes to PyPI.
7. Return to `main`, `git pull --ff-only`, report the new tag, and offer to watch
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
| `rc-X.Y.ZrcN` | `dev-build.yml` (`prerelease=true`) | **GitHub pre-release** (+ `remo-web:X.Y.ZrcN`) |
| `vX.Y.Z` | release-please only | **Public GitHub release + GHCR image** (`release.yml`) |

`release.yml` triggers on `tags: v*`. A `v`-prefixed RC tag would therefore cut
a public release through the stable pipeline; `dev-build.yml` refuses to create
one for exactly that reason, and this skill never hand-pushes a `v*` tag.
Outcome B below is what "publish a pre-release so we can install it by version"
means in this repo — **not** a `v` tag.

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

### B — GitHub pre-release, installable by version

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

Then **validate the published artifact** through the real installer, which is
stronger than a pre-build local check because it is exactly what testers run
(checksums verified, plugins and web included). It replaces the operator's own
install, so say so first; or set `REMO_HOME` and `HOME` to a scratch directory:

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --prerelease X.Y.ZrcN --with ec2,web
remo --version     # MUST print X.Y.ZrcN
remo providers     # MUST list ec2 (and hetzner if installed) as loaded
remo --help
gh release view rc-X.Y.ZrcN --json assets --jq '.assets[].name'   # 3 wheels + manifest.json
```

Confirm with `gh release view rc-X.Y.ZrcN` that it is marked pre-release.
Testers install it with no `gh` auth:

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --prerelease X.Y.ZrcN --with ec2,web
```

A GitHub pre-release is deletable and the version re-cuttable, so this outcome is
recoverable — unlike a stable `v*` release.

### C — Dev build artifact (no version reserved, no tag)

For cross-machine testing when no version should be claimed at all. Omit
`version` and the workflow stamps `X.Y.Z.devN+g<sha>`; that PEP 440 **local
segment** marks it as a throwaway that can never be promoted to a release
(`prerelease=true` refuses it). Note this applies only to the auto version — an explicit
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

---

## Step V — Validation gate (test the exact wheels before a stable release)

**Required before every stable release** (merging the release-please PR, which
creates the `v*` tag, the public GitHub release and the GHCR `latest` image).
Also used by RC outcome **A**, whose whole purpose it is.

Outcomes **B** and **C** build in clean CI rather than locally, so Step V does
not apply as written — their equivalent is the post-publish check in **B**, which
validates the artifact testers will actually fetch. If a full local run was
already green on the same commit, say so rather than repeating it; do not claim
Step V ran when CI did.

Run this on whatever ref will be released (the release-please PR head for stable,
or the bumped working tree for an RC). The released version comes from
`pyproject.toml` via `uv build`, and `release.yml` builds every workspace package
the same way, so this builds the identical set of artifacts:

```bash
uv run pytest -q                    # full suite must pass
uv build --wheel --all-packages -o dist   # remo-cli + every plugins/* wheel
scripts/check_plugin_wheels.sh dist # plugin discovery on the built wheels
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
  A/B/C, or stable PR merged).
- No tag was pushed, commit was pushed, or PR merged without explicit approval.
- Nothing was uploaded to PyPI or TestPyPI.
- The outcome was reported clearly, naming **what became public**: for A and C,
  nothing; for B, the `rc-` GitHub pre-release (and its `remo-web:X.Y.ZrcN`
  image); for a stable release, the `vX.Y.Z` GitHub release and GHCR image.
