# Quickstart: validating one install path

## 1. Local gates (no network)

```bash
cd /workspaces/remote-coding
uv sync --all-extras --no-extra hetzner --no-extra ec2
uv run --no-sync pytest tests/unit/test_install_script.py tests/unit/test_release_manifest.py \
  tests/unit/core/test_install_record.py tests/unit/core/test_version.py \
  tests/unit/providers/test_self_update.py tests/unit/cli/test_self_update.py
uv run ruff check src/remo_cli scripts && uv run mypy src/remo_cli
```

Expected: all green; the installer suite exercises dry-run and full-run paths through shims (see [contracts/installer-cli.md](contracts/installer-cli.md)).

## 2. Manifest on real wheels (Tier 2, local)

```bash
rm -rf dist && uv build --wheel --all-packages -o dist
uv run python scripts/release_manifest.py generate --dist dist --tag rc-9.9.9rc1 \
  --channel prerelease --repo get2knowio/remo --out dist/manifest.json
uv run python scripts/release_manifest.py verify --dist dist --manifest dist/manifest.json
scripts/check_plugin_wheels.sh dist
```

Expected: manifest lists remo-cli + ec2 + hetzner with matching sha256; verify exits 0. Tamper one byte of a wheel → verify exits non-zero naming it.

## 3. Installer end to end against a local fixture release

The installer suite's full-run test does this with shims; to see it by hand, serve `dist/` and point the curl shim at it as in `tests/unit/test_install_script.py`. Expected: one `uv tool install --force "remo-cli[web] @ file://…" --with "remo-provider-ec2 @ file://…"` and an `install.json` per [contracts/install-record.md](contracts/install-record.md).

## 4. Tier 2 — a real RC before merge (Constitution IX)

```bash
gh workflow run dev-build.yml --ref 031-install-path -f version=5.0.0rc1 -f prerelease=true -f image=false
# after it completes:
gh release view rc-5.0.0rc1 --json assets --jq '.assets[].name'   # 3 wheels + manifest.json
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/031-install-path/install.sh \
  | bash -s -- --prerelease 5.0.0rc1 --with ec2,web
remo providers        # ec2 loaded from remo-provider-ec2
remo web check --help # web extra present
cat ~/.config/remo/install.json
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/031-install-path/install.sh \
  | bash -s -- --prerelease 5.0.0rc1 --with hetzner
remo providers        # ec2 + hetzner; web still present
remo self-update --check
```

PyPI is untouched by every step above. The stable-lane check (identical wheel on the GitHub release and PyPI) is verified at the next stable release by comparing `sha256` in `manifest.json` with the PyPI file's digest.
