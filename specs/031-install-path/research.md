# Research: One Install Path for Every Release

Grounded in an audit of `main` at 62b16f0 (2026-11-27). Anchors are file:line at that commit.

## R1 — One build feeds the GitHub release and PyPI

- **Decision**: Restructure `release.yml` into four jobs: `build` → `release` (attach + attest) and `publish` (PyPI) → `publish-image`.
  - `build` checks out the tag and runs `uv build --all-packages -o dist`, then `scripts/check_plugin_wheels.sh dist`, then `scripts/release_manifest.py generate --dist dist --tag v<ver> --channel stable|prerelease --repo $GITHUB_REPOSITORY --out dist/manifest.json` and `… verify`. It uploads `dist/` as artifact `release-dist`.
  - `release` downloads `release-dist` and creates the release with `softprops/action-gh-release` `files:` set to `dist/*.whl` plus `dist/manifest.json`. It runs `actions/attest-build-provenance` over `dist/*.whl` and needs `id-token: write` and `attestations: write`.
  - `publish` downloads the same artifact, copies only `remo_cli-*` (wheel and sdist) into `pypi-dist/`, and publishes with `packages-dir: pypi-dist/`.
- **Rationale**:
  - Today `publish` rebuilds with `uv build` (release.yml:191-192) and never builds plugins, so neither "identical wheel" (FR-003) nor plugin assets (FR-001) are possible without a single build.
  - Filtering keeps plugins off PyPI (FR-005a).
- **Alternatives**:
  - Build twice and compare hashes. Rejected: hatchling wheels are not guaranteed reproducible across runs.
  - Attach from the publish job. Rejected: it couples asset upload to PyPI success.

## R2 — dev-build attaches the same set

- **Decision**: Changes to the `prerelease` job (dev-build.yml:153-237):
  - Generate and verify `manifest.json` with `--tag rc-<ver> --channel prerelease`.
  - Attach the wheels and the manifest with `gh release create "$TAG" dist/*.whl dist/manifest.json --target "$GITHUB_SHA" --prerelease …`. Sdists are dropped from RC assets.
  - Run `attest-build-provenance` over the wheels. Add the `id-token: write` and `attestations: write` permissions to that job only.
  - Rewrite the release-notes heredoc to show the `install.sh --prerelease <ver> --with …` one-liner instead of the hand-written `uv tool install … --with` form, and do the same for the header comments at :37-50.
  - The `v*` refusal (:181-183) and `TAG="rc-${VERSION}"` stay; tests pin them.
- **Rationale**:
  - Same artifact set (FR-001).
  - `--target` makes the tag point at the built commit, which matters because self-update fetches `install.sh` from the tag (R7).
- **Alternatives**: Keep sdists on RCs. Rejected: they add noise, and the manifest lists wheels only.

## R3 — Manifest format: line-oriented JSON

- **Decision**: Valid JSON, written by `release_manifest.py` with a fixed layout:
  - A header object line.
  - One `components` array element per line, with fixed key order: `name`, `dist`, `version`, `kind` (`cli` or `plugin`), `file`, `url`, `sha256`.
  - A closing line.

  install.sh extracts fields per line with `grep`/`sed`, keyed on `"name":"<n>"`. Full format: [contracts/release-manifest.md](contracts/release-manifest.md).
- **Rationale**:
  - `test_resolver_needs_no_json_parser` (test_install_script.py:365) forbids `jq`/`python3`, and the existing release resolution already uses grep/sed.
  - Python consumers still `json.load` it.
- **Alternatives**:
  - A TSV sidecar. Rejected: two formats can drift.
  - Allow `python3` in install.sh. Rejected: macOS without the Command Line Tools has no python3, and uv may not be installed yet.

## R4 — Accepted `--with` names come from the manifest

- **Decision**: Valid names are:
  - `web`, which is an extra of remo-cli and fixed in install.sh;
  - every manifest component with `kind: plugin`, keyed by its `name` (`ec2`, `hetzner`).

  An unknown name fails after the manifest is read but **before any wheel download or install**. FR-011's "before anything changes" holds, because reading the manifest changes nothing.
- **Rationale**: A new first-party plugin needs no installer change (Principle II spirit).
- **Alternative**: A hard-coded list in install.sh. Rejected: it drifts from the plugins actually released.

## R5 — Verify, then install from the verified files

- **Decision**:
  - Download each needed wheel to `mktemp -d` under the original file name, then compare its SHA-256 with `sha256sum`, falling back to `shasum -a 256`; if neither exists, fail.
  - Install with `uv tool install --force "remo-cli[web] @ file://$tmp/remo_cli-…whl" --with "remo-provider-ec2 @ file://$tmp/…whl"`.
  - With pipx: `pipx install --force "remo-cli[web] @ file://…"`, then one `pipx inject remo-cli "<dist> @ file://…"` per plugin.
  - Third-party additions pass through: `--with "<pkg>"` (uv) or `pipx inject remo-cli <pkg>`.
  - The temp dir is removed on exit (`trap`).
- **Rationale**: What is checked is what is installed (clarification Q2).
- **Alternative**: URL `#sha256=` fragments. Rejected: tool-manager support varies, and pipx inject does not honour them uniformly.

## R6 — Foreign-URL refusal and release membership

- **Decision**:
  - The installer refuses a manifest unless every component `url` begins with `https://github.com/<REPO>/releases/download/<tag>/` and the manifest's `tag` is the tag it asked for.
  - `release_manifest.py verify` checks three things against the dist dir:
    - every wheel present is in the manifest;
    - each entry's sha256 matches its file;
    - every URL is under the tag.
- **Rationale**: This is the reconciliation ruling that membership is by build and location (FR-014).

## R7 — Self-update runs the release's own installer

- **Decision**: `providers/self_update.py`:
  - Load the record and resolve the newest version on its channel (R8). If that version is not newer, report "already up to date".
  - Otherwise download `https://raw.githubusercontent.com/<repo>/<tag>/install.sh` to a temp file and run `bash <tmp> --version <ver>` (stable) or `--prerelease <ver>` (RC), plus `--with <extras+plugins>`, `--with-pkg …` and `--with-url …`.
  - `REMO_REPO_SLUG` is taken from the record's `repo`. The subprocess inherits the TTY.
  - Before running, it asks for confirmation unless `--yes`. The prompt shows the exact command.
- **Rationale**:
  - One install implementation.
  - The installer always matches the manifest format of the release it installs.
- **Alternative**: Reimplement the install in Python. Rejected: two implementations would drift.

## R8 — Release lookup for self-update and the passive notice

- **Decision**: `core/version.get_latest_github_release(repo, channel)`:
  - stable reads `GET https://api.github.com/repos/<repo>/releases/latest` → `tag_name`, with `v` stripped;
  - prerelease reads `GET …/releases?per_page=20`, takes the first `rc-` tag with `prerelease: true`, and strips `rc-`.
  - It uses `urlopen` with a 10 s timeout and raises a typed `ReleaseLookupError` on failure, which self-update maps to `OperationFailedError`. A 403 with `X-RateLimit-Remaining: 0` names the rate limit.
  - The passive check keeps its cache and thread design but consults GitHub when a record exists, using the record's channel; it keeps PyPI otherwise.
  - `_SEMVER_RE` and the markers gain the PEP 440 `a`/`b` forms.
- **Rationale**: Install-record installs track GitHub releases, not PyPI.

## R9 — Install origin detection

- **Decision**: `core/install_record.detect_origin()` returns one of three origins:
  - `recorded`: `install.json` is valid.
  - `source`: no record, and `importlib.metadata.distribution("remo-cli").read_text("direct_url.json")` has `dir_info` (an editable or local directory install).
  - `pypi`: no record and no `direct_url.json`, or a `direct_url.json` with neither `dir_info` nor `url` pointing to a file.

  Anything else is `unknown`, and guidance then prints both commands.
- **Rationale**: PEP 610 is the standard signal.

## R10 — Cross-manager cleanup and the record writer

- **Decision**:
  - When the chosen installer is uv and `pipx list --short` lists `remo-cli` (or the reverse), the installer uninstalls the other copy after a successful install, so the new one is in place before the old one goes.
  - It writes the record with `printf` to `install.json.tmp.$$` and then `mv`. Values are validated against `^[A-Za-z0-9._:/@+=,~%-]+$` before writing, which rules out quotes and backslashes, so no JSON escaping is needed.
  - `--dry-run` writes nothing.
  - The record is read with grep/sed in the same line-oriented shape (contract: [install-record.md](contracts/install-record.md)). An unreadable record, or one with another `format`, produces a warning and is ignored.
- **Rationale**:
  - One remo on PATH (edge case).
  - JSON with no parser.
