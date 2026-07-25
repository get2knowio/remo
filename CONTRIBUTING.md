# Contributing to Remo

## Development Setup

```bash
# Clone the repo
git clone https://github.com/get2knowio/remo.git
cd remo

# Initialize
./remo init
```

## Making Changes

1. Create a feature branch from `main`
2. Make your changes
3. Test locally
4. Submit a pull request

## Release Process

Releases run through GitHub Actions in **two lanes**:

- **Stable releases** are automated by **release-please**. It watches
  Conventional Commits on `main` and maintains a "release PR" that bumps the
  version in `pyproject.toml` and updates `CHANGELOG.md`. **Merging that PR**
  creates the `vX.Y.Z` tag + GitHub release, which triggers `release.yml` to
  publish to PyPI and GHCR. You never bump the version or tag a stable release by
  hand.
- **Pre-releases (RC/beta)** are **manual** — release-please stays out of them.
  You bump `pyproject.toml` to the RC version yourself and push a `vX.Y.Z-rcN`
  tag, which `release.yml` publishes as a prerelease.

> **⚠️ Publishing to PyPI is irreversible** — a version, once uploaded, can never
> be replaced or re-uploaded. Whether you're about to merge a release PR or push
> an RC tag, **validate the build locally first** (next section).

### Test a release build locally (before tagging)

The published package version comes from **`pyproject.toml`** (via `uv build`) —
**not** from the git tag. So a local build produces the exact wheel that would be
published, and you can install and smoke-test it without touching PyPI.

```bash
# 1. Set the version you intend to release in pyproject.toml, using the PEP 440
#    pre-release form (no separator before the suffix), e.g.:
#        version = "2.3.0rc1"
#    This is what determines the wheel / PyPI version; it must correspond to the
#    tag you push later (v2.3.0-rc1).

# 2. Build the wheel + sdist into dist/
uv build

# 3. Install the built wheel into a throwaway environment (NOT your dev checkout)
#    and smoke-test it end to end:
uv venv /tmp/remo-rc
uv pip install --python /tmp/remo-rc/bin/python ./dist/remo_cli-2.3.0rc1-py3-none-any.whl
/tmp/remo-rc/bin/remo --version        # should print 2.3.0rc1
/tmp/remo-rc/bin/remo --help

# 4. If you were ONLY testing (not releasing yet), revert the version bump so it
#    doesn't accidentally land on main:
git checkout pyproject.toml
```

You can also hand a reviewer the built wheel directly (`dist/*.whl`), or let them
install a branch straight from git with **no build and no tag** at all:

```bash
# Install the tip of a branch (or main) — never touches PyPI:
uv tool install --force "git+https://github.com/get2knowio/remo.git@<branch>"

# Or run it once, ephemerally, without installing anything:
uvx --from "git+https://github.com/get2knowio/remo.git@<branch>" remo --help
```

Only once the local wheel checks out should you commit the version bump and push
the tag.

### Pre-release (RC/Beta) — manual

release-please does **not** cut RCs. To publish a pre-release for wider testing:

```bash
# 1. Bump pyproject.toml to the RC version (PEP 440 form, no separator):
#        version = "2.3.0rc1"
#    ...and validate the build locally (see the section above).
# 2. Commit the bump, then tag and push. The tag suffix must contain `rc`
#    (or `beta`/`alpha`) so release.yml marks it a pre-release:
git commit -am "chore(release): 2.3.0rc1"
git tag v2.3.0-rc1
git push origin main v2.3.0-rc1
```

`release.yml` detects the pre-release (from the `rc` suffix), creates a GitHub
pre-release, and publishes the prerelease to PyPI + GHCR (`latest` is never
moved). Users can install with:

```bash
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash -s -- --pre-release
```

> Prefer testing **without** publishing when you can — a local wheel or a
> `git+https://…@<branch>` install (see the local-test section) needs no tag and
> never touches PyPI.

### Stable Release — via release-please

You do **not** bump the version or push a tag by hand for stable releases:

1. Merge feature/fix work to `main` using Conventional Commits (`feat:`, `fix:`,
   `feat!:`/`BREAKING CHANGE:` for a major).
2. **release-please** opens (and keeps updating) a `chore(main): release X.Y.Z`
   PR that bumps `pyproject.toml` and `CHANGELOG.md`. Review it.
3. Before merging, validate the build locally at the PR's version (check out the
   release-please branch and run the local-test steps above).
4. **Merge the release PR.** release-please tags `vX.Y.Z` + creates the GitHub
   release, which triggers `release.yml` to publish to PyPI + GHCR and move
   `latest`.

Users install the stable release with:
```bash
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash
```

> **One-time setup:** the tag release-please creates must be authored by a PAT
> (repo secret `RELEASE_PLEASE_TOKEN`, `contents` + `pull-requests` write) for
> the publish to fire — a tag made with the default `GITHUB_TOKEN` does not
> trigger `release.yml`. See `.github/workflows/release-please.yml`.
>
> **Note:** release-please does not run `uv lock`; if `uv.lock`'s recorded
> project version matters to you, run `uv lock` and amend it onto the release PR
> before merging.

### Version Numbering

We use [Semantic Versioning](https://semver.org/):

- **MAJOR.MINOR.PATCH** (e.g., `1.2.3`)
- **Pre-release**: append `-rc.N`, `-beta.N`, or `-alpha.N` (e.g., `1.2.3-rc.1`)

| Change Type | Version Bump |
|-------------|--------------|
| Breaking changes | MAJOR |
| New features (backward compatible) | MINOR |
| Bug fixes | PATCH |

## GitHub Actions Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `provision.yml` | Manual | Provision Hetzner server |
| `teardown.yml` | Manual | Teardown Hetzner server |
| `sync-install.yml` | Push to main | Sync install.sh to docs/ for GitHub Pages |
| `release-please.yml` | Push to main | Maintain the stable release PR (version bump + CHANGELOG); tag on merge |
| `release.yml` | Tag push (`v*`) | GitHub release + publish to PyPI and GHCR (stable or prerelease) |

## Updating the Installer

When you modify `install.sh`:

1. The `sync-install.yml` workflow automatically copies it to `docs/install.sh`
2. GitHub Pages serves the updated version at https://get2knowio.github.io/remo/install.sh

No manual sync needed.

## Testing Changes

### Test Zellij Config Changes

```bash
remo incus update <container> --only zellij
```

### Test Full Container Setup

```bash
remo incus create test-container --host <incus-host>
# ... test ...
remo incus destroy test-container --yes --host <incus-host>
```

### Test Installer

```bash
# Test from branch
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash -s -- --branch my-feature
```
