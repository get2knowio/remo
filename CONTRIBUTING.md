# Contributing to Remo

## Development Setup

```bash
# Clone the repo
git clone https://github.com/get2knowio/remo.git
cd remo

# Install with all optional deps + dev tools
uv sync --all-extras
```

## Making Changes

1. Create a feature branch from `main`
2. Make your changes
3. Test locally
4. Submit a pull request

## Adding a Provider

Want to add a fifth cloud/host provider (DigitalOcean, Vultr, ...)? See
[`docs/providers.md`](docs/providers.md) — providers are a free-function
module + one descriptor registration, no existing CLI files to touch.

## Release Process

**remo is never uploaded to PyPI or TestPyPI** — not a stable release, not an
RC, not a plugin (Constitution IX). GitHub Releases is the only package channel:
every release is a GitHub release carrying the wheels, `manifest.json` and
build-provenance attestations, installed by `install.sh`. The GHCR `remo-web`
image is the only other published artifact.

Releases run through GitHub Actions in **two lanes**:

- **Stable releases** are automated by **release-please**. It watches
  Conventional Commits on `main` and maintains a "release PR" that bumps the
  version in `pyproject.toml` and updates `CHANGELOG.md`. **Merging that PR**
  creates the `vX.Y.Z` tag + GitHub release, which triggers `release.yml`. One
  build produces the wheels (remo-cli plus every plugin) and `manifest.json`;
  those are attached to the GitHub release and attested. **The GitHub release
  assets are the release.** The image goes to GHCR. You never bump the version
  or tag a stable release by hand.
- **Pre-releases (RC/beta)** are **manual** — release-please stays out of them.
  Dispatch `dev-build.yml` with an explicit version to build the wheel in clean
  CI:

  ```bash
  gh workflow run dev-build.yml -f version=X.Y.ZrcN -f prerelease=true
  ```

  That attaches the wheels and `manifest.json` (the same artifact set a stable
  release carries) to a GitHub pre-release tagged **`rc-X.Y.ZrcN`**, which
  installs anywhere with no `gh` auth and no index, plugins included via `--with`:

  ```bash
  curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
    | bash -s -- --prerelease X.Y.ZrcN --with ec2,web
  ```

  Nothing is committed and nothing is uploaded anywhere but that pre-release.

> **⚠️ The two tag namespaces are not interchangeable.** `release.yml` triggers on
> `tags: v*` and cuts a public, stable GitHub release + GHCR image (moving
> `latest`); `dev-build.yml` tags `rc-<version>` and publishes only a GitHub
> pre-release — it refuses to create a `v*` tag for exactly this reason. `v*`
> tags come only from release-please; never push one by hand.

> **⚠️ A stable release is public the moment its tag lands** — users and
> `remo self-update` pick it up straight away. Before merging a release PR,
> **validate the build locally first** (next section), and merge only with
> explicit approval.

### Test a release build locally (before releasing)

The released package version comes from **`pyproject.toml`** (via `uv build`) —
**not** from the git tag. So a local build produces the exact wheels that would
be released, and you can install and smoke-test them without publishing anything.

```bash
# 1. Set the version you intend to release in pyproject.toml, using the PEP 440
#    pre-release form (no separator before the suffix) when testing an RC, e.g.:
#        version = "2.3.0rc1"
#    This is what determines the wheel version.

# 2. Build every wheel (remo-cli + each plugins/* distribution) into dist/
uv build --wheel --all-packages -o dist

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
# Install the tip of a branch (or main):
uv tool install --force "git+https://github.com/get2knowio/remo.git@<branch>"

# Or run it once, ephemerally, without installing anything:
uvx --from "git+https://github.com/get2knowio/remo.git@<branch>" remo --help
```

### Pre-release (RC/Beta) — manual

release-please does **not** cut RCs. An RC is always a GitHub pre-release made
by `dev-build.yml`; there is no other RC channel.

`dev-build.yml` builds the wheels in clean CI and attaches them to a GitHub
pre-release tagged `rc-<version>`. No local bump, nothing committed:

```bash
gh workflow run dev-build.yml -f version=2.3.0rc1 -f prerelease=true
# also publishes ghcr.io/get2knowio/remo-web:2.3.0rc1 (never `latest`);
# add -f image=false for a CLI-only RC
```

Testers install it by version, with no `gh` auth (add `--with ec2,hetzner,web` to
exercise the plugins and the console):

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --prerelease 2.3.0rc1

# or the newest pre-release, whatever it is
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh \
  | bash -s -- --prerelease
```

This is reversible — delete the pre-release, re-cut the version. Verify the
published wheel afterwards:

```bash
URL=https://github.com/get2knowio/remo/releases/download/rc-2.3.0rc1/remo_cli-2.3.0rc1-py3-none-any.whl
uv venv /tmp/remo-rc && uv pip install --python /tmp/remo-rc/bin/python "remo-cli @ $URL"
/tmp/remo-rc/bin/remo --version    # should print 2.3.0rc1
```

> Prefer testing **without** publishing when you can — a local wheel or a
> `git+https://…@<branch>` install (see the local-test section) needs no tag at
> all.

### Stable Release — via release-please

You do **not** bump the version or push a tag by hand for stable releases:

1. Merge feature/fix work to `main` using Conventional Commits (`feat:`, `fix:`,
   `feat!:`/`BREAKING CHANGE:` for a major).
2. **release-please** opens (and keeps updating) a `chore(main): release X.Y.Z`
   PR that bumps `pyproject.toml` and `CHANGELOG.md`. Review it.
3. Before merging, validate the build locally at the PR's version (check out the
   release-please branch and run the local-test steps above).
4. **Merge the release PR** (with explicit approval). release-please tags
   `vX.Y.Z` + creates the GitHub release, which triggers `release.yml` to attach
   the wheels, `manifest.json` and attestations, and to publish the GHCR image
   and move `latest`.

Users install the stable release with:
```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash
```

See [docs/install.md](docs/install.md) for `--with`, pre-releases and `remo self-update`.

> **One-time setup — publish trigger:** the tag release-please creates must be
> authored by a token that can trigger workflows, or `release.yml` (wheels +
> Docker) won't fire (a tag made with the default `GITHUB_TOKEN` does **not**
> trigger `on: push: tags`). `release-please.yml` resolves the token in this
> order:
>
> 1. **GitHub App (recommended)** — set the org (or repo) Actions **variable**
>    `RELEASE_PLEASE_APP_ID` and the **secret** `RELEASE_PLEASE_APP_KEY` (the
>    App's private key). Install a GitHub App with **Contents: write** +
>    **Pull requests: write** on this repo. Short-lived, auto-minted, nothing to
>    rotate, tied to no personal account — and reusable across org repos.
> 2. **`RELEASE_PLEASE_TOKEN` PAT** — a personal access token (`contents` +
>    `pull-requests` write) as a fallback if you don't use an App.
> 3. **`GITHUB_TOKEN`** — automatic last resort: release PRs still open, but the
>    tag won't auto-publish until a maintainer re-pushes it.
>
> See `.github/workflows/release-please.yml`.
>
> **`uv.lock` is synced for you.** release-please bumps `pyproject.toml` but
> knows nothing about `uv.lock`, which records the project's own version too.
> The workflow now runs `uv lock` against the release branch and commits the
> result onto the release PR, and `tests/unit/test_lockfile_version.py` fails
> the build if the two ever disagree — so a drifted lockfile can't merge. If
> that test does fire, the fix is `uv lock` plus a commit.

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
| `release-please.yml` | Push to main | Maintain the stable release PR (version bump + CHANGELOG); tag on merge |
| `release.yml` | Tag push (`v*`) | Build once; GitHub release (wheels + `manifest.json`, attested); image to GHCR. Never uploads to PyPI |

## Updating the Installer

`install.sh` at the repo root is the one copy, served straight off `main`:

```
https://raw.githubusercontent.com/get2knowio/remo/main/install.sh
```

There is no publish step and no second copy to keep in sync — a merge to
`main` *is* the deploy. The installer is also served from each release tag
(`https://raw.githubusercontent.com/get2knowio/remo/<tag>/install.sh`):
`remo self-update` runs the installer of the release it is moving to, so the
installer always matches that release's manifest format. Note the URL is pinned to `main`, not to a tag: users
should always get the current installer, and pinning a *release* is what the
script's own `--version` flag is for.

Because it ships the instant it merges, treat a change to `install.sh` as a
change to production: run `bash -n install.sh` at minimum, and prefer testing
the raw URL from your own branch first —

```bash
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/<your-branch>/install.sh | bash
```

## Testing Changes

### Test Zellij Config Changes

```bash
remo incus upgrade <container> --only zellij
```

### Test Full Container Setup

```bash
remo incus create test-container --host <incus-host>
# ... test ...
remo incus destroy test-container --yes --host <incus-host>
```

### Test Installer

```bash
# Test the latest pre-release
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash -s -- --prerelease --with ec2,web
```

`uv run pytest tests/unit/test_install_script.py` drives the real script against a
fake GitHub (fixtures built by `tests/unit/install_fixtures.py`).
