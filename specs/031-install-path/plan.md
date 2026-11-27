# Implementation Plan: One Install Path for Every Release

**Branch**: `031-install-path` | **Date**: 2026-11-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/031-install-path/spec.md`

## Summary

> **Amended 2026-11-27 (owner decision, spec Clarifications):** remo is no
> longer uploaded to PyPI at all — stable, RCs and plugins. The `publish` (PyPI)
> job is removed from `release.yml`, Constitution IX is redefined (v3.0.0), and
> the passive notice of an install without a record looks up the newest GitHub
> stable release instead of PyPI. Superseded text below is struck through, not
> deleted.

Every release, stable and RC, attaches the same artifact set to its GitHub
release: the remo-cli wheel, every first-party plugin wheel, a line-oriented
JSON **release manifest** (components, URLs, SHA-256) and build-provenance
attestations. One build job produces those files~~, and PyPI (remo-cli stable
only) gets the identical wheel from the same artifact~~ (no PyPI upload since
the 2026-11-27 amendment). `install.sh` learns
`--with web,ec2,hetzner`, `--without`, `--with-pkg` and `--with-url`. It reads
the manifest for the chosen channel, downloads and checksum-verifies every
wheel, and installs the set in one uv operation (pipx: install plus inject)
from the verified local files. Afterwards it writes `install.json`, the
**install record**, so later runs are additive. A new `remo self-update`
re-runs the installer, fetched from the target release's own tag, with the
recorded set. The passive update notice names it when a record exists. PyPI
stops being ~~the default for anything~~ a channel at all (amended 2026-11-27), and #257 is superseded. Decisions R1–R10
are in [research.md](research.md).

## Technical Context

**Language/Version**: Bash 3.2+ (install.sh must still run on stock macOS bash, as today); Python 3.11+ (CLI, release scripts)

**Primary Dependencies**: Click (existing), stdlib `urllib`/`json`/`hashlib`/`importlib.metadata`. GitHub Actions: `actions/attest-build-provenance` (new, SHA-pinned), `softprops/action-gh-release` (existing), ~~`pypa/gh-action-pypi-publish` (existing)~~ (removed 2026-11-27; no workflow may use it). No new runtime Python dependency.

**Storage**: `<REMO_HOME>/install.json` (install record, format 1, line-oriented JSON, written by install.sh, read by Python and bash); `manifest.json` per GitHub release.

**Testing**: pytest (serial). Installer tests run the real `install.sh` with PATH shims for `curl`/`uv`/`pipx`/`sha256sum`/`shasum`/`uname`, a sandbox `HOME`/`REMO_HOME` and a fixture release directory served by the curl shim. The manifest script gets unit tests plus a CI run against built wheels.

**Target Platform**: Linux and macOS workstations (no Windows, gate unchanged); GitHub Actions ubuntu-latest for release.

**Project Type**: CLI and release engineering.

**Performance Goals**: Stable manifest resolution needs no GitHub API call (`releases/latest/download/manifest.json`); only bare `--prerelease`, self-update, and the stable fallback when the newest release has no manifest yet (#290) use the API (one call).

**Constraints**:
- No `v*` tag ~~or PyPI upload~~ from any RC path; no PyPI upload from any path at all (amended 2026-11-27).
- No `jq`/`python3` in install.sh; `test_resolver_needs_no_json_parser` stays.
- The Docker image, `uv sync` and the platform gate are unchanged.
- Every URL in docs is written out in full.

**Scale/Scope**: 3 components today (remo-cli, ec2, hetzner); the manifest format is open to more plugins without installer changes.

## Constitution Check

| Principle | Status | How |
|---|---|---|
| I Layering | ✅ | `cli/self_update.py` is Click wiring only. `providers/self_update.py` holds the business logic. `core/install_record.py` and `core/version.py` are shared and name no providers. |
| II Providers declared | ✅ | The installer and manifest carry no provider logic in Python; plugin names (`ec2`, `hetzner`) live only in the manifest and in install.sh's accepted-names list, which is derived from the manifest at runtime (R4). |
| III Typed errors | ✅ | Self-update raises `PreconditionError` (no record or unknown origin), `OperationFailedError` (lookup, download or installer failure) and `UserAbortedError`. The `self-update` command maps them through the same pattern as the other non-factory commands (the `local`/`projects` precedent). |
| IV Generated contracts | ✅ N/A | No FastAPI model or frame change. |
| V Ansible defaults | ✅ N/A | No Ansible change. |
| VI Test every skip/fail path | ✅ | The installer refusal matrix (unknown name, checksum mismatch, foreign URL, missing manifest, missing checksum tool, rate limit) and each self-update origin and failure are tested. |
| VII Idempotent | ✅ | Re-running the same set at the same version leaves the record unchanged apart from `updated_at`. The record is written atomically and only after success. |
| VIII Docs reflect reality | ✅ | README, the new `docs/install.md`, the release skill, CONTRIBUTING, CLAUDE.md/AGENTS.md (structure tree plus Recent Changes) and the plugin docs ship in the same change. |
| IX ~~Pre-release never PyPI~~ GitHub Releases only, never PyPI (v3.0.0) | ✅ | RC tags stay `rc-<ver>` and the `v*` refusal in dev-build is kept. ~~Only release.yml's publish job uploads, and only `remo_cli-*`.~~ Nothing uploads to PyPI: the `publish` job is removed and `tests/unit/test_release_workflows.py` forbids any PyPI publish action or `pypi` environment (amended 2026-11-27). This change is a packaging surface, so Tier 2 is mandatory: an RC is cut and installed with `--with` before merge (quickstart §4). |

No violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/031-install-path/
├── plan.md, research.md, data-model.md, quickstart.md
├── contracts/
│   ├── release-manifest.md     # manifest.json format + publish-time checks
│   ├── installer-cli.md        # install.sh flags/env/exit codes/outputs
│   ├── install-record.md       # install.json format + read/write rules
│   └── self-update-cli.md      # remo self-update surface + notice wording
└── tasks.md
```

### Source Code (repository root)

```text
install.sh                                 # --with/--without/--with-pkg/--with-url, manifest, sha256, record
scripts/release_manifest.py                # NEW: generate + verify manifest.json from a dist dir (stdlib)
scripts/check_plugin_wheels.sh             # unchanged assertions; CI also runs release_manifest.py verify
.github/workflows/release.yml              # build job → release (assets+attest) → publish-image; ~~+ publish (remo_cli only)~~ PyPI job removed 2026-11-27
.github/workflows/dev-build.yml            # prerelease job: manifest + attest + --target sha; header text
.github/workflows/ci.yml                   # wheel-install job: generate+verify manifest for stable & rc shapes
src/remo_cli/core/install_record.py        # NEW: read/validate install.json, detect origin (direct_url.json)
src/remo_cli/core/version.py               # GitHub release lookup by channel, a/b parsing, notice wording
src/remo_cli/providers/self_update.py      # NEW: plan + run self-update (fetch installer at tag, run it)
src/remo_cli/cli/self_update.py            # NEW: `remo self-update [--check] [--yes]`
src/remo_cli/cli/main.py                   # register self-update
src/remo_cli/cli/shell.py, cli/web.py, core/platform.py, core/provider_registry.py  # install hints
tests/unit/test_install_script.py          # rewritten for the manifest path (dry-run + full-run with shims)
tests/unit/test_release_manifest.py        # NEW
tests/unit/core/test_install_record.py     # NEW
tests/unit/core/test_version.py            # GitHub lookup + notice wording
tests/unit/providers/test_self_update.py   # NEW
tests/unit/cli/test_self_update.py         # NEW
tests/fixtures/install/                    # NEW: fixture manifests + tiny wheels-as-bytes
README.md, docs/install.md (NEW), docs/ec2.md, docs/hetzner.md, docs/provider-plugins.md,
CONTRIBUTING.md, .claude/skills/release/SKILL.md, CLAUDE.md, AGENTS.md, .specify/memory/constitution.md (IX wording, no principle change)
```

**Structure Decision**: Keep the existing single-project layout. The new Python
modules follow the established `cli/` → `providers/` → `core/` split, and the
release tooling sits beside `check_plugin_wheels.sh` in `scripts/`.

## Phasing

1. **Foundation**: the manifest format plus `scripts/release_manifest.py` (generate/verify) and its tests; `core/install_record.py` and its tests.
2. **US1 + US2 (P1)**:
   - the install.sh manifest path, `--with`, checksum verification, pipx inject and the refusals;
   - the release.yml restructure and dev-build manifest/attest;
   - the CI manifest check.
3. **US3 (P2)**: the install record write, the union/`--without` logic, `--with-pkg`/`--with-url`, and cross-manager cleanup.
4. **US4 (P2)**: the `core/version.py` GitHub lookup, `providers/self_update.py`, `cli/self_update.py`, and the notice wording plus hard-coded hints.
5. **US5 (P3)**: the docs, release skill, CONTRIBUTING, CLAUDE.md/AGENTS.md and constitution IX wording; close #257 as superseded (`gh issue close 257 --reason "not planned" --comment …`).

## Complexity Tracking

None.
