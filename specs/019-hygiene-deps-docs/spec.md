# Feature Specification: Dependency, Dead-Code & Documentation Hygiene

**Feature Branch**: `019-hygiene-deps-docs`

**Created**: 2026-07-26

**Status**: Draft

**Input**: User description: "A hygiene pass aligning the codebase's dependencies, dead code, and documentation with reality. Dependencies: remove hcloud from pyproject.toml — it is declared as a hard dependency but never imported anywhere (providers/hetzner.py talks to the Hetzner REST API via raw urllib, with two separate hand-rolled HTTP clients in one file that should be consolidated); investigate the dev dependency \"httpx2\", which appears to be a typo'd package name for httpx; decide explicitly whether boto3 stays a hard dependency or returns to an optional extra, and make pyproject, the lazy-import machinery in providers/aws.py (currently guarding an impossible state), and the docs agree. Dead code: remove the accepted-but-never-forwarded --yes/-y flag on all four provider create commands (or wire it up — pick one); remove providers/proxmox.py's unused _parse_pct_json; fix the shadowed json import in providers/incus.py info(). Docs: CLAUDE.md and README describe a \"remo init\" command, cli/init_cmd.py, and core/init.py that do not exist, list aws/hetzner extras that no longer exist (only web and dev extras remain), omit the add/remove and completion commands, and misstate which providers have the info command. Regenerate the project-structure and commands sections to match the actual tree, and establish that CLAUDE.md's structure section is verified against the filesystem as part of the change (a drift check in CI or a documented update ritual). Update the Active Technologies list (e.g. \"hcloud (Hetzner, optional)\" is doubly wrong)."

## Pre-Specification Findings

The originating description was written 2026-07-25, before feature 018 landed on `main`. Every claim in it
was re-verified against the working tree at `2c0c283`. Findings that **change the scope** of this feature:

| Original claim | Verified state | Effect on scope |
|---|---|---|
| `hcloud` is declared but "never imported anywhere" | True for `src/remo_cli/` (Hetzner uses raw `urllib`), but **false for the product as a whole**. The `hetzner.hcloud` Ansible collection (`ansible/requirements.yml`) backs `hetzner create`, `destroy`, and `resize`, and its modules `import hcloud` under `ansible_playbook_python` — the same interpreter that carries the CLI's own dependencies. `ansible/hetzner_site.yml` and `ansible/hetzner_teardown.yml` even carry the comment "Use the same Python that runs ansible-playbook (has hcloud installed)". | Removing `hcloud` outright would break Hetzner provisioning. The requirement becomes *classify it correctly*, not *delete it*. |
| `httpx2` "appears to be a typo'd package name for httpx" | **Not a typo.** `httpx2` 2.7.0 is a real package published by pydantic (`github.com/pydantic/httpx2`). The installed Starlette 1.3.1 `testclient` module resolves it first (`import httpx2 as httpx`, falling back to `import httpx`). The full 1746-test suite collects and the `TestClient`-backed tests pass with `httpx2` installed and no `httpx` present. | Item resolves to *verify and record the rationale*, not *fix a typo*. |
| Shadowed `json` import in `incus.py` `info()` | Already fixed in 018. | **Dropped.** |
| `--yes`/`-y` on `create` is "accepted but never forwarded" | 018 converted it to a declared deprecation (`CREATE_YES_DEPRECATION` on all four descriptors; the factory prints a notice and discards the value). | Becomes *complete the removal*, subject to the release-timing decision below. |
| `core/completion.py` per-provider completers | Deleted in 018; completion is descriptor-generated. | **Dropped.** |
| Docs describe `remo init`, `cli/init_cmd.py`, `core/init.py` | Confirmed still wrong. `remo init` is absent from the CLI; the registered commands are `shell`, `cp`, `add`, `remove`, `completion`, `web`, and the four provider groups. Ansible collections are installed automatically by `core/ansible_runner.py` on requirements-hash change, so no init step is needed at all. | Confirmed in scope, and worse than described — it is the **first command in the README's install instructions**. |
| Docs list `aws`/`hetzner` extras | Confirmed: only `dev` and `web` extras exist. | Confirmed in scope. |
| Docs "misstate which providers have the info command" | **Not reproduced.** `info` is generated for all four providers by the CLI factory, all four provider modules implement it, and the README documents it for all four. | **Dropped.** |
| Docs omit `add`/`remove`/`completion` | Partly wrong: the README documents all three. **CLAUDE.md** omits them from its Commands section. | Narrowed to CLAUDE.md/AGENTS.md. |

### Scope decision: truth now, slimming separately

The dependency footprint was measured to decide whether "remove `hcloud`" should become "make the
provider SDKs optional". Clean `uv venv` installs, Python 3.12, no extras — default install is 65 MB of
`site-packages`:

| Stack | Size | % of install |
|---|---:|---:|
| `boto3` (botocore 24.6, boto3 1.05, urllib3 0.49, dateutil 0.47, s3transfer 0.35, jmespath 0.08, six 0.04) | 27.1 MB | 42% |
| `hcloud` (hcloud 0.70, idna 0.34, requests 0.26, certifi 0.25, charset_normalizer 0.23) | 1.8 MB | 2.7% |
| Combined | 28.9 MB | 45% |

`botocore` alone is 38% of the install; `hcloud` is 0.70 MB. **`boto3` is 94% of the available savings.**
A further 41 MB of Ansible collections (`amazon.aws` 9 MB and `community.aws` 6 MB among them) is
installed unconditionally for every user on their first playbook run, so a pyproject-only change would
capture 29 MB of a ~44 MB provider-specific footprint while creating a new failure mode — collections
present, SDK absent, and no preflight in the AWS or Hetzner teardown/resize playbooks to catch it.

**This feature therefore changes no dependency's required/optional status.** It makes the existing
declarations *truthful and annotated*. The slimming work — an `aws` extra, per-provider collection
scoping, and the missing SDK preflights — is tracked separately as
[issue #94](https://github.com/get2knowio/remo/issues/94).

Additional drift found during verification, not in the original description:

- **`AGENTS.md` is a severely stale fork of `CLAUDE.md`.** It describes the package as `src/remo/`,
  documents a flat-file `known_hosts` registry, and lists three "notifier sidecar" features (007/008/009)
  with dependencies (`python-telegram-bot`, `structlog`, `httpx`) that exist nowhere in this repository.
- **`CLAUDE.md` structure drift, measured** with a path-reconstructing parser: **2** phantom entries
  (`cli/init_cmd.py`, `core/init.py`), **13** modules that exist but are undocumented, and **2**
  grouped lines that name four files each (`incus.py / hetzner.py / aws.py / proxmox.py` and the
  `*_descriptor.py` equivalent), which hide 8 of those 13. The 13: `cli/added.py`,
  `providers/added.py`, `providers/{incus,hetzner,aws,proxmox}.py`,
  `providers/{incus,hetzner,aws,proxmox}_descriptor.py`, `web/operator_auth.py`, `web/pairing.py`,
  `web/api/pairing.py`. Splitting the two grouped lines documents 8 of them; **5** then remain to be
  added by hand.
- **`remo init` appears in three more places** beyond the README: `docs/aws.md`, and twice in
  `docs/install.sh` — the last of which prints it as a post-install "get started" instruction.
- **`ansible/hetzner_resize.yml` and `ansible/hetzner_teardown.yml` use `hetzner.hcloud` modules with no
  `hcloud`-present preflight**, unlike `roles/hetzner_server/tasks/main.yml`, which checks and pip-installs
  it. A user whose first Hetzner command is `destroy` or `resize` has no self-healing path.
- **The `--yes` deprecation notice added by 018 has never shipped.** 018 is unreleased (`v2.2.0` predates
  it), so no released version has ever printed the warning.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A new user's documented install path actually works (Priority: P1)

Someone installs `remo-cli` for the first time and follows the README's Installation section verbatim.
Today the second instruction is `remo init`, which does not exist — the CLI exits with a usage error
before the user has done anything. The same phantom command is printed by the installer script as a
"get started" hint and referenced in the AWS guide. After this change, every command a user is told to
run in installation and getting-started documentation exists and succeeds.

**Why this priority**: It is the very first interaction a new user has with the tool, and it currently
fails. No other item in this feature is visible to a user who never gets past installation.

**Independent Test**: Take a clean machine, follow README Installation → Quick Start literally, and
confirm every command runs without a usage error. Shippable on its own.

**Acceptance Scenarios**:

1. **Given** a clean environment with `remo-cli` freshly installed, **When** the user runs each command in
   the README Installation and Quick Start sections in order, **Then** no command fails with "no such
   command" or an equivalent usage error.
2. **Given** the installer script completes, **When** it prints its "get started" hints, **Then** every
   command shown is a real `remo` command.
3. **Given** a user who has never installed Ansible collections, **When** they run their first
   provider command, **Then** collections are installed automatically and the documentation explains
   that this is automatic rather than instructing a manual step.
4. **Given** any documentation page in the repository, **When** it is searched for `remo init`,
   **Then** there are no occurrences.

---

### User Story 2 - Dependency declarations state what is actually required, and why (Priority: P2)

An operator or contributor reads `pyproject.toml` to understand what installing `remo-cli` pulls in and
why. Today three declarations are unexplained or wrong: `hcloud` looks unused (its real consumer is the
Ansible layer, not Python code), `boto3` is a hard dependency while the code and docs still describe an
optional-extra world, and `httpx2` reads as a typo for `httpx`. After this change each declaration is
correct, and any declaration whose necessity is not obvious from the Python source carries a comment
naming its actual consumer.

**Why this priority**: Wrong dependency metadata causes either broken installs (if a load-bearing
dependency is dropped) or bloated installs and misleading error messages (if optional ones are
mandatory). It is invisible until it breaks, but it breaks badly.

**Independent Test**: Read `pyproject.toml` and confirm every dependency is either imported in
`src/remo_cli/` or annotated with its non-Python consumer; then install the package in a clean
environment and exercise the affected provider paths.

**Acceptance Scenarios**:

1. **Given** `pyproject.toml`, **When** a reader examines each runtime dependency, **Then** every
   dependency not imported anywhere in `src/remo_cli/` is annotated with the component that requires it.
2. **Given** a clean install of the package, **When** the user runs `hetzner create`, `hetzner destroy`,
   and `hetzner resize`, **Then** each completes its Hetzner API interactions without a missing-library
   failure — including when `destroy` or `resize` is the very first Hetzner command ever run.
3. **Given** a clean install, **When** the user runs any AWS command, **Then** it works immediately —
   unchanged from today — and the documentation says so rather than describing an extra to install.
4. **Given** the test dependency set, **When** the full test suite is collected and run, **Then** it
   collects and passes, and the reason the chosen HTTP-client package is correct is recorded in the
   dependency declaration.
5. **Given** the resolved dependency model, **When** `pyproject.toml`, the provider-side missing-SDK
   handling, the descriptor metadata that names installable extras, and the documentation are compared,
   **Then** all four agree — no document names an extra that does not exist, and no code path guards
   against a state that cannot occur.

---

### User Story 3 - Repository documentation matches the repository (Priority: P3)

A contributor (human or AI agent) opens `CLAUDE.md`, `AGENTS.md`, or the README to orient themselves.
Today the structure diagram lists two files that do not exist and omits thirteen that do, the Commands
section advertises two extras that were removed, and `AGENTS.md` describes an entirely different
package layout plus three features that are not in this repository. After this change every documented
path, command, extra, and technology entry corresponds to something real.

**Why this priority**: Stale orientation docs actively mislead — the constitution names this explicitly
("Stale documentation is worse than no documentation"). It costs contributor time on every task but does
not break the shipped product.

**Independent Test**: Cross-check each file path, command name, and extra name mentioned in the
orientation documents against the working tree and the CLI's own help output.

**Acceptance Scenarios**:

1. **Given** the structure diagram in the orientation documents, **When** each source path in it is looked
   up in the working tree, **Then** all are found, and every source module in the tree appears in the
   diagram.
2. **Given** the Commands section, **When** each documented install command is run, **Then** it succeeds —
   no command references a non-existent extra.
3. **Given** the documented CLI surface, **When** it is compared with the commands the CLI actually
   registers, **Then** the two match, including `add`, `remove`, and `completion`.
4. **Given** the Active Technologies list, **When** each entry is checked, **Then** no entry describes a
   dependency's role or optionality incorrectly.
5. **Given** `AGENTS.md`, **When** it is compared with `CLAUDE.md`, **Then** it describes this
   repository — the same package name, registry format, and feature set — with no references to
   components that do not exist here.

---

### User Story 4 - Documentation drift cannot silently return (Priority: P4)

A contributor adds, renames, or deletes a source module. Today nothing notices that the orientation
documents no longer describe the tree, which is how the current drift accumulated across eight features.
After this change, the mismatch is surfaced as part of the normal change process rather than discovered
by a later audit.

**Why this priority**: Without it, this entire feature is a one-time cleanup that decays again. It
depends on Stories 1–3 having produced a correct baseline, so it goes last among the documentation work.

**Independent Test**: On a branch, add a new source module without touching the documentation, run the
project's checks, and confirm the omission is reported.

**Acceptance Scenarios**:

1. **Given** a correct structure section, **When** the project's checks run, **Then** they report no drift.
2. **Given** a newly added source module not present in the structure section, **When** the checks run,
   **Then** the drift is reported and names the specific undocumented file.
3. **Given** a structure section entry whose file has been deleted, **When** the checks run, **Then** the
   drift is reported and names the specific missing file.
4. **Given** the drift signal fires, **When** a contributor reads it, **Then** it states what to change and
   where and points at the written procedure, without requiring them to reverse-engineer the check.
5. **Given** a contributor who has never seen the check, **When** they follow the written procedure alone,
   **Then** they can add, remove, or deliberately exclude a structure entry and get a passing build.

---

### User Story 5 - Redundant and unreachable code is gone (Priority: P5)

A maintainer reading the provider layer finds no functions that nothing calls, no flags that are accepted
but do nothing, and one HTTP access pattern per provider rather than several. Today `proxmox.py` carries
an unused JSON parser retained "for symmetry", `create --yes` is accepted on all four providers purely to
print a deprecation notice, and `hetzner.py` reaches the Hetzner REST API through a shared helper in some
places and hand-rolled request blocks in others.

**Why this priority**: Pure maintainability. No user-visible behavior depends on it except the eventual
`--yes` removal, which is a deliberate, announced break.

**Independent Test**: Search the provider modules for definitions with no callers and for duplicated
request-construction blocks; confirm none remain and that the full test suite still passes.

**Acceptance Scenarios**:

1. **Given** the Proxmox provider module, **When** it is searched for functions with no callers and no
   tests, **Then** the unused JSON parser is gone and nothing else regressed.
2. **Given** the Hetzner provider module, **When** its outbound Hetzner API calls are inspected, **Then**
   they all go through a single request helper — authorization header, timeout, error mapping, and
   response decoding are defined once.
3. **Given** the consolidated Hetzner request path, **When** the existing Hetzner tests run, **Then** they
   pass unchanged in observable behavior — same error messages, same exit codes, same pagination
   completeness signalling.
4. **Given** the resolved `--yes` decision, **When** a user runs `create --yes` on any provider,
   **Then** the behavior matches the decision uniformly across all four providers, and the documentation
   states it.

---

### Edge Cases

- **A Hetzner user's first-ever command is `destroy` or `resize`.** Those playbooks call
  `hetzner.hcloud` modules with no library preflight, unlike the create role. Any dependency change must
  not leave this path without a working library or a clear, actionable failure.
- **A user installed a previous version and upgrades in place.** *Moot under FR-004a* — no dependency
  changes required/optional status, so no upgrade path can end up with a missing or orphaned package.
  Recorded because it becomes live for issue #94, which inherits it.
- **A user's environment already provides one of the affected packages** (e.g. a system-wide `httpx`, or
  a `boto3` pinned by another tool). *Moot under FR-004a* for the same reason, and independently safe
  for `httpx`: Starlette prefers `httpx2` and falls back to `httpx`, so either being present works.
  Also inherited by #94.
- **The Ansible interpreter differs from the CLI interpreter.** The playbooks assume
  `ansible_playbook_python` carries the CLI's dependencies. If a user runs with an external Ansible, that
  assumption fails; the dependency documentation must say which interpreter must have what.
- **A structure-drift signal fires on a file a contributor deliberately does not want documented** (a
  scratch module, a generated file). There must be a supported way to express that without disabling the
  check wholesale.
- **Package `__init__.py` and other boilerplate files** must not each require a documentation line, or the
  structure section becomes noise and the check becomes something contributors route around.
- **Documentation is regenerated by tooling from feature plans** (the orientation files are marked
  "auto-generated"). Corrections must survive the next regeneration rather than being overwritten.
- **A dependency is required only by an optional extra's transitive graph.** Classification must state
  which install profile needs it, not merely required-vs-optional.

## Requirements *(mandatory)*

### Functional Requirements

#### Dependency truth

- **FR-001**: Every runtime dependency declared for the package MUST be either imported somewhere in
  `src/remo_cli/` or accompanied by a declaration-site comment naming the non-Python component that
  requires it (for example, an Ansible collection) and the install profile that needs it.
- **FR-002**: The `hcloud` dependency MUST be classified according to its real consumer — the
  `hetzner.hcloud` Ansible collection running under the same interpreter as the CLI — and MUST NOT be
  removed in a way that breaks `hetzner create`, `hetzner destroy`, or `hetzner resize` on a clean
  install.
- **FR-003**: The Hetzner teardown and resize playbooks MUST NOT be left as the only Hetzner paths without
  a library-availability guarantee; whichever dependency model is chosen, all three Hetzner lifecycle
  operations MUST behave consistently on a clean install.
- **FR-004**: The `boto3` dependency MUST remain a hard runtime dependency, and its declaration site MUST
  record why: the `amazon.aws` Ansible collection requires it under `ansible_playbook_python` for AWS
  create, destroy, and resize, independently of the CLI's own lazy `import boto3`.
- **FR-004a**: This feature MUST NOT change any dependency's required-versus-optional status. Moving
  `boto3` to an `aws` extra is deferred to issue #94, together with the per-provider collection scoping
  and SDK preflights that must accompany it.
- **FR-005**: The provider-side missing-SDK guard MUST be annotated at its definition to state that it is
  currently unreachable — the SDK it guards cannot be absent under the present dependency model — and to
  name issue #94 as the change that makes it load-bearing again. The guard MUST NOT be deleted, since
  deleting and restoring it across two features is churn with no intervening benefit.
- **FR-006**: The provider descriptors' declaration of which installable extra a provider needs MUST NOT
  name an extra that does not exist in the package metadata. Where a descriptor currently names a
  non-existent extra, it MUST either declare none or carry an annotation that the named extra is
  introduced by issue #94.
- **FR-007**: The `httpx2` test dependency MUST be retained, and its declaration site MUST record that it
  is the package the installed test-client stack resolves first — not a misspelling of `httpx` — so the
  question is not re-litigated.
- **FR-008**: After the dependency changes, the full test suite MUST collect and pass in a clean
  environment created from the declared dependencies alone.

#### Documentation truth

- **FR-009**: All references to a `remo init` command MUST be removed from every documentation surface in
  the repository, including the README, the provider guides, and the installer script's post-install
  output.
- **FR-010**: Documentation MUST state that Ansible collections are installed automatically on first use
  when their requirements change, replacing the removed manual step.
- **FR-011**: The orientation documents' project-structure section MUST list exactly the source modules
  present in the working tree, with no entry for a file that does not exist.
- **FR-012**: The orientation documents' commands section MUST reference only install extras that exist in
  the package metadata.
- **FR-013**: The documented CLI surface MUST match the commands the CLI registers, including `add`,
  `remove`, and `completion`.
- **FR-014**: The Active Technologies list MUST describe each dependency's actual role and optionality,
  with no entry contradicting the package metadata.
- **FR-015**: `AGENTS.md` MUST describe this repository — correct package name, current registry format,
  and only features that exist here — or MUST be removed if it is not a maintained artifact. It MUST NOT
  be left describing a different project.
- **FR-016**: Documentation corrections MUST be durable against the next regeneration of the
  "auto-generated" orientation files; if a generator produces them, the generator or its inputs MUST be
  corrected rather than only the output.

#### Drift prevention

- **FR-017**: A mismatch between the documented project structure and the actual source tree MUST be
  detected automatically by an executable check that runs in continuous integration and fails the build,
  rather than relying on a contributor remembering to look.
- **FR-018**: The drift check MUST identify the specific files involved — undocumented files present in
  the tree, and documented files absent from it.
- **FR-019**: The drift check's failure output MUST tell a contributor what to update and where, and MUST
  point at a written procedure for doing so, without requiring them to read the checking mechanism.
- **FR-019a**: A short documented procedure for updating the structure section MUST exist alongside the
  check, covering how to add an entry, how to exclude a file deliberately, and where the check lives.
- **FR-020**: The drift check MUST support intentionally excluding boilerplate (such as package
  `__init__` files) and MUST offer a supported way to exclude a deliberately undocumented file without
  disabling the whole check.
- **FR-021**: The drift check MUST report no drift against the corrected baseline produced by this
  feature.

#### Dead code

- **FR-022**: The unused Proxmox JSON-parsing helper MUST be removed.
- **FR-023**: All Hetzner outbound API calls MUST route through a single request helper so that the
  authorization header, timeout, HTTP-error mapping, and response decoding are each defined once in the
  module.
- **FR-024**: The Hetzner consolidation MUST NOT change observable behavior: identical error messages,
  exit codes, and enumeration-completeness reporting for every existing Hetzner command.
- **FR-025**: The deprecated `create --yes` flag MUST be removed outright from all four providers in this
  feature, together with the deprecation metadata that supports it, and the removal MUST be recorded in
  the release notes as a breaking change. The flag has never had any effect in any released version, and
  the deprecation notice introduced by the unreleased feature 018 would otherwise ship and be withdrawn
  in the same release.
- **FR-026**: No change in this feature may alter the behavior of any command other than as explicitly
  specified here; the existing test suite MUST pass without behavioral test modifications, except where a
  test asserts the removed flag or a removed dependency.

### Key Entities

- **Dependency declaration**: A named package the project requires, with an install profile (always /
  test-only / feature-specific), the component that consumes it (Python source, Ansible collection, test
  harness), and a recorded rationale when the consumer is not visible in the Python source.
- **Documented structure entry**: A source path claimed by the orientation documents, paired with a
  one-line description of its role. Comparable one-to-one against the working tree.
- **Orientation document**: A repository file whose purpose is to describe the project to a newcomer or
  agent (`CLAUDE.md`, `AGENTS.md`, `README.md`, the guides under `docs/`).
- **Drift finding**: A specific discrepancy between a documented structure entry and the tree, classified
  as *documented-but-missing* or *present-but-undocumented*, carrying the path and remediation hint.
- **Deprecated CLI option**: An option accepted for backward compatibility that has no effect, carrying
  the notice shown to users and the release in which it disappears.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new user following the installation and getting-started documentation verbatim on a clean
  machine encounters zero commands that do not exist — currently one such command (`remo init`) appears
  as the second instruction, and in three further places across the docs and installer.
- **SC-002**: Zero occurrences of `remo init` remain anywhere in the repository's documentation, installer
  script, or guides.
- **SC-003**: Every path listed in the orientation documents' structure section exists, and every source
  module in the tree is listed — a documented-versus-actual diff of exactly zero entries in both
  directions, down from 2 phantom entries, 13 undocumented modules, and 2 unparseable grouped lines
  today.
- **SC-004**: Every install command shown in the orientation documents succeeds when run — currently two
  of them name extras that do not exist.
- **SC-005**: Every declared runtime dependency is traceable to a named consumer, whether Python code or a
  named Ansible collection; a reader can answer "why is this here?" for 100% of them without leaving the
  declaration site.
- **SC-005a**: A clean install of the package resolves to the same dependency set before and after this
  feature — the footprint is unchanged, and the 45% reduction quantified above is realized by issue #94,
  not here.
- **SC-006**: All three Hetzner lifecycle operations (create, destroy, resize) succeed on a clean install
  regardless of which one is run first.
- **SC-007**: The complete test suite collects and passes in an environment built from the declared
  dependencies alone, with no manual package installation.
- **SC-008**: Introducing an undocumented source module on a branch causes continuous integration to fail
  and name the file — verified by deliberately adding one. A contributor can resolve the failure using
  only the written procedure the message points to.
- **SC-009**: No provider module contains a function that is neither called nor covered by a test; the
  Hetzner module defines its API request construction exactly once.
- **SC-010**: Every user-facing behavior other than the removed `create --yes` flag is unchanged,
  evidenced by the existing test suite passing without behavioral assertions being rewritten.
- **SC-011**: `AGENTS.md` contains zero references to package paths, registry formats, or features that do
  not exist in this repository — currently it references a different package name, a superseded registry
  format, and three absent features.

## Assumptions

- **`AGENTS.md` is a maintained artifact.** It is treated as a peer of `CLAUDE.md` that must describe this
  repository, and is brought back into agreement rather than deleted. If the project considers it
  abandoned, deleting it satisfies FR-015 equally.
- **The orientation documents' "auto-generated" marker reflects an update ritual, not a live generator.**
  No script in this repository regenerates `CLAUDE.md` from feature plans; corrections are made directly
  to the files. If a generator is later introduced, FR-016 applies to it.
- **The CLI and Ansible share an interpreter in the supported install paths.** `uv tool install remo-cli`
  and `pip install remo-cli` both place `ansible-core` alongside the CLI, so `ansible_playbook_python`
  resolves to an environment carrying the CLI's declared dependencies. This is the assumption the existing
  playbook comments already encode; this feature documents it rather than changing it.
- **`hcloud` and `boto3` are the only dependencies whose necessity is invisible from the Python source.**
  `click`, `InquirerPy`, and `ansible-core` are all directly imported or invoked.
- **This feature ships no new runtime dependencies, no dependency reclassification, and no registry
  schema change.** It is a hygiene pass; any behavior change is limited to what is explicitly specified.
  Install-footprint reduction is out of scope by decision — see issue #94.
- **Provider smoke tests are the acceptance vehicle for the Hetzner and AWS clean-install scenarios.** The
  repository already runs provider smoke workflows; those are the intended proving ground for SC-006
  rather than a new manual procedure.
- **The structure section covers first-class source modules only.** Package `__init__.py` files, tests,
  and build artifacts are out of scope for the documented tree and therefore for the drift check.
- **Feature 018 is unreleased.** `v2.2.0` predates it, so the `--yes` deprecation notice it introduced has
  never appeared in a shipped release — the basis for FR-025's immediate-removal resolution.
