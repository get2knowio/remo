# Feature Specification: One Install Path for Every Release

**Feature Branch**: `031-install-path`

**Created**: 2026-11-27

**Status**: Draft (amended 2026-11-27: never PyPI)

**Input**: User description: "Make GitHub Releases the single artifact source for every remo version, release candidate and stable alike, and make `install.sh` (curl + shell, as in hola) the supported way to install remo with its provider plugins and extras, so an RC installs through exactly the path the stable release will." (Source: Notion spec prompt "One install path for every release — GitHub Releases + install.sh --with, plugins included", grounded in `main` at 62b16f0, 2026-11-27. Decided 2026-11-27: curl + shell is the default for every release, RC and stable; PyPI is no longer the default for anything. Supersedes #257; unblocks the 5.0.0 stable release. **Amended 2026-11-27 (owner decision): remo is no longer uploaded to PyPI at all — stable, RCs and plugins, no exceptions; see Clarifications.**)

## Context

remo ships as one CLI distribution (`remo-cli`) plus first-party provider
plugins (`remo-provider-ec2`, `remo-provider-hetzner`, each its own
distribution and version since #258/#259) and an optional `web` extra of the
CLI itself. Today these reach users by two unrelated routes:

- **Stable** releases go to PyPI (remo-cli only). The GitHub release carries
  notes but no files. The `ec2`/`hetzner` extras name distributions that are
  not on PyPI, so `remo-cli[ec2]` cannot install from PyPI at all — the reason
  #257 (publish plugins to PyPI) blocks 5.0.0.
- **Release candidates** never reach PyPI (Constitution IX). `dev-build.yml`
  attaches every wheel to an `rc-<ver>` GitHub pre-release, and `install.sh
  --prerelease` installs remo-cli from it — but cannot add a plugin or extra,
  so an RC cannot exercise AWS or Hetzner without a hand-written `uv tool
  install … --with …` command.

So an RC is tested through a different path than the stable release ships
through, and the plugins have no supported install path at all. This feature
makes one path — GitHub Releases as the artifact source, `install.sh` as the
installer — serve both, with plugins and extras as first-class choices.

## Clarifications

### Session 2026-11-27

- Q: Is PyPI kept at all? → A: ~~remo-cli stable *may* keep a secondary PyPI upload so existing PyPI installs keep upgrading, but nothing documented or automated depends on it; plugins are never published to PyPI.~~ **Superseded 2026-11-27** by the last Q below: PyPI is not kept. #257 closes as superseded.
- Q: Where does a stable install come from? → A: The wheel attached to the stable GitHub release ~~, the identical file uploaded to PyPI (no rebuild)~~ — the same code path as an RC, differing only in which release's manifest is read. (**Amended 2026-11-27**: there is no PyPI upload to be identical to.)
- Q: How does `remo self-update` run the install? → A: It fetches `install.sh` from the target release's own tag (`v<ver>` for stable, `rc-<ver>` for a pre-release) and runs it with the recorded set as flags (`--version`/`--prerelease VER`, `--with …`, `--with-pkg …`, `--with-url …`), so install logic lives in one place and the installer always matches the release it installs. Python never reimplements the install.
- Q: Does the installer hand the manifest URLs to uv/pipx, or the files it verified? → A: It downloads each wheel to a private temp dir, verifies its SHA-256, and installs from those verified local files — so what was checked is exactly what is installed (no second download). The manifest URL is still recorded for provenance.
- Q: Does the stable PyPI upload of remo-cli continue? → A: ~~Yes, unchanged, as a secondary convenience so existing `uv tool install remo-cli`/pipx installs keep upgrading; it uploads the same wheel files that are attached to the GitHub release. Plugins are never uploaded to PyPI.~~ **Superseded 2026-11-27**: No — see the last Q below.
- Q: When is the install record written? → A: Only after the tool-manager install exits successfully; never on `--dry-run` or on any failure. Writes are atomic (temp file + rename) and the record carries `format: 1`.
- Q: How does remo tell a PyPI install from a source checkout when there is no record? → A: From the running distribution's own install metadata: an editable/local-directory install (PEP 610 `direct_url.json` with `dir_info`) is a source checkout; anything else without a record is treated as a PyPI tool install. Unknown cases print both commands rather than guessing one.
- Q: (Reconciliation with the code audit at 62b16f0) What makes a plugin component "belong to" a release, given plugin wheels carry their own versions (0.1.0) independent of remo-cli? → A: Belonging is by build and location, not version number: every component in a manifest is produced by the one build run that produced remo-cli, and every component URL lives under that release's own download path (`releases/download/<tag>/`). CI refuses to publish a manifest that fails either check; the installer refuses a manifest with a component URL outside its release's tag. Plugin versions stay independent.
- Q: (Reconciliation) The installer test suite forbids a JSON parser (`jq`/`python3`) in install.sh. How is the manifest read? → A: The manifest stays JSON but is written line-oriented (one component object per line, fixed key order), a documented contract, so the installer reads it with grep/sed like its existing release resolution; the test stays. The install record uses the same shape.
- Q: (Reconciliation) FR-003 said stable wheels are also "baked into the image", but the Dockerfile builds its own wheels and FR-028 keeps the image unchanged. → A: FR-028 wins; FR-003 amended to cover the GitHub release ~~and PyPI~~ only.

### Session 2026-11-27 (owner decision, after review)

- Q: Does remo keep *any* PyPI upload — the "secondary" stable remo-cli upload of FR-005a included? → A: No. The repo owner decided that remo is no longer uploaded to PyPI at all: stable, RCs and plugins, with no exceptions. GitHub Releases (wheels + `manifest.json` + attestations, installed by `install.sh`) is the only package channel, alongside the GHCR `remo-web` image. `release.yml`'s `publish` (PyPI) job is removed and a test forbids any workflow from using `pypa/gh-action-pypi-publish` or a `pypi` environment. Constitution Principle IX is redefined accordingly ("Releases Ship Only Through GitHub Releases — Never PyPI", v2.1.0 → 3.0.0, MAJOR). The last versions already on PyPI stay there, frozen. Because PyPI will never get a newer version, the passive update notice of a PyPI-origin install looks up the newest **GitHub stable** release (not PyPI) and shows the switch-to-installer one-liner; source and unknown origins also look up GitHub stable. `InstallOrigin.PYPI` stays: it still describes existing installs.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Install a release candidate with the plugins I need, in one line (Priority: P1)

A tester on a clean machine wants to try an RC against AWS and the web console.
They run the installer one-liner with `--prerelease <rc> --with ec2,web` and get
remo with the EC2 provider and the web extra, all from that RC's release.

**Why this priority**: This is the gap that blocks RC testing of any plugin
today and the core of the change; everything else builds on its manifest and
install code path.

**Independent Test**: Run the installer against a fixture release (shimmed
download + installer tools) with `--prerelease X --with ec2,web` and assert the
single install command names the remo-cli wheel with the `web` extra plus the
ec2 plugin wheel, all from release X, after checksum verification.

**Acceptance Scenarios**:

1. **Given** an RC release carrying remo-cli and plugin wheels plus a manifest, **When** the user installs with `--prerelease <rc> --with ec2,web`, **Then** remo is installed from that RC's remo-cli wheel with the web extra, the ec2 plugin is installed from that RC's plugin wheel, and `remo providers` lists ec2.
2. **Given** `--prerelease` with no version, **When** the installer runs, **Then** it uses the newest pre-release's manifest.
3. **Given** `--with ec2,bogus`, **When** the installer runs, **Then** it fails naming `bogus` and the accepted names, before anything is downloaded or changed.
4. **Given** a downloaded wheel whose checksum does not match the manifest, **When** the installer runs, **Then** it refuses to install and leaves the existing installation untouched.
5. **Given** `--dry-run`, **When** the installer runs, **Then** it prints the exact install command and changes nothing.
6. **Given** the user already uses pipx and not uv, **When** they install with plugins, **Then** pipx installs remo-cli and injects each plugin, with the same result.

---

### User Story 2 - Stable installs the same way, from the same kind of release (Priority: P1)

A user installs the latest stable remo with the Hetzner provider using the same
one-liner without `--prerelease`. The stable GitHub release now carries the
same artifact set as an RC, and the installer reads it the same way.

**Why this priority**: Without plugin wheels on the stable release, 5.0.0
cannot ship a working `ec2`/`hetzner` install; this replaces #257.

**Independent Test**: Assert the release workflow attaches remo-cli + every
plugin wheel + a manifest to the stable release, that the manifest matches the
built wheels (names, versions, checksums), and that the installer resolves the
latest stable manifest for a default run.

**Acceptance Scenarios**:

1. **Given** a stable release is published, **When** its GitHub release is inspected, **Then** it carries the remo-cli wheel, every first-party plugin wheel, a manifest describing them, and provenance attestations for the wheels.
2. ~~**Given** the stable release also uploads remo-cli to PyPI, **When** the attached wheel and the uploaded wheel are compared, **Then** they are byte-identical.~~ **Superseded 2026-11-27** (no PyPI upload): **Given** a stable release is published, **When** its workflows are inspected, **Then** no job uploads anything to PyPI or TestPyPI (no `pypa/gh-action-pypi-publish`, no `pypi` environment), and the GHCR image waits for the GitHub release.
3. **Given** a default run (no `--prerelease`, no `--version`) with `--with ec2`, **When** the installer runs, **Then** it installs the latest stable remo-cli and ec2 plugin wheels from the GitHub release, never from PyPI.
4. **Given** `--version 5.0.0`, **When** the installer runs, **Then** it reads that release's manifest; an unknown version fails with a clear message.

---

### User Story 3 - Add a plugin later without losing what I have (Priority: P2)

A user installed with `--with ec2,web`. Later they want Hetzner too. Running
the installer with `--with hetzner` leaves them with ec2, web and hetzner, not
hetzner alone. `--without web` removes one.

**Why this priority**: Tool managers replace the whole environment on a forced
reinstall, so without a record of prior choices a second install silently drops
plugins.

**Independent Test**: Run the installer twice in a sandbox HOME; assert the
second install command contains the union and that the recorded install state
reflects it; run with `--without` and assert removal.

**Acceptance Scenarios**:

1. **Given** a recorded install with ec2 and web, **When** the user runs with `--with hetzner`, **Then** the installed set is ec2, web and hetzner and the record says so.
2. **Given** a recorded install with ec2, web and hetzner, **When** the user runs with `--without web`, **Then** web is no longer installed and no longer recorded.
3. **Given** the same set is requested again at the same version, **When** the installer runs, **Then** the result is unchanged (idempotent).
4. **Given** a user adds a third-party plugin by package name or URL, **When** they later install a first-party plugin, **Then** the third-party plugin is kept.
5. **Given** `--without` names something not installed, **When** the installer runs, **Then** it says so and proceeds without error.

---

### User Story 4 - Update in one command, keeping my set (Priority: P2)

A user installed by the one-liner. When a newer release appears on their
channel, `remo self-update` installs it with the same extras and plugins.
`remo self-update --check` only reports.

**Why this priority**: Today's update notice tells URL installs to run a
command that does not work for them.

**Independent Test**: Unit-test the command against each install origin
(recorded install, PyPI tool install, source checkout) with the network and the
installer stubbed; assert the action taken or the guidance printed.

**Acceptance Scenarios**:

1. **Given** a recorded stable install and a newer stable release, **When** the user runs `remo self-update`, **Then** the installer re-runs for the newer version with the recorded extras and plugins.
2. **Given** a recorded pre-release install, **When** a newer pre-release exists, **Then** self-update moves to the newest pre-release.
3. **Given** the installed version is already the newest on the channel, **When** self-update runs, **Then** it says so and changes nothing.
4. **Given** `--check`, **When** self-update runs, **Then** it reports current vs. newest and changes nothing.
5. **Given** no install record (PyPI tool install or source checkout), **When** self-update runs, **Then** it prints the right command for that origin — for a PyPI install, the one-time switch to the installer one-liner — and changes nothing.
6. **Given** the release lookup fails (offline, rate-limited), **When** self-update runs, **Then** it reports the failure with a non-zero exit and changes nothing.
7. **Given** a passive update notice is due, **When** an install record exists, **Then** the notice names `remo self-update`; otherwise it names the switch-over one-liner for a PyPI install ~~and today's text elsewhere~~ (**amended 2026-11-27**: stating that PyPI no longer receives remo releases), `git pull && uv sync` for a source checkout, and the installer one-liner for an unknown origin. Without a record, the newer version is looked up as the newest GitHub stable release, never on PyPI.

---

### User Story 5 - Maintainers and docs describe one path (Priority: P3)

The README, the install docs, the release skill and the project guidance all
lead with the installer one-liner, for RC and stable alike, with every URL in
full, and say plainly that ~~RCs never reach PyPI and plugins are distributed
only through GitHub Releases~~ (**amended 2026-11-27**) no remo release, plugin
or RC is ever published to PyPI: GitHub Releases is the only channel.

**Why this priority**: Required for the change to be the default in practice,
but carries no runtime behavior.

**Independent Test**: Docs-structure tests and review: the install section
shows the one-liner with `--with`, no elided URLs, and no PyPI-first install
instruction.

**Acceptance Scenarios**:

1. **Given** the README install section, **When** read, **Then** it leads with the one-liner and shows `--with` and `--prerelease`.
2. **Given** the release skill's stable lane, **When** followed, **Then** it treats the GitHub release's assets and manifest as the release ~~and any PyPI upload as secondary~~ and offers no PyPI upload at all (**amended 2026-11-27**).

### Edge Cases

- The manifest is missing from a release (e.g. an older release cut before this feature): the installer fails with a message naming the release and suggesting a newer version; it never falls back to PyPI silently.
- A manifest lists a plugin built for a different remo version: CI refuses to publish such a release; the installer refuses a manifest whose components do not all belong to its release.
- The install record is unreadable or from a newer format: the installer warns, ignores it, and installs exactly what was asked; self-update stops with guidance instead of guessing.
- The user switches channel (stable install, then `--prerelease`): the record's channel updates to the newly installed one; self-update follows it.
- `--with` given both as a flag and its environment twin: the flag wins, consistent with existing flag-over-env behavior.
- No checksum tool on the machine: the installer fails before installing rather than skipping verification.
- An existing PyPI tool install of remo-cli: under the same tool manager, the forced install replaces it in place; under the *other* tool manager (e.g. a pipx PyPI install when uv is chosen), the installer uninstalls that copy first so there is one remo on PATH (amended 2026-11-27: today's old-install cleanup handles only the pre-3.0 git checkout, not this).
- GitHub API rate limit while resolving "newest pre-release": a clear error naming the limit, nothing changed.

## Requirements *(mandatory)*

### Functional Requirements

**Release artifacts**

- **FR-001**: Every release — stable and RC — MUST attach to its GitHub release the remo-cli wheel, every first-party plugin wheel, and a manifest.
- **FR-002**: The manifest MUST record the release version and channel (stable or prerelease) and, per component (`remo-cli`, `ec2`, `hetzner`), its install name, distribution name, version, wheel file name, download URL and SHA-256 checksum.
- **FR-003**: Stable release assets MUST come from one build job with no rebuild between build and release ~~and be the identical wheel files uploaded to PyPI (one build job feeds both)~~ (**amended 2026-11-27**: there is no PyPI upload). The Docker image keeps building its own wheels from source (FR-028 governs; amended 2026-11-27, see Clarifications).
- **FR-004**: Release workflows MUST generate provenance attestations for the attached wheels.
- **FR-005**: CI MUST verify, for both a stable-shaped and an RC-shaped release, that the full artifact set is present and that the manifest matches the built wheels (names, versions, checksums), failing the build otherwise.
- **FR-005a**: ~~The stable PyPI upload of remo-cli continues unchanged as a secondary convenience, uploading the same wheel file attached to the GitHub release; plugins are never uploaded to PyPI.~~ **Superseded 2026-11-27 (owner decision)**: No build of remo-cli or any plugin — stable or RC — MAY be uploaded to PyPI or TestPyPI. `release.yml` has no PyPI job, and CI MUST fail if any workflow uses `pypa/gh-action-pypi-publish` or a `pypi` environment (Constitution IX v3.0.0).
- **FR-006**: No RC path may create a `v*` tag ~~or upload to PyPI~~ (Constitution IX; since 2026-11-27 no path at all uploads to PyPI, FR-005a); RC tags remain `rc-<version>`.

**Installer**

- **FR-007**: The installer MUST accept `--with LIST` (comma-separated, repeatable) naming any of `web`, `ec2`, `hetzner`, with an environment-variable twin; a flag overrides the environment.
- **FR-008**: The installer MUST resolve the manifest by channel: latest stable by default; a named stable release with `--version`; a named pre-release with `--prerelease VER`; the newest pre-release with bare `--prerelease`.
- **FR-009**: The installer MUST install remo-cli (with `web` as an extra when requested) and each requested plugin from the checksum-verified local copies of the manifest's wheels in a single tool-manager operation (uv), or install-then-inject (pipx).
- **FR-010**: The installer MUST verify every downloaded wheel's SHA-256 against the manifest before installing, and refuse on any mismatch, leaving the existing installation untouched.
- **FR-011**: Unknown `--with`/`--without` names MUST fail before anything is downloaded or changed, listing the accepted names.
- **FR-012**: `--dry-run` MUST print the exact command(s) that would run and change nothing, including the install record.
- **FR-013**: The default install path MUST NOT fall back to PyPI for remo-cli or any plugin; stable and RC differ only in which manifest is read.
- **FR-014**: The installer MUST never mix components from different releases: every component comes from the one manifest read.
- **FR-015**: The installer MUST accept third-party additions — `--with-pkg NAME[==VER]` (an index package) and `--with-url URL` — passing them through to the tool manager and recording them.

**Install record**

- **FR-016**: Only after the tool-manager install exits successfully (never on `--dry-run` or failure), the installer MUST atomically write an install record at the remo config directory (`install.json`) holding format version, channel, installed version, tool manager, extras, first-party plugins and third-party additions.
- **FR-017**: A later install MUST install the union of the recorded set and the requested set; `--without NAME` removes a recorded entry; re-running the same set at the same version MUST leave the install and record unchanged apart from timestamps.
- **FR-018**: An unreadable or unknown-format record MUST be reported and ignored by the installer, never silently trusted.

**Self-update and notices**

- **FR-019**: `remo self-update` MUST read the install record, find the newest release on the recorded channel, fetch the installer from that release's tag, and run it for that version with the recorded extras, plugins and third-party additions.
- **FR-020**: `remo self-update --check` MUST report the installed and newest versions and change nothing.
- **FR-021**: With no install record, `remo self-update` MUST print the correct guidance for the origin detected from the running distribution's install metadata (PyPI tool install → the one-time switch to the installer one-liner; source/editable checkout → update the checkout; undeterminable → both) and change nothing.
- **FR-022**: Self-update failures (lookup failure, installer failure, missing tools) MUST surface as typed errors with actionable messages and a non-zero exit.
- **FR-023**: The passive update notice MUST name `remo self-update` when an install record exists; for a PyPI install it MUST name the switch-over one-liner ~~; otherwise it keeps today's text~~. **Amended 2026-11-27**: the PyPI notice MUST say that PyPI no longer receives remo releases; a source checkout's names `git pull && uv sync`, an unknown origin's the installer one-liner. Without a record, the newer version MUST be looked up as the newest GitHub stable release (`get_latest_github_release`), never on PyPI, which will never receive one.

**Compatibility, docs and process**

- **FR-024**: Documentation MUST state that the manifest pins each first-party plugin to the build released with that remo version, and that the `REMO_PROVIDER_API_VERSION` handshake remains the runtime guard.
- **FR-025**: README, the install documentation, the release skill, and the project guidance (CLAUDE.md/AGENTS.md: Pre-release testing and Principle IX wording) MUST describe the installer one-liner as the default for RC and stable, with every URL written in full, and keep the hola parity notes current.
- **FR-026**: #257 MUST be closed as superseded once this lands.
- **FR-027**: Installer tests MUST cover: stable and RC resolution, each `--with` combination, additive re-runs and `--without`, checksum mismatch refusal, unknown names, `--dry-run`, the pipx path, and install-record contents. Unit tests MUST cover self-update for each install origin and the notice wording.
- **FR-028**: The Docker image, source/`uv sync` workflows and the platform gate (no Windows) MUST be unchanged.

### Key Entities

- **Release manifest**: one per GitHub release. Release version, channel, and a list of components; each component has an install name (`remo-cli`, `ec2`, `hetzner`), distribution name, version, wheel file name, URL, SHA-256.
- **Install record** (`install.json`): format version, channel, installed version, tool manager (uv|pipx), extras (e.g. `web`), first-party plugins, third-party additions (packages and URLs), last-updated time.
- **Install origin**: how the running remo got there — recorded installer install, PyPI tool install, or source checkout — decides what self-update and the notice say.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tester can install any RC with any combination of `web`, `ec2`, `hetzner` using one command, on a clean machine, with no hand-written install command.
- **SC-002**: 100% of releases cut after this feature (stable and RC) carry the same artifact set: remo-cli wheel, every first-party plugin wheel, manifest, attestations.
- **SC-003**: The stable and RC install paths share one code path; the only difference is which manifest is read.
- **SC-004**: Adding a plugin to an existing install never removes a previously installed plugin or extra (0 losses across the additive test matrix).
- **SC-005**: Every downloaded wheel is checksum-verified before install; a tampered wheel is refused in 100% of tested cases.
- **SC-006**: `remo self-update` moves a recorded install to the newest release on its channel with the same set, in one command.
- **SC-007**: The 5.0.0 stable release no longer depends on publishing plugins to PyPI (**amended 2026-11-27**: nor on publishing anything to PyPI; remo is never uploaded there).

## Assumptions

- GitHub Releases' `releases/latest` resolves to the newest stable (non-pre-release) release, so stable manifest resolution needs no API call; "newest pre-release" uses the existing GitHub API lookup in `install.sh`.
- The machine has `curl` and either `sha256sum` or `shasum` (macOS); the installer does not need `jq` — manifest reading uses tools the installer already relies on, or the Python that uv/pipx bring.
- Third-party `--with-pkg` additions still resolve from an index (PyPI) — only first-party components are GitHub-only. (remo itself is never on an index: **amended 2026-11-27**.)
- (**Added 2026-11-27**) The versions of `remo-cli` already on PyPI stay there, frozen; they are never yanked, updated or added to. Existing PyPI installs switch by running the installer one-liner once.
- The first release to carry a manifest is the next one after this lands (5.0.0 or its RCs); older releases are not backfilled.
- Attestations use GitHub's built-in artifact attestation; verifying them is documented but not enforced by the installer.
- Hola parity: flag spellings and environment variable naming keep following try-hola/hola's `cli-install.sh` conventions where it has an equivalent.
