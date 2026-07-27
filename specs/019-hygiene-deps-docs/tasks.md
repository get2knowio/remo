---

description: "Task list for 019-hygiene-deps-docs"
---

# Tasks: Dependency, Dead-Code & Documentation Hygiene

**Input**: Design documents from `/specs/019-hygiene-deps-docs/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Test tasks are included because the feature specification *requires* them as deliverables —
FR-017 through FR-021 define an executable drift check, and FR-024 requires proof that the Hetzner
consolidation changed no observable behavior. These are not TDD-by-default; they are the product.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US5)
- Exact file paths included in every task

## Path Conventions

Single Python CLI package: `src/remo_cli/`, `tests/` at repository root. Documentation at the root
(`CLAUDE.md`, `AGENTS.md`, `README.md`) and under `docs/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish an attributable baseline before anything changes.

- [X] T001 Run `uv sync --all-extras`, then record the current green baseline — `uv run pytest --tb=short -q` (expect ~1746 passing), `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli` — to a scratch path **outside the repository** (e.g. `/tmp/019-baseline.txt`) so any later regression is attributable, without committing a transient artifact. Carry the numbers into the PR description
- [X] T002 [P] Confirm the working tree is on branch `019-hygiene-deps-docs` and clean apart from `specs/019-hygiene-deps-docs/`, so documentation edits in later phases are reviewable in isolation

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: One shared correction that three later stories depend on.

**⚠️ CRITICAL**: T003 blocks US3 and US4 — the drift check cannot parse the structure block until the
grouped lines are split.

- [X] T003 In `CLAUDE.md`, split the two grouped structure-diagram lines into one path per line: `providers/incus.py / hetzner.py / aws.py / proxmox.py` becomes four lines, and the `*_descriptor.py` line likewise (format error **F-1** in `contracts/docs-structure-check.md` §2). **Side effect to know about**: this documents 8 of the 13 currently-undocumented modules, so T019 only has 5 left to add — do not re-add these eight

**Checkpoint**: The structure block is machine-parseable. User stories can begin.

---

## Phase 3: User Story 1 - A new user's documented install path works (Priority: P1) 🎯 MVP

**Goal**: Remove the phantom `remo init` command from every documentation surface and replace it with an
accurate statement that Ansible collections install automatically.

**Independent Test**: On a clean machine, follow README Installation → Quick Start literally; no command
fails with a usage error. `grep -rn "remo init"` over docs returns nothing.

### Implementation for User Story 1

- [X] T004 [P] [US1] In `README.md:15`, remove the `remo init` line and the `# Initialize (installs Ansible collections)` comment from the Installation code block; the install block ends at the `pip install remo-cli` line
- [X] T005 [P] [US1] In `README.md:278`, remove the `remo init   # Install Ansible collections` entry from the command reference table/list
- [X] T006 [P] [US1] In `README.md:454`, replace the `remo init  # Reinstalls dependencies` troubleshooting advice with guidance that does not reference a nonexistent command (collections reinstall automatically when `ansible/requirements.yml` changes; the manual escape hatch is deleting the `collections.lock` marker in `REMO_HOME`)
- [X] T007 [P] [US1] In `docs/aws.md:387`, remove or rewrite "Run `remo init` to install Python dependencies." — Python dependencies come from the package install, and collections are automatic
- [X] T008 [P] [US1] In `docs/install.sh:188`, remove the `remo init            # Set up SSH keys, Ansible, etc.` line from the post-install "Get started:" hints, leaving `remo --version` and `remo --help`
- [X] T009 [US1] Add a short note to `README.md`'s Installation section stating that Ansible collections install automatically on first provider command (FR-010), matching the behavior in `src/remo_cli/core/ansible_runner.py::_ensure_collections` (SHA-256 marker over `ansible/requirements.yml`, stored in `REMO_HOME`)
- [X] T010 [US1] Verify closure two ways (SC-001, SC-002): (a) `grep -rn "remo init" --include="*.md" --include="*.sh" . | grep -v "^./specs/" | grep -v node_modules` returns no output; (b) walk the `README.md` Installation and Quick Start sections top to bottom in a scratch environment, running each command shown, and confirm none exits with a usage error — the grep alone cannot catch a *different* nonexistent command

**Checkpoint**: A new user can follow the README verbatim without hitting a nonexistent command.

---

## Phase 4: User Story 2 - Dependency declarations state what is required, and why (Priority: P2)

**Goal**: Annotate every dependency whose necessity is invisible from the Python source, and annotate the
two code paths that guard states which cannot currently occur.

**Independent Test**: Read `pyproject.toml` top to bottom; every runtime dependency is either imported in
`src/remo_cli/` or carries a comment naming its non-Python consumer. Dependency set is byte-identical
before and after.

**⚠️ Scope guard**: This story changes **no** package's required-versus-optional status (FR-004a). If a
task tempts you to move `boto3` into an extra, stop — that is issue #94.

### Implementation for User Story 2

- [X] T011 [P] [US2] In `pyproject.toml`, add a comment above `hcloud` in `dependencies` naming its real consumer: the `hetzner.hcloud` Ansible collection (pinned `>=6.7.0` in `ansible/requirements.yml`), whose modules `import hcloud` under `ansible_playbook_python` — the interpreter `ansible/hetzner_site.yml:10` and `ansible/hetzner_teardown.yml:14` deliberately pin to the CLI's own environment. Note it is imported nowhere in `src/remo_cli/` and link issue #94
- [X] T012 [P] [US2] In `pyproject.toml`, add a comment above `boto3` recording that it serves two consumers — the CLI's own lazy `import boto3` in `providers/aws.py` **and** the `amazon.aws`/`community.aws` collections under `ansible_playbook_python` — and that it stays unconditional per FR-004 pending issue #94
- [X] T013 [P] [US2] In `pyproject.toml`, extend the existing `httpx2` comment in the `dev` extra to state that it is **not** a misspelling of `httpx`: it is the pydantic package (`github.com/pydantic/httpx2`) that Starlette 1.3.1's `testclient` resolves first via `import httpx2 as httpx`, falling back to `httpx` (FR-007, research R2)
- [X] T014 [P] [US2] In `src/remo_cli/providers/aws.py:180-190`, annotate `_require_boto3` as currently unreachable — `boto3` is an unconditional dependency, so the `ImportError` branch cannot fire — and name issue #94 as what re-arms it. Do **not** delete it (FR-005, plan Complexity Tracking)
- [X] T015 [P] [US2] In `src/remo_cli/providers/aws.py:88-95`, annotate the *second*, differently-behaved missing-boto3 path (silent return, mirroring legacy bash) with the same note, so a future reader of #94 finds both sites
- [X] T016 [US2] In `src/remo_cli/core/provider_registry.py:222-236`, annotate the `sdk_extra` `MissingDependencyError` message as naming extras (`aws`, `hetzner`) that do not currently exist in `pyproject.toml`, introduced by issue #94 (FR-006). Leave `descriptor.sdk_extra` values in place
- [X] T017 [US2] Verify no reclassification occurred: `uv run python -c "import importlib.metadata as md; print(sorted(r for r in md.requires('remo-cli') if 'extra ==' not in r))"` returns exactly `['ansible-core<2.20.0,>=2.18.0', 'boto3', 'click>=8.1', 'hcloud', 'inquirerpy>=0.3.4']` (SC-005a), and `uv sync --extra aws --dry-run` still fails with "Extra `aws` is not defined"

**Checkpoint**: Every dependency answers "why is this here?" at its declaration site; footprint unchanged.

---

## Phase 5: User Story 3 - Repository documentation matches the repository (Priority: P3)

**Goal**: Rebuild the structure, commands, and Active Technologies sections against the real tree, and
rewrite `AGENTS.md`, which currently describes a different project.

**Independent Test**: Every path, command, and extra named in `CLAUDE.md`/`AGENTS.md` resolves to
something real; `diff` of the two files' structure sections is empty.

**Depends on**: T003 (grouped lines split).

### Implementation for User Story 3

- [X] T018 [US3] In `CLAUDE.md`'s `## Project Structure` block (line 24 onward), delete the two phantom entries `cli/init_cmd.py` and `core/init.py` — neither file exists (FR-011)
- [X] T019 [US3] In the same block, add the **five** modules that remain undocumented after T003, each with a one-line description: `src/remo_cli/cli/added.py`, `src/remo_cli/providers/added.py`, `src/remo_cli/web/operator_auth.py`, `src/remo_cli/web/pairing.py`, `src/remo_cli/web/api/pairing.py`. The other 8 of the 13 (the four provider implementations and their four descriptors) are already documented by T003's line split — adding them again would create duplicate entries, which the check rejects as format error **F-2**
- [X] T020 [US3] In `CLAUDE.md`'s `## Commands` section (lines 157-159), delete the `uv sync --extra aws` and `uv sync --extra hetzner` lines — neither extra exists; only `dev` and `web` do (FR-012)
- [X] T021 [US3] In `CLAUDE.md`'s `## Commands` section, add the CLI commands it omits — `remo add`, `remo remove`, `remo completion {bash,zsh,fish}` — matching what `src/remo_cli/cli/main.py:_register_commands` actually registers (FR-013)
- [X] T022 [US3] In `CLAUDE.md:12`'s `## Active Technologies` list, correct the entry `"boto3 (AWS, optional), hcloud (Hetzner, optional)"` — both are unconditional, and `hcloud` is consumed by the Ansible layer, not the CLI (FR-014). Also correct `CLAUDE.md:191`'s "Provider SDKs (boto3, hcloud) are lazy-imported with clear error messages if missing"
- [X] T023 [US3] Rewrite `AGENTS.md` to describe this repository: replace `src/remo/` with `src/remo_cli/` throughout, replace the flat-file `known_hosts` registry description with registry v2, and delete the three "notifier sidecar" Active Technologies entries (007/008/009) and their dependencies (`python-telegram-bot`, `structlog`, `tomli`, `httpx`) — none of which exist here (FR-015, research R5)
- [X] T024 [US3] Bring `AGENTS.md`'s `## Project Structure`, `## Commands`, and `## Code Style` sections into agreement with the corrected `CLAUDE.md` — these three are hand-maintained; `update-agent-context.sh` manages only Active Technologies and Recent Changes
- [X] T025 [US3] Verify: `grep -n "src/remo/\|known_hosts\|notifier\|telegram\|structlog\|tomli" AGENTS.md` returns nothing, and `diff <(sed -n '/## Project Structure/,/^## /p' CLAUDE.md) <(sed -n '/## Project Structure/,/^## /p' AGENTS.md)` is empty (SC-011)

**Checkpoint**: Both orientation documents describe the repository that exists.

---

## Phase 6: User Story 4 - Documentation drift cannot silently return (Priority: P4)

**Goal**: An executable CI-gating check that fails the build naming the specific drifted files, plus a
written procedure a first-time contributor can follow.

**Independent Test**: Add a throwaway module to `src/remo_cli/`; the suite fails naming it; remove it;
the suite passes.

**Depends on**: T003 (parseable format) and US3 (a correct baseline — T027's real-repository case cannot
pass until US3 lands). Tasks T026 and T028–T032 may be written in parallel with US3.

### Tests for User Story 4

> These *are* the deliverable — the check is a test module. Normative rules in
> `contracts/docs-structure-check.md`.

- [X] T026 [US4] Create `tests/unit/test_docs_structure.py` with the parser implementing rules R-P1…R-P7 from `contracts/docs-structure-check.md` §2: depth from `len(prefix) // 4`, parent stack, description stripped at the first `#`, collecting only paths under `src/remo_cli/` ending in `.py`
- [X] T027 [US4] Add assertion A-1 (`D − A = ∅`, phantom) and A-2 (`A − D − X = ∅`, undocumented), parameterized over `CLAUDE.md` and `AGENTS.md`, skipping a document that has no `## Project Structure` heading (test case T-8)
- [X] T028 [US4] Declare `EXCLUDED_FROM_DOCS` in the same module containing exactly the seven package-marker `__init__.py` files (`cli/`, `cli/providers/`, `core/`, `models/`, `providers/`, `web/`, `web/api/`) with a reason comment each — **not** `src/remo_cli/__init__.py`, which is meaningfully documented and stays in the diagram
- [X] T029 [US4] Add anti-rot assertions A-3 (`X ⊆ A`, stale exclusion) and A-4 (`X ∩ D = ∅`, both excluded and documented), following the allowlist pattern in `tests/unit/test_architecture.py`
- [X] T030 [US4] In `tests/unit/test_docs_structure.py`, add format-error detection F-1 (a `" / "` grouped line), F-2 (duplicate reconstructed path), and F-3 (heading present, fenced block absent), reported distinctly from drift findings because they cause silent under-reporting
- [X] T031 [P] [US4] Add synthetic-document test cases T-2 through T-7 from `contracts/docs-structure-check.md` §5, operating on in-memory strings — not by mutating tracked files — so the suite stays hermetic and parallel-safe
- [X] T032 [P] [US4] In `tests/unit/test_docs_structure.py`, add test case T-9 asserting entries under `frontend/`, `docker/`, and `ansible/` are parsed and ignored, never reported (scope boundary)
- [X] T033 [US4] Implement the failure message to satisfy M-1…M-6 in `contracts/docs-structure-check.md` §4: names the document and section, lists every path grouped by kind with counts, gives document line numbers for phantom findings, states both remediation directions and the exclusion escape hatch, and points at `docs/maintaining-claude-md.md`

### Implementation for User Story 4

- [X] T034 [US4] Write `docs/maintaining-claude-md.md`: how to add a structure entry (one path per line, with the indentation convention), how to remove one, how to exclude a file deliberately via `EXCLUDED_FROM_DOCS`, and where the check lives — readable standalone, without opening the test module (FR-019a)
- [X] T035 [US4] Confirm no CI workflow change is needed: `.github/workflows/ci.yml:29` already runs `uv run pytest` across Python 3.11/3.12/3.13, so the new module gates the build automatically. Do not add a job
- [X] T036 [US4] Perform the SC-008 manual acceptance: `printf 'PLACEHOLDER = True\n' > src/remo_cli/core/_drift_probe.py`, run `uv run pytest tests/unit/test_docs_structure.py -q` and confirm the failure names `_drift_probe.py` and points at the procedure doc, then delete the probe and confirm the suite passes again

**Checkpoint**: Drift is a build failure, and the failure teaches the fix.

---

## Phase 7: User Story 5 - Redundant and unreachable code is gone (Priority: P5)

**Goal**: Delete the uncalled Proxmox helper, remove the `create --yes` flag, and funnel four divergent
Hetzner HTTP call sites onto the module's existing typed request helper without changing behavior.

**Independent Test**: `hetzner.py` contains exactly one `urllib.request.Request`; `_parse_pct_json` has no
hits; `create --yes` exits 2; every pre-existing Hetzner test passes unmodified.

### Dead code removal

- [X] T037 [P] [US5] Delete `_parse_pct_json` from `src/remo_cli/providers/proxmox.py:856-865` along with its "kept around for symmetry" comment block at `:850-853` (FR-022) — the symmetry argument never applied; `providers/incus.py`'s analogous parser *is* called
- [X] T038 [US5] Delete the now-orphaned `import json` at `src/remo_cli/providers/proxmox.py:15` (depends on T037). Without this `uv run ruff check src/remo_cli` fails F401, and lint is a required CI job (research R7)

### `create --yes` removal

> Full change list and verification matrix in `contracts/cli-surface-delta.md`.

- [X] T039 [US5] In `src/remo_cli/cli/providers/factory.py:161`, delete `params.append(_click_option(YES, descriptor))` from `_build_create`
- [X] T040 [US5] In `src/remo_cli/cli/providers/factory.py:164-171`, delete the `used_yes = kwargs.pop("auto_confirm", False)` block and the `descriptor.deprecated_options` notice loop from `_build_create.run`, keeping the `rc` / `emit_out_of_date_notice()` logic verbatim
- [X] T041 [P] [US5] Delete `deprecated_options=(CREATE_YES_DEPRECATION,)` and the `CREATE_YES_DEPRECATION` import from all four descriptors: `incus_descriptor.py:14,95`, `hetzner_descriptor.py:11,50`, `aws_descriptor.py:18,99`, `proxmox_descriptor.py:16,118`
- [X] T042 [US5] In `src/remo_cli/core/provider_registry.py`, delete the `CREATE_YES_DEPRECATION` constant at `:322`, the `deprecated_options` field on `ProviderDescriptor` at `:159`, and the `DeprecatedOption` dataclass at `:106-112` (depends on T041)
- [X] T043 [US5] **Do not remove the shared `YES` `OptionSpec`** at `src/remo_cli/core/provider_registry.py:306` — verify it is still referenced by `factory.py:227` (destroy), `:308` (sync), `:326`, and `:413` (snapshot restore/delete), and that `remo remove --help` still shows `--yes`
- [X] T044 [US5] In `tests/unit/cli/surface_baseline.py`, remove `"--yes"` and `"-y"` from the `create` list of all four providers, and note in the module docstring that the create entries diverge from the 2026-07-26 capture by this one deliberate removal (the sole FR-026 carve-out)

### Hetzner HTTP consolidation

> **⚠️ Highest-risk group in the feature.** Research R3 established that the four call sites have three
> *different* error contracts. Read `research.md` R3 before starting.

- [X] T045 [US5] Create `tests/unit/providers/test_hetzner_http.py` covering the **current** behavior of all four sites before touching them — `_query_hetzner_server_ip` returns `""` on missing token and on transport error; `info()` raises `PreconditionError("HETZNER_API_TOKEN is not set.")` and `PreconditionError("No Hetzner server found with name '<n>'.")`; `info()`'s volume lookup swallows failures. These tests must pass against unmodified code first, then still pass after T046–T048 — that is the FR-024 evidence
- [X] T046 [US5] Rewrite `_query_hetzner_server_ip` (`src/remo_cli/providers/hetzner.py:57-86`) to call `_hetzner_api("GET", f"/servers?{urlencode({'name': name})}", timeout=15)` inside `try/except ProviderError: return ""`, preserving the silent-`""`-on-any-failure contract **and** the 15s timeout (the helper defaults to 30s)
- [X] T047 [US5] Rewrite `info()`'s server lookup (`src/remo_cli/providers/hetzner.py:296-318`) to use `_hetzner_api(..., timeout=15)`. **Keep `info()`'s own token check and its own message strings** — `"HETZNER_API_TOKEN is not set."` (`:301`) differs from the helper's `"HETZNER_API_TOKEN is not set; cannot reach the Hetzner Cloud API."` (`:472`), and `"No Hetzner server found with name '<n>'."` (`:318`) differs from `_get_server_by_name`'s `"No Hetzner server found named '<n>'."` (`:512`). Do **not** substitute `_get_server_by_name`
- [X] T048 [US5] Rewrite `info()`'s volume lookup (`src/remo_cli/providers/hetzner.py:328-341`) to use `_hetzner_api(..., timeout=15)` inside `try/except ProviderError: pass`, preserving the best-effort contract that leaves `volume_size` empty rather than failing the whole `info` call
- [X] T049 [US5] In `src/remo_cli/providers/hetzner.py`'s `info()`, preserve the transport-error text. Its current message is `f"Hetzner API request failed: {e}"`, whereas `_hetzner_api` raises `f"Hetzner API {method} {path} failed: ..."`. Per plan recommendation, catch and re-raise with the original wording — FR-024 says "identical error messages" without carve-out, and no test asserts either string today. Pin the chosen string in `tests/unit/providers/test_hetzner_http.py`
- [X] T050 [US5] Verify consolidation: `grep -c "urllib.request.Request" src/remo_cli/providers/hetzner.py` returns exactly `1` (inside `_hetzner_api`), down from 4, and `uv run pytest tests/unit/providers/ tests/integration/test_sync_reconcile.py -q` passes with **no** pre-existing Hetzner test modified
- [X] T058 [US5] Verify SC-009's *global* claim, not just the two known sites: run an AST scan over `src/remo_cli/providers/*.py` listing every top-level function whose name appears nowhere in `src/` or `tests/` beyond its own `def`. Expect **zero** results after T037. (Baseline confirmed during analysis: exactly one such function exists today — `_parse_pct_json`. The `snapshot_*_legacy` functions are all called by their entry-based wrappers and are **not** dead code.) Record the command in the PR description so the claim is reproducible

**Checkpoint**: One request constructor, no uncalled functions, one deliberate CLI break.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T051 Run the full gate: `uv run pytest --tb=short -q`, `uv run ruff check src/remo_cli`, `uv run mypy src/remo_cli`. Compare against `baseline.txt` from T001 — test count should rise by the new cases and fall by none
- [X] T052 [P] Verify FR-016 durability: back up `CLAUDE.md`, run `.specify/scripts/bash/update-agent-context.sh claude`, and confirm the only diff is an appended Active Technologies line plus a Recent Changes entry — and that the appended dependency line does **not** reintroduce "boto3 (AWS, optional), hcloud (Hetzner, optional)". Structure and Commands sections must be untouched
- [X] T053 [P] Re-run the drift check after all documentation edits to confirm the corrected baseline reports zero findings in both `CLAUDE.md` and `AGENTS.md` (FR-021, SC-003)
- [X] T054 [P] Add the `create --yes` removal to the commit body as a `BREAKING CHANGE:` trailer so release-please surfaces it in the changelog, using the wording in `contracts/cli-surface-delta.md` §6
- [X] T055 Walk `specs/019-hygiene-deps-docs/quickstart.md` steps 1–11 end to end
- [ ] T059 Close SC-006 / FR-003, the feature's only success criterion with no other task: run quickstart step 12 — build a clean venv (`uv venv /tmp/v-clean && VIRTUAL_ENV=/tmp/v-clean uv pip install .`), confirm `import hcloud, boto3` both succeed there, then trigger the repository's Hetzner smoke workflow exercising `create`, `destroy`, and `resize`. Confirm order-independence explicitly: `destroy` and `resize` must work when run **first**, since neither playbook has the `hcloud` preflight that `roles/hetzner_server/tasks/main.yml` has (gap deferred to #94). Needs live Hetzner credentials — if unavailable, mark the criterion explicitly unverified in the PR rather than silently skipping it
  - **Partially verified as of PR #95.** The `hetzner` job in `.github/workflows/smoke-test.yml` ran against live credentials and passed: `create` → SSH → `info` → `snapshot create/list/delete` → `destroy --remove-volume`, against a plain wheel install. That establishes the `destroy` half — `ansible/hetzner_teardown.yml` has no `hcloud` preflight, so teardown succeeding proves the unconditional dependency is what carries it.
  - **Still open, tracked in #96:** (a) `resize` has no smoke coverage at all (`grep -rn resize .github/workflows/` returns nothing); (b) order-independence is not actually exercised, because the smoke job always runs `create` first — and the create path's pip fallback in `roles/hetzner_server/tasks/main.yml` can mask a missing `hcloud` for every later step. Closing this task requires both, per #96's checklist.
- [X] T056 Confirm the two documented deferrals are visible to a future reader: issue #94 is referenced from `pyproject.toml`, `providers/aws.py` (both sites), and `core/provider_registry.py`
- [X] T057 Update `CLAUDE.md`'s `## Recent Changes` with a 019 entry summarizing: dependency annotations (no reclassification), `remo init` removal, structure/commands/AGENTS.md correction, the new drift check, and the `create --yes` breaking change

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: T003 blocks US3 and US4
- **US1 (Phase 3)**: independent — can start immediately after Setup
- **US2 (Phase 4)**: independent — can start immediately after Setup
- **US3 (Phase 5)**: needs T003
- **US4 (Phase 6)**: needs T003; T027's real-repository case needs US3 complete
- **US5 (Phase 7)**: independent — can start immediately after Setup
- **Polish (Phase 8)**: needs all desired stories. T059 (SC-006) needs live Hetzner credentials and may lag the rest — it is the one criterion that cannot be closed from a workstation alone. It outlived the feature: partially verified by PR #95's smoke run, remainder tracked in #96

### The one cross-story dependency

US4's check will **fail** until US3 has corrected the baseline. This is inherent, not a design flaw —
the check's job is to report the drift US3 removes. Write the check (T026–T034) in parallel with US3;
expect red until T025 lands. The spec anticipated this: US4 "depends on Stories 1–3 having produced a
correct baseline."

### Within User Story 5

Three independent sub-groups, safe to run in parallel by different people:

- Proxmox: T037 → T038 (strictly sequential — the import removal depends on the function removal)
- `--yes`: T039 → T040, T041 → T042, then T043, T044
- Hetzner: **T045 first** (characterize current behavior), then T046, T047, T048 in parallel, then T049, T050
- T058 runs last in this story — it verifies SC-009 globally and depends on T037

### Parallel Opportunities

- T004–T008 — five different documentation files, fully parallel
- T011–T015 — `pyproject.toml` comments and `aws.py` annotations (T011–T013 touch the same file; serialize those three or apply as one edit)
- T031, T032 — independent synthetic test cases
- T037, T041 — different modules
- The three US5 sub-groups against each other

---

## Parallel Example: User Story 1

```bash
# All five remo-init removals touch different files:
Task: "Remove remo init from README.md:15 installation block"
Task: "Remove remo init from README.md:278 command reference"
Task: "Rewrite README.md:454 troubleshooting advice"
Task: "Remove remo init from docs/aws.md:387"
Task: "Remove remo init from docs/install.sh:188 post-install hints"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational (T003)
2. Phase 3: US1 — five parallel file edits plus verification
3. **STOP and VALIDATE**: `grep -rn "remo init"` over docs returns nothing; a clean-machine README walkthrough hits no usage error
4. Shippable on its own — it fixes the second instruction a new user reads

### Incremental Delivery

1. Setup + T003 → parseable baseline
2. **US1** → new users unblocked → ship (MVP)
3. **US2** → dependency rationale recorded → ship
4. **US5** → dead code gone, one deliberate CLI break → ship *(independent of US3/US4; can precede them)*
5. **US3** → documentation matches the tree → ship
6. **US4** → drift becomes a build failure → ship *(last, because it enforces US3's result)*

### Recommended sequencing note

US5 carries the only behavioral risk in the feature and the only breaking change. Consider landing it as
its own PR with the `BREAKING CHANGE:` trailer, separate from the documentation work — reviewers of a
docs-only diff should not have to reason about Hetzner error semantics.

---

## Notes

- `[P]` = different files, no dependencies
- The riskiest tasks are T046–T049; research R3 documents exactly why the four Hetzner sites are not
  interchangeable (raise-vs-swallow, 15s vs 30s, and two message strings that differ from the canonical
  helpers by a word)
- FR-005 deliberately **keeps** an unreachable branch (T014/T015 annotate rather than delete). If a
  reviewer flags it as dead code, point at plan Complexity Tracking and issue #94
- Do not add a CI job (T035). The existing pytest job already gates the new check across three Python
  versions
