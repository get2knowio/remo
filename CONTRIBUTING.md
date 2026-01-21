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

Releases are automated via GitHub Actions. When you push a tag, a release is created automatically.

### Pre-release (RC/Beta)

For testing new features before stable release:

```bash
# 1. Update VERSION file
echo "0.3.0-rc.1" > VERSION

# 2. Commit the version bump
git add VERSION
git commit -m "chore: bump version to 0.3.0-rc.1"

# 3. Create and push tag
git tag v0.3.0-rc.1
git push origin main v0.3.0-rc.1
```

The release workflow will:
- Validate VERSION matches the tag
- Detect it's a pre-release (from `-rc` suffix)
- Create a GitHub pre-release with auto-generated notes

Users can install with:
```bash
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash -s -- --pre-release
```

### Stable Release

When ready to promote to stable:

```bash
# 1. Update VERSION file (remove -rc suffix)
echo "0.3.0" > VERSION

# 2. Commit
git add VERSION
git commit -m "chore: release v0.3.0"

# 3. Create and push tag
git tag v0.3.0
git push origin main v0.3.0
```

The release workflow will create a stable GitHub release.

Users can install with:
```bash
curl -fsSL https://get2knowio.github.io/remo/install.sh | bash
```

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
| `release.yml` | Tag push (`v*`) | Create GitHub release automatically |

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
