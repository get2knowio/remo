# Implementation Plan: Dependency, Dead-Code & Documentation Hygiene

**Branch**: `019-hygiene-deps-docs` | **Date**: 2026-07-26 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/019-hygiene-deps-docs/spec.md`

## Summary

A hygiene pass that makes three classes of claim in the repository true: what the package depends on and
why, what the orientation documents say the tree contains, and which code is actually reachable.

The dependency work is **annotation only** — no package changes required/optional status (measured
rationale in the spec; slimming split to [#94](https://github.com/get2knowio/remo/issues/94)). The
documentation work removes a phantom `remo init` command from four places including the README's second
install instruction, rebuilds the project-structure and commands sections against the real tree, and
rewrites `AGENTS.md`, which currently describes a different project. The drift work adds one pytest
module that parses the structure diagram and diffs it against `src/remo_cli/`, riding the existing CI
pytest job with no workflow change. The dead-code work removes an uncalled Proxmox helper, funnels four
divergent Hetzner HTTP call sites onto the module's existing typed request helper, and deletes the
`create --yes` flag that 018 deprecated but that has never done anything in any released version.

## Technical Context

**Language/Version**: Python 3.11+ (CI matrix 3.11/3.12/3.13); Bash for `docs/install.sh`; Markdown for
the orientation documents

**Primary Dependencies**: No change. `click`, `InquirerPy`, `boto3`, `hcloud`, `ansible-core` all remain
unconditional runtime dependencies; `httpx2` remains a dev dependency. This feature only annotates the
declarations. Stdlib `re`/`pathlib` for the new drift check. **No new runtime dependencies.**

**Storage**: N/A — no registry schema change, no new state files

**Testing**: pytest (existing `tests/unit`, `tests/integration`); the new drift check is a pytest module
under `tests/unit/`, so it runs in CI through the existing `test` job on all three Python versions

**Target Platform**: Linux/macOS developer workstations (CLI); the drift check runs anywhere pytest does

**Project Type**: Single Python CLI package (`src` layout, hatchling) with an optional FastAPI web
service and an Ansible automation layer

**Performance Goals**: `remo --help` and shell completion must still import zero optional provider SDKs
(018 SC-008 — preserved, not extended). The drift check must add negligible time to the suite (single
pass over ~65 files).

**Constraints**: Zero behavior change except the `create --yes` removal (FR-026). Hetzner HTTP
consolidation must preserve per-call-site error semantics exactly — the four sites currently differ in
raise-vs-swallow, timeout, and message text (research R3). Documentation fixes must survive
`update-agent-context.sh`, which is a live generator for two of the sections (research R5).

**Scale/Scope**: ~65 source modules under `src/remo_cli/`; 4 documentation surfaces (`CLAUDE.md`,
`AGENTS.md`, `README.md`, `docs/`); 1 installer script; 4 provider descriptors; ~10 files touched in
`src/`, ~6 in docs, 2 new test modules.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment | Status |
|---|---|---|
| **I. Defensive Variable Access (Ansible)** | This feature adds no Ansible tasks and registers no new variables. It touches no `when:`/`register:` logic. The one Ansible-adjacent finding (missing `hcloud` preflight in `hetzner_resize.yml`/`hetzner_teardown.yml`) is documented as a known gap and deferred to #94 along with the dependency change that would make it load-bearing — see Complexity Tracking. Pre-commit grep for `.rc ==` / `.stdout` without `\| default()` still applies and finds nothing new. | **PASS** |
| **II. Test All Conditional Paths** | The new drift check has exactly three outcomes (clean / phantom entry / undocumented file) plus a stale-exclusion guard; all four are covered in `tests/unit/test_docs_structure.py` (research R4). The Hetzner consolidation's raise-vs-swallow branches are the main risk and get explicit per-call-site tests — currently those four sites bypass the mocked `_hetzner_api` and are untested at the HTTP layer, so coverage strictly increases. | **PASS** |
| **III. Idempotent by Default** | The drift check is a pure read-only comparison, re-runnable with identical results. No playbook changes. Documentation edits are ordinary file edits. | **PASS** |
| **IV. Fail Fast with Clear Messages** | FR-018/FR-019 are this principle applied to the drift check: the failure must name the specific files and point at the written procedure, not merely assert False. The typed-error taxonomy from 018 is preserved unchanged by the Hetzner consolidation (`PreconditionError` for a missing token, `OperationFailedError` for transport/HTTP failures). | **PASS** |
| **V. Documentation Reflects Reality** | This principle *is* the feature. FR-009 through FR-021 implement it, and FR-017 adds the first executable enforcement the project has had — the principle has existed since ratification (2026-01-06) and the structure section still drifted across features 010–018, which is the evidence that a prose rule alone is insufficient. | **PASS — and materially strengthened** |

**Gate result: PASS.** Two deferrals are recorded in Complexity Tracking; both remove scope rather than
adding it, and both are traceable to a filed issue.

### Post-Design Re-Check (after Phase 1)

Re-evaluated against the generated artifacts. **Result: PASS, no new violations.**

| Principle | Post-design finding |
|---|---|
| **I. Defensive Variable Access** | Confirmed unchanged: the design touches no `.yml` file. The deferral of the SDK preflights (Complexity Tracking) is what *keeps* it that way — adding always-skipped guarded tasks would have introduced exactly the `when:`/`register:` surface this principle governs. |
| **II. Test All Conditional Paths** | Strengthened during design. `contracts/docs-structure-check.md` §5 enumerates nine test cases covering all four assertions (A-1…A-4) *and* the three format-error branches — including the two negative cases (T-8 skip, T-9 ignore) that a naive design would have left untested. Research R3 revealed the Hetzner sites have three distinct error contracts, so `test_hetzner_http.py` now pins raise-vs-swallow per site; these paths have **no** HTTP-layer coverage today. |
| **III. Idempotent by Default** | Confirmed: the check is a pure function of (document text, filesystem). Quickstart step 3 exercises re-runnability explicitly — fail, remove the probe file, pass again. |
| **IV. Fail Fast with Clear Messages** | Design elevates this from intent to contract: `docs-structure-check.md` §4 fixes the required failure shape with six numbered message requirements (M-1…M-6), forbidding a bare `assert False`. The 018 error taxonomy is preserved verbatim through the Hetzner consolidation — `PreconditionError` for a missing token, `OperationFailedError` for transport/HTTP. |
| **V. Documentation Reflects Reality** | The design's central risk surfaced in research R5 and is now handled: `update-agent-context.sh` is a **live generator** for two of the sections it was assumed to only advise on. `plan.md`'s Primary Dependencies line is therefore itself an input to `CLAUDE.md`, written accurately, and quickstart step 11 verifies the round-trip does not reintroduce the stale `"hcloud (Hetzner, optional)"` entry. Had this gone unnoticed, the feature would have re-drifted on the next `/speckit-plan`. |

One design decision warrants review attention: `contracts/docs-structure-check.md` mandates **one path
per line** in the structure diagram, which forces a cosmetic edit to two existing `CLAUDE.md` lines that
currently group four files each. The alternative (splitting on `" / "`) was rejected as ambiguous with
the directory separator the parser depends on. This is a format constraint imposed by the enforcement
mechanism — small, but it is the check shaping the documentation rather than the reverse.

## Project Structure

### Documentation (this feature)

```text
specs/019-hygiene-deps-docs/
├── plan.md                              # This file (/speckit-plan command output)
├── spec.md                              # Feature specification
├── research.md                          # Phase 0 output
├── data-model.md                        # Phase 1 output
├── quickstart.md                        # Phase 1 output
├── contracts/
│   ├── docs-structure-check.md          # Parsing rules, exclusion policy, failure-output format
│   └── cli-surface-delta.md             # The one intentional CLI break (create --yes)
├── checklists/
│   └── requirements.md                  # Spec quality checklist + decision log
└── tasks.md                             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
pyproject.toml                           # MODIFIED: per-dependency rationale comments (FR-001/002/004/007)

src/remo_cli/
├── core/
│   └── provider_registry.py             # MODIFIED: annotate sdk_extra as #94-pending (FR-006);
│                                        #   delete DeprecatedOption + the create-only YES plumbing (FR-025)
├── cli/providers/
│   └── factory.py                       # MODIFIED: drop the create --yes param + notice block (FR-025)
└── providers/
    ├── aws.py                           # MODIFIED: annotate both missing-boto3 paths (FR-005)
    ├── hetzner.py                       # MODIFIED: route _query_hetzner_server_ip + info()'s two
    │                                    #   inline urllib blocks through _hetzner_api (FR-023/024)
    ├── proxmox.py                       # MODIFIED: delete _parse_pct_json (FR-022)
    └── *_descriptor.py                  # MODIFIED (x4): drop deprecated_options (FR-025)

tests/unit/
├── test_docs_structure.py               # NEW: the drift check (FR-017/018/019/020/021)
├── cli/surface_baseline.py              # MODIFIED: drop "--yes"/"-y" from all four create entries
└── providers/test_hetzner_http.py       # NEW: per-call-site error-semantics tests (FR-024)

CLAUDE.md                                # MODIFIED: structure, commands, Active Technologies (FR-011..014)
AGENTS.md                                # REWRITTEN: currently describes a different project (FR-015)
README.md                                # MODIFIED: remove remo init (FR-009/010)
docs/aws.md                              # MODIFIED: remove remo init
docs/install.sh                          # MODIFIED: remove remo init from post-install hints
docs/maintaining-claude-md.md            # NEW: the structure-update procedure (FR-019a)
```

**Structure Decision**: Single Python CLI package, unchanged. This feature adds no modules to
`src/remo_cli/` and creates no new package. The only new code is two pytest modules under `tests/unit/`,
which is where 018 already put its architecture gates (`tests/unit/test_architecture.py`) — the drift
check follows that file's established allowlist pattern rather than introducing a new mechanism. No CI
workflow file changes: `.github/workflows/ci.yml` already runs `uv run pytest` across the Python matrix,
so a test module is automatically a CI gate.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| FR-005 keeps a knowingly-unreachable code branch (`_require_boto3`'s `ImportError` handler) in a feature whose theme is removing dead code | `boto3` cannot be absent today, but issue #94 makes it optional again within a release or two. Deleting the guard now and restoring it then is churn across two features with no benefit in the interval, and the restored version would have to re-derive the message text and exit-code mapping. | Deleting it outright was the first draft. Rejected because it trades a documented, annotated dead branch for a future re-implementation — and because `aws.py:88-95` has a *second*, differently-behaved missing-boto3 path (silent return, mirroring legacy bash) that would also need reconstructing. Annotating both, with the issue number, preserves the information at lower cost. |
| The SDK preflight gap in `hetzner_resize.yml`, `hetzner_teardown.yml`, and all AWS playbooks is documented but not fixed | The gap is only *reachable* if the SDK can be absent, which is exactly what #94 introduces. Fixing it here would add Ansible tasks guarding a condition that cannot occur under this feature's dependency model — the same anti-pattern FR-005 is about. | Adding the preflights now was considered for symmetry with `roles/hetzner_server/tasks/main.yml`. Rejected: it would add always-skipped tasks whose `when:` false-branch cannot be meaningfully tested (Principle II), and it is already required scope in #94, so it would be written twice. |
