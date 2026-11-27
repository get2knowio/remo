# Tasks: One Install Path for Every Release

**Input**: Design documents from `specs/031-install-path/` (plan.md, research.md R1–R10, data-model.md, contracts/, quickstart.md)

**Tests**: Required (Constitution VI). In every phase, write the tests before the implementation and confirm they fail first.

**Format**: `- [ ] [ID] [P?] [Story] Description with file path`

## Phase 1: Setup

- [X] T001 Create the fixture directory `tests/fixtures/install/` with a `README.md` describing it. Each fixture release is a folder `<tag>/` holding `manifest.json` (line-oriented, per contracts/release-manifest.md) plus tiny placeholder "wheel" files whose sha256 matches the manifest. Add a small pytest helper `tests/unit/install_fixtures.py` that builds such a release into `tmp_path` at runtime (computing sha256), so the tests never hard-code digests. The helper needs to support:
  - a stable `v9.9.9` release;
  - an RC `rc-9.9.9rc1` release;
  - a tampered wheel;
  - a foreign URL;
  - a missing manifest.

---

## Phase 2: Foundational (blocks all stories)

- [X] T002 [P] Write `tests/unit/test_release_manifest.py` for `scripts/release_manifest.py`. Build real minimal wheels in `tmp_path` with zipfile, each containing a `*.dist-info/METADATA` with `Name`/`Version`.
  - `generate`:
    - writes the exact line layout;
    - `json.load` round-trips the output;
    - orders `remo-cli` first, then plugins sorted by name;
    - names plugins as dist minus `remo-provider-`;
    - ignores sdists;
    - refuses an unknown wheel with exit 2.
  - `verify` fails on each of the following, naming the cause:
    - invalid JSON or layout;
    - channel/tag mismatch;
    - cli version different from `release`;
    - an extra wheel in the dist dir;
    - a missing file;
    - an sha256 mismatch;
    - a foreign URL;
    - a duplicate name;
    - a plugin named `web`.
  - `verify` passes on a clean `generate`.
- [X] T003 [P] Write `tests/unit/core/test_install_record.py` for `remo_cli.core.install_record`:
  - `load_record()` handles a valid record, a missing file, invalid JSON, an unknown `format`, a value failing the allowed-character regex, and a bad channel or installer; for each failure it returns `None` plus a reason.
  - `record_path()` honours REMO_HOME, then XDG_CONFIG_HOME, then `~/.config/remo`.
  - `detect_origin()` returns:
    - `recorded`;
    - `source` (a fake distribution whose `direct_url.json` has `dir_info`);
    - `pypi` (no `direct_url.json`);
    - `unknown` (a `direct_url.json` with a `url` but no `dir_info`, or an unreadable record).
  - Monkeypatch `importlib.metadata.distribution` for these cases.
- [X] T004 Implement `scripts/release_manifest.py`, stdlib only, with `generate` and `verify` subcommands per contracts/release-manifest.md, and make T002 pass. Add `scripts/release_manifest.py` to ruff's scope in the lint commands later (T045).
- [X] T005 Implement `src/remo_cli/core/install_record.py` and make T003 pass:
  - `InstallRecord` (a frozen dataclass with the data-model fields);
  - `record_path()`;
  - `load_record() -> tuple[InstallRecord | None, str | None]`;
  - `InstallOrigin` (an Enum with `RECORDED`, `SOURCE`, `PYPI`, `UNKNOWN`);
  - `detect_origin()`.

  Constraints:
  - No provider names.
  - Reuse `core/config._resolve_remo_home` (make it public as `resolve_remo_home` only if needed, keeping the old name as an alias).

**Checkpoint**: `uv run --no-sync pytest tests/unit/test_release_manifest.py tests/unit/core/test_install_record.py`.

---

## Phase 3: User Story 1: RC install with `--with`, in one line (P1) 🎯 MVP

**Goal**: `install.sh --prerelease <rc> --with ec2,web` installs remo-cli[web] and the ec2 plugin from that RC's verified wheels.

**Independent test**: run `install.sh` with shims against a fixture RC and assert the single install command and the refusals.

- [X] T006 [US1] Rewrite the harness in `tests/unit/test_install_script.py`:
  - Keep `_run()`, adding `home=`/`remo_home=` parameters and a `full_run` mode that drops `--dry-run`.
  - Extend the `fake_path` fixture with shims:
    - a `curl` shim that maps `https://github.com/<repo>/releases/...` and `https://api.github.com/...` URLs to files under a fixture root (`REMO_TEST_FIXTURE_ROOT`), honours `-o FILE`, exits 22 on a missing file the way `-f` does, and logs every URL to `$SHIM_LOG`;
    - `uv` and `pipx` shims that log their argv to `$SHIM_LOG` and answer `uv tool list` / `pipx list --short`;
    - `sha256sum` and `shasum` shims that delegate to the real binary or to a Python fallback, and that can be omitted on demand.

  Keep these tests unchanged:
  - `test_resolver_needs_no_json_parser`;
  - the `PRERELEASE_RE` ↔ dev-build pin;
  - `test_workflow_still_tags_rc_prefixed`;
  - the Windows refusals;
  - the hola parity tests.
- [X] T007 [US1] Add RC-path tests in `tests/unit/test_install_script.py` (dry-run and full-run):
  - `--prerelease 9.9.9rc1 --with ec2,web` logs exactly one `uv tool install --force "remo-cli[web] @ file://…/remo_cli-9.9.9rc1-…whl" --with "remo-provider-ec2 @ file://…"`;
  - each combination: none, `web`, `ec2`, `hetzner`, `ec2,hetzner`, `web,ec2,hetzner`, and repeated `--with web --with ec2`;
  - `REMO_WITH` as the env twin, with the flag winning over it;
  - a bare `--prerelease` resolves the newest `rc-` tag through the API fixture;
  - `--with bogus` exits 1 listing the accepted names, and the shim log shows no wheel download and no uv call;
  - a tampered wheel exits 1 naming the file, with no uv call;
  - no `sha256sum` and no `shasum` exits 1 before installing;
  - only `shasum` present works;
  - a missing manifest (curl 22) exits 1 naming the tag;
  - bare `--prerelease` with the API answering 403 and an exhausted rate limit exits 1 naming the GitHub rate limit, nothing installed (C1);
  - `--version 9.9.9rc1` behaves exactly like `--prerelease 9.9.9rc1` (keep `test_pre_release_flag_with_version_matches_bare_version`) (C3);
  - a foreign URL or tag mismatch in the manifest exits 1;
  - `--dry-run` prints the command with manifest URLs, writes no `install.json`, and downloads no wheels;
  - pipx only: `pipx install --force "remo-cli[web] @ file://…"` followed by one `pipx inject remo-cli "remo-provider-ec2 @ file://…"`.
- [X] T008 [US1] Implement the manifest path in `install.sh` and make T007 pass:
  - `--with`/`REMO_WITH` parsing (comma-split, repeatable);
  - `manifest_url()` by channel;
  - `fetch_manifest` (curl into a `mktemp -d`, with a `trap` cleanup);
  - `manifest_field NAME KEY`, using grep/sed on the line layout;
  - tag and URL validation;
  - name validation against `web` plus the plugin names;
  - `download_and_verify` with `sha256sum` or `shasum -a 256`;
  - `build_install_cmd` for uv (one command) and pipx (install, then inject);
  - a dry-run printer.

  Constraints:
  - Keep `resolve_latest_prerelease`, but filter on `rc-` tags as it does today, and give it a distinct rate-limit message when the API response says `rate limit exceeded` (C1).
  - Remove the PyPI `install_spec` routing.
  - Update the `--help` text (no "from PyPI"; document `--with`, `REMO_WITH`, and every URL in full).
  - Bash 3.2 compatible: no `declare -A`, no `mapfile`.

**Checkpoint**: `uv run --no-sync pytest tests/unit/test_install_script.py`.

---

## Phase 4: User Story 2: Stable installs the same way; every release carries the artifact set (P1)

**Goal**: The default, `--version` and RC installs share one code path. `release.yml` and `dev-build.yml` attach the wheels, the manifest and attestations. PyPI gets only remo-cli, from the same build.

**Independent test**: the stable installer tests, plus CI's manifest verify on real wheels, plus the workflow structure tests.

- [X] T009 [P] [US2] Add stable-path tests to `tests/unit/test_install_script.py`:
  - a default run reads `https://github.com/get2knowio/remo/releases/latest/download/manifest.json`;
  - `--version 9.9.9` reads `releases/download/v9.9.9/manifest.json`;
  - an unknown version exits 1 with a clear message;
  - `REMO_REPO_SLUG` changes both URLs;
  - the shim log never contains `pypi.org` and no bare `remo-cli==`.

  Replace the obsolete PyPI-shaped assertions (`test_no_version_installs_the_package_from_pypi`, `test_final_version_pins_against_pypi`, `test_dry_run_stdout_is_only_the_command`, `test_an_explicit_flag_beats_the_environment`) with manifest-shaped equivalents. Do not delete their intent.
- [X] T010 [P] [US2] Write `tests/unit/test_release_workflows.py`, which parses the workflow YAML with `yaml.safe_load` and asserts:
  - `release.yml`:
    - has a `build` job running `uv build --all-packages`, `scripts/check_plugin_wheels.sh`, and `release_manifest.py generate` and `verify`;
    - the `release` job attaches `*.whl` plus `manifest.json` and runs `attest-build-provenance` with `id-token: write` and `attestations: write`;
    - the `publish` job downloads the build artifact (no `uv build`) and publishes from a dir that receives only `remo_cli-*`;
    - every `uses:` of a third-party action is pinned to a 40-hex SHA.
  - `dev-build.yml`:
    - the prerelease job generates and verifies the manifest;
    - it attaches `dist/*.whl` and `dist/manifest.json` (not `dist/*`);
    - it passes `--target`;
    - it runs the attestation;
    - it keeps the `v*` refusal.
  - `ci.yml`: the `wheel-install` job runs `release_manifest.py` generate and verify for a `v`-shaped and an `rc-`-shaped tag.
- [X] T011 [US2] Restructure `.github/workflows/release.yml` per research R1:
  - The new `build` job uses `actions/upload-artifact` and `release` / `publish` use `actions/download-artifact`, SHA-pinned like the other release.yml actions.
  - Pin `actions/attest-build-provenance` by SHA, looked up with `gh api repos/actions/attest-build-provenance/git/ref/tags/<latest v-tag>`.
  - `pypa/gh-action-pypi-publish` gets `packages-dir: pypi-dist/`.
  - Update the release-notes header (:117-128) to lead with the installer one-liner, URL in full.
  - Keep the pre-release detection step that `tests/unit/test_release_prerelease_detection.py` pins.
- [X] T012 [US2] Update `.github/workflows/dev-build.yml` per research R2:
  - The prerelease job runs `release_manifest.py` generate and verify, uses `gh release create "$TAG" dist/*.whl dist/manifest.json --target "$GITHUB_SHA" …`, and runs the attestation step (job permissions `contents: write`, `id-token: write`, `attestations: write`).
  - Rewrite the notes heredoc and the header comments (:37-50) to show the `install.sh --prerelease <ver> --with …` one-liner in full.
  - If the notes no longer contain `remo_cli-${VERSION}-py3-none-any.whl`, update `test_workflow_wheel_filename_matches_the_url` in `tests/unit/test_install_script.py` to pin the equivalent (the `dist/*.whl dist/manifest.json` attach line); never just delete it (I1).
- [X] T013 [US2] Extend `.github/workflows/ci.yml` job `wheel-install`: after `check_plugin_wheels.sh`, read the built remo-cli version from the wheel file name (`remo_cli-<ver>-py3-none-any.whl`) and run `release_manifest.py generate`+`verify` twice — `--tag v<ver> --channel stable` and `--tag rc-<ver> --channel prerelease` (the cli-version == release rule holds for both because `release` is taken from the wheel). No relaxation flag. Comment the job explaining it proves both release shapes would attach a full, matching set (FR-005).
  Also extend the `lint` job's ruff invocation in `.github/workflows/ci.yml` to `uv run ruff check src/remo_cli scripts/release_manifest.py` (U1).

**Checkpoint**: `uv run --no-sync pytest tests/unit/test_install_script.py tests/unit/test_release_workflows.py tests/unit/test_release_prerelease_detection.py`.

---

## Phase 5: User Story 3: Additive, remembered installs (P2)

**Goal**: `install.json` is written after success. Later runs install the union of what was recorded and what is asked; `--without` removes entries. Third-party additions are kept.

**Independent test**: two consecutive full runs in one sandbox HOME.

- [X] T014 [US3] Add tests to `tests/unit/test_install_script.py`:
  - the record contents after `--with ec2,web` match the contracts/install-record.md layout exactly, and `json.load` validates the record;
  - a second run with `--with hetzner` installs ec2, web and hetzner, and the record lists all three;
  - `--without web` removes web, and the record updates;
  - `--without notinstalled` prints an info line and exits 0;
  - re-running the same set leaves every field but `updated_at` byte-identical;
  - `--with-pkg foo==1.0` and `--with-url https://example.com/x.whl` are passed through and recorded, and kept by a later `--with ec2`;
  - `--with-pkg 'bad"name'` exits 1;
  - `--with-url http://…` (not https) exits 1;
  - a corrupt `install.json` produces a warning and is ignored, and the run installs exactly what was asked;
  - a failed install (the uv shim exits 1) leaves the previous record untouched;
  - cross-manager cleanup: chosen uv plus `pipx list --short` showing remo-cli leads to a `pipx uninstall remo-cli` after a successful install, never before;
  - a channel switch from a stable record to `--prerelease` records `prerelease`.
- [X] T015 [US3] Implement in `install.sh` and make T014 pass:
  - `read_record` (grep/sed, validated);
  - set union and removal (plain bash string lists);
  - `--without`/`REMO_WITHOUT`, `--with-pkg`, `--with-url`;
  - value validation `^[A-Za-z0-9._:/@+=,~%-]+$`;
  - `write_record` (printf to `install.json.tmp.$$`, then `mv`; the directory is created 0700);
  - `cleanup_other_manager`.

**Checkpoint**: `uv run --no-sync pytest tests/unit/test_install_script.py`.

---

## Phase 6: User Story 4: `remo self-update` and the update notice (P2)

**Goal**: One command moves a recorded install to the newest release on its channel with the same set. The notice and hints point the right way for each origin.

**Independent test**: unit tests with the network and the installer stubbed.

- [X] T016 [P] [US4] Extend `tests/unit/core/test_version.py`:
  - `get_latest_github_release(repo, "stable")` reads `releases/latest` and strips `v`;
  - `"prerelease"` picks the first `rc-` tag with `prerelease: true` and strips `rc-`;
  - a network error raises `ReleaseLookupError`;
  - an HTTP 403 with `X-RateLimit-Remaining: 0` raises `ReleaseLookupError` with a message naming the rate limit;
  - `_parse_version` and `version_is_newer` handle `5.0.0a1 < 5.0.0b2 < 5.0.0rc1 < 5.0.0`;
  - notice wording per origin:
    - `recorded`: `Run 'remo self-update' to upgrade.`, via the GitHub lookup on the record's channel;
    - `pypi`: the switch one-liner with the full URL;
    - `source`/`unknown`: today's text.
- [X] T017 [P] [US4] Write `tests/unit/providers/test_self_update.py` (stub `load_record`/`detect_origin`, the lookup, the download and `subprocess.run`):
  - recorded with a newer stable builds `bash <tmp> --version X --with ec2,web --with-pkg … --with-url …`, sets `REMO_REPO_SLUG` from the record, and fetches the installer from `https://raw.githubusercontent.com/<repo>/v<X>/install.sh`;
  - a recorded prerelease uses `--prerelease X` and the `rc-X` tag;
  - an up-to-date install runs nothing;
  - `check=True` runs nothing and reports;
  - pypi and source give guidance with nothing run;
  - pypi guidance derives `--with` from installed `remo-provider-*` distributions plus whether `fastapi` is importable (e.g. `--with ec2,web`), and omits `--with` when empty (C2);
  - an invalid record raises `PreconditionError`;
  - a lookup failure, a download failure or an installer non-zero exit raises `OperationFailedError`;
  - a declined confirm raises `UserAbortedError`.
- [X] T018 [P] [US4] Write `tests/unit/cli/test_self_update.py` using CliRunner:
  - `remo self-update --help`;
  - `--check` exits 0 with its output;
  - `--yes` skips the prompt;
  - the exit codes 0, 1 and 3 map through `@provider_command`;
  - the command is registered in `remo --help`.
- [X] T019 [US4] Implement `get_latest_github_release` and `ReleaseLookupError` in `src/remo_cli/core/version.py`, add the `a|b` forms to `_SEMVER_RE`/`_PRERELEASE_MARKERS`, and make `check_for_updates_passive` origin-aware: GitHub lookup plus self-update wording for `recorded`, the switch wording for `pypi`. Keep the cache and thread design. T016 must pass.
- [X] T020 [US4] Implement `src/remo_cli/providers/self_update.py`:
  - `plan_self_update()` returns a dataclass with the origin, current, newest, command and guidance;
  - `run_self_update(check, assume_yes, confirm=…, runner=…, fetch=…)`, using injectable seams;
  - no Click, no `sys.exit`, typed errors only.

  T017 must pass.
- [X] T021 [US4] Implement `src/remo_cli/cli/self_update.py` (`@click.command("self-update")`, `--check`, `--yes`, `@provider_command`) and register it in `src/remo_cli/cli/main.py` `_register_commands`. T018 must pass.
- [X] T022 [P] [US4] Update the hard-coded install hints to name the installer, URL in full:
  - `src/remo_cli/cli/shell.py:333` ("Consider: uv tool upgrade remo-cli" → `remo self-update`);
  - `src/remo_cli/cli/web.py:18` (`--with web`);
  - `src/remo_cli/core/platform.py:38`;
  - `src/remo_cli/core/provider_registry.py:340` (`--with <sdk_extra>`).

  Update the tests that pin these strings: grep `tests/` for each old string and update it.

**Checkpoint**: `uv run --no-sync pytest tests/unit/core/test_version.py tests/unit/providers/test_self_update.py tests/unit/cli/test_self_update.py tests/unit/test_architecture.py`.

---

## Phase 7: User Story 5: One path in docs, skills and project guidance (P3)

- [X] T023 [P] [US5] Rewrite the `## Installation` section of `README.md` (:5-~101):
  - Lead with the one-liner, including `--with` and `--prerelease` examples, every URL in full.
  - Replace "Or install it yourself" (PyPI-first) with a short "Other ways" note: from source with `uv sync`; PyPI remo-cli is secondary and has no plugins.
  - Update the options table (`--with`, `--without`, `--with-pkg`, `--with-url`, `REMO_WITH`).
  - Add a "Keeping remo up to date" section using `remo self-update`.
  - Fix `## Provider plugins` (:386-400) and the `### Version Checking` text.
- [X] T024 [P] [US5] Create `docs/install.md`, covering:
  - the channels;
  - `--with` names and where they come from (the manifest);
  - the additive record (`install.json`) and `--without`;
  - third-party plugins;
  - checksum verification and attestations (`gh attestation verify <wheel> -R get2knowio/remo`);
  - compatibility: the manifest pins plugins to the build, and the `REMO_PROVIDER_API_VERSION` handshake stays the runtime guard (FR-024);
  - switching from a PyPI install;
  - hola parity notes;
  - every URL in full.

  Link it from the README.
- [X] T025 [P] [US5] Update the install instructions in `docs/ec2.md` (:20-21, and the stale boto3 line at :408), `docs/hetzner.md` (:18-19), `docs/provider-plugins.md` (:228), `docs/incus.md` (:14) and `docs/proxmox.md` (:16) to the one-liner with `--with ec2`/`--with hetzner` as relevant.
- [X] T026 [P] [US5] Update `CONTRIBUTING.md`:
  - Release Process (:27-233): the GitHub release assets and manifest are the release, the PyPI upload of remo-cli is secondary, and plugins are never on PyPI;
  - the RC section: the `--with` install;
  - remove the stale `sync-install.yml` row (:240);
  - "Updating the Installer" (:244+): the installer is also served from each release tag and used by self-update.
- [X] T027 [P] [US5] Update `.claude/skills/release/SKILL.md`:
  - the stable lane: verify that the release carries 3 wheels, `manifest.json` and attestations, and that PyPI is secondary;
  - outcome B: the validation installs with `--prerelease X --with ec2,web` and checks `remo providers`;
  - outcome D wording unchanged.
- [X] T028 [US5] Update `CLAUDE.md` and `AGENTS.md` identically:
  - in the Commands block (:338-360), replace the PyPI-routing comment ("a final version comes from PyPI") with the manifest description, add `--with` examples and `remo self-update`, and write every URL in full;
  - Pre-release testing (Principle IX): stable and RC share the installer, RCs still never reach PyPI;
  - the project-structure tree: add `core/install_record.py`, `providers/self_update.py`, `cli/self_update.py`, and `scripts/release_manifest.py` in the scripts block;
  - Active Technologies: add a line for 031;
  - Recent Changes: add a 031 entry and move the displaced oldest entry (ec2-plugin) to `docs/feature-history.md`.

  `tests/unit/test_docs_structure.py` must pass.
- [X] T029 [US5] Update Principle IX in `.specify/memory/constitution.md`. Only the wording changes:
  - GitHub Releases is the artifact source for every release;
  - PyPI receives only final remo-cli releases, as a secondary channel;
  - RCs never reach PyPI.

  Bump the patch version (v2.1.0 → v2.1.1), add a dated amendment note, and update the `v2.1.0` reference in CLAUDE.md/AGENTS.md.

---

## Phase 8: Polish and cross-cutting

- [X] T030 Run the full core suite in the foreground: `uv sync --all-extras --no-extra hetzner --no-extra ec2 && uv run --no-sync pytest`. Then run the plugin suites: `uv sync --all-extras && uv run --no-sync pytest plugins/hetzner/tests plugins/ec2/tests`. Record the counts and fix any regressions.
- [X] T031 Run `uv run ruff check src/remo_cli scripts` and `uv run mypy src/remo_cli`; both must be clean.
- [X] T032 Run the Tier 2 local check from quickstart §2: `rm -rf dist && uv build --wheel --all-packages -o dist`, `scripts/check_plugin_wheels.sh dist`, then `release_manifest.py` generate and verify for an `rc-` tag. Tamper one byte of a wheel and confirm that verify fails. Clean up `dist/` afterwards.
- [X] T033 Run `bash -n install.sh` and `shellcheck install.sh` if shellcheck is available. Fix the findings, or explain each one you leave.
- [X] T034 File a follow-up GitHub issue: "Tier 2: cut an RC from 031 and validate the --with install end to end (quickstart §4)". The issue should say that it needs a user-approved `dev-build.yml` dispatch that creates a public `rc-<ver>` pre-release (no PyPI), plus the stable-lane check at 5.0.0. Record the issue number here. Filed as #289.
- [ ] T035 Close #257 as superseded with `gh issue close 257 --reason "not planned" --comment "Superseded by spec 031 (one install path): plugins ship only as GitHub Release assets installed by install.sh --with; PyPI no longer needed for them."`. **Defer this until the PR merges.** The parent session does it after merge; the implement agent leaves it unchecked.
- [X] T036 (Amendment 2026-11-27, owner decision: never PyPI) Remove `release.yml`'s `publish` (PyPI) job and every PyPI-publishing comment/note in the workflows; add a test that no workflow uses `pypa/gh-action-pypi-publish` or a `pypi` environment; redefine Constitution IX ("Releases Ship Only Through GitHub Releases — Never PyPI", 2.1.0 → 3.0.0) and sync CLAUDE.md/AGENTS.md/plan-template; drop the release skill's outcome D and PyPI rules; make the passive notice of an install without a record look up the newest GitHub stable release (removing the PyPI `get_latest_release`); reword the self-update PyPI guidance; repoint the legacy root `remo` stub at install.sh; amend spec/plan/research/data-model/contract/quickstart in place. Superseded wording in T013-era tasks above (PyPI secondary upload, `pypi-dist/`) is history, not current state.

---

## Dependencies

- Phase 1 → Phase 2 → US1 → US2 (both edit install.sh and its tests in sequence) → US3 (install.sh again) → US4 (independent of install.sh internals; it may run in parallel with US2/US3 after Phase 2) → US5 → Polish.
- T004 blocks T011, T012 and T013. T005 blocks T019 and T020.

## Parallel examples

- Phase 2: T002 ∥ T003, then T004 ∥ T005.
- US2: T009 ∥ T010, then T011 ∥ T012 ∥ T013.
- US4: T016 ∥ T017 ∥ T018 ∥ T022, then T019 → T020 → T021.
- US5: T023–T027 in parallel, then T028 → T029.

## Implementation strategy

MVP = Phase 2 + US1: an RC installs with plugins. US2 makes stable identical and makes the releases carry the files. US3 and US4 make it pleasant to live with. US5 makes it the documented default.
