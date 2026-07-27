<!--
  Sync Impact Report
  ===================
  Version change: 1.0.0 → 2.0.0 (MAJOR)

  Bump rationale: the constitution was written when remo was an Ansible-only
  project. It is now a four-layer system (Python CLI, FastAPI service,
  TypeScript SPA, Ansible roles). Every original principle survives, but four
  are renamed/redefined and three new NON-NEGOTIABLE principles apply
  retroactively to existing code. Redefinition + newly-binding gates = MAJOR.

  Modified principles:
  - I. Defensive Variable Access (Ansible) → V. Defensive Variable Access (Ansible)
    (unchanged in substance; renumbered, no longer the lead principle)
  - II. Test All Conditional Paths → VI. Test Every Path That Can Skip or Fail
    (expanded from Ansible-only to all four layers)
  - III. Idempotent by Default → VII. Idempotent and Re-runnable by Default
    (expanded to cover registry mutation and web push/sync)
  - IV. Fail Fast with Clear Messages → merged into III. Typed Errors, One Exit
    Boundary, Actionable Messages (now also binds the Python error taxonomy)
  - V. Documentation Reflects Reality → VIII. Documentation Reflects Reality,
    and CI Proves It (now machine-checked, not aspirational)

  Added principles:
  - I. Layered Architecture with One-Way Dependencies
  - II. Providers Are Declared, Not Special-Cased
  - IV. Contracts Are Generated, Never Hand-Authored

  Added sections:
  - Technology Standards (Python/CLI, Web Service, Frontend, Ansible)
    — replaces the narrower "Ansible-Specific Standards"
  - Quality Gates (enumerates the machine-checked gates and their commands)

  Removed sections:
  - "Ansible-Specific Standards" (absorbed into Technology Standards → Ansible)

  Templates requiring updates:
  - .specify/templates/plan-template.md: ✅ updated (Constitution Check gate list)
  - .specify/templates/spec-template.md: ✅ no changes needed
  - .specify/templates/tasks-template.md: ✅ no changes needed
  - CLAUDE.md: ✅ updated (Architecture + Quality Gates sections)
  - AGENTS.md: ✅ updated (mirrors CLAUDE.md)

  Follow-up TODOs:
  - mypy is configured in pyproject.toml but is NOT yet a CI gate (see Quality
    Gates → Known gap). Closing that gap is tracked work, not a constitution
    deferral.
-->

# Remo Project Constitution

Remo is a multi-layer system: a Python CLI (`src/remo_cli/`), an optional
FastAPI web service (`src/remo_cli/web/`), a TypeScript/React browser console
(`frontend/`), and Ansible roles (`ansible/`) invoked by the CLI. These
principles bind all four layers. Where a rule is layer-specific, it says so.

## Core Principles

### I. Layered Architecture with One-Way Dependencies

The Python package has exactly three layers, and dependencies flow in exactly
one direction: `cli/` → `providers/` → `core/`.

**Rules:**

- `cli/` MUST contain Click command wiring and argument parsing only. No
  business logic.
- `providers/` MUST NOT import Click, and MUST NOT call `sys.exit()`.
- `core/` MUST NOT contain provider-specific knowledge. Provider-varying
  behavior belongs behind a descriptor field or hook (e.g. AWS's SSM
  `ProxyCommand` lives in `providers/aws.py:ssh_proxy_hook`, reached via
  `descriptor.connection.proxy_hook` — never hardcoded in `core/ssh.py`).
- `cli/` MUST NOT reach into a `providers/` module's private
  (leading-underscore) helpers.
- New cross-layer escape hatches MUST be justified in the PR description.

**Rationale:** The one-way rule is what makes a provider addable without
editing existing files, and what keeps `remo --help` from importing a single
provider SDK. It is enforced by `tests/unit/test_architecture.py`, which has
zero-tolerance (empty) allowlists — a new violation fails the build.

### II. Providers Are Declared, Not Special-Cased

Adding a provider MUST require exactly one implementation module plus one
descriptor registration, and MUST NOT require editing any existing file.

**Rules:**

- Every provider MUST satisfy the `Provider` Protocol
  (`core/provider_protocol.py`) and declare a `ProviderDescriptor`
  (`core/provider_registry.py`) in a metadata-only `*_descriptor.py` module
  that imports no SDK.
- Branching on `host.type` string literals is FORBIDDEN outside the descriptor
  layer. Per-type behavior MUST be driven by descriptor fields
  (`name_format`, `registry_fields`, `connection`, `sdk_extra`, …).
- A flag shared by multiple providers MUST be the *same* `OptionSpec` object
  from the shared catalog, not a re-declared copy.
- Provider implementation modules MUST be imported lazily by
  `provider_registry.get_provider()`, so no optional SDK loads during
  `--help` or shell completion.
- A duplicated skeleton across providers is a defect: collapse it into a
  shared template in `core/` (see `core/lifecycle.run_destroy`,
  `core/snapshot.list_all_snapshots`, `core/ansible_runner`,
  `core/output.render_host_table`).

**Rationale:** The pre-018 provider layer was convention-by-copy: five
near-identical skeletons that drifted independently. Declaring providers
instead of copying them makes uniformity a property of the code, not of
reviewer vigilance.

### III. Typed Errors, One Exit Boundary, Actionable Messages

Failures MUST be raised as typed exceptions and translated to exit codes in
exactly one place.

**Rules:**

- The business layer MUST raise a `core/errors.py` subclass —
  `MissingDependencyError`, `PreconditionError`, `OperationFailedError`, or
  `UserAbortedError`. Bare `RuntimeError` and `sys.exit()` are FORBIDDEN
  there.
- `cli/providers/factory.py`'s `provider_command` wrapper is the ONLY
  exception-to-exit-code translation boundary. Exit codes: `0` success,
  `1` failure, `3` user-aborted.
- Non-CLI consumers (the web service) MUST catch `ProviderError` directly
  rather than shelling out to the CLI.
- Prerequisites MUST be validated at the START of an operation (pre-flight),
  not discovered halfway through.
- Every error message MUST state: what failed, why it matters, and the
  concrete command or edit that fixes it. A message that names a remediation
  command is preferred over prose.
- `failed_when: false` (Ansible) and bare `except:` (Python) are permitted
  ONLY with explicit handling of the failure path immediately after.

**Rationale:** One boundary means exit-code behavior is testable in one place
and cannot drift per-provider. Actionable messages are the difference between
a user fixing their own problem and filing an issue.

### IV. Contracts Are Generated, Never Hand-Authored

Where one layer consumes another's shapes, the producing layer is the single
machine-checked source of truth and the consuming layer's types are generated
from it.

**Rules:**

- The FastAPI app is the source of truth for every shape `frontend/` consumes.
  The four generated artifacts (`openapi.json`, `terminal-frames.json`,
  `schema.d.ts`, `terminal-frames.d.ts`) are checked in but MUST NEVER be
  hand-edited.
- A change to a service response model or WS control frame MUST be followed by
  regeneration in the same commit.
- Generated artifacts MUST be byte-reproducible (deterministic ordering,
  stable trailing newline) so drift is a clean diff, not noise.
- A drift check MUST fail the build — never skip, never auto-fix, and never
  write a tracked file. Its failure message MUST name the stale artifact, group
  the findings, and close with the exact regeneration command.
- Generated artifacts are internal build inputs. They carry NO external
  compatibility promise, and consumers outside this repository MUST NOT depend
  on them.
- Independently-evolving contracts MUST version independently. The
  `remo-terminal.v1` WS frame contract is versioned separately from the REST
  OpenAPI contract, with its own drift check.

**Rationale:** Hand-mirrored types are correct only until the next service
change. Generating them converts a whole class of runtime shape mismatch into a
compile error or a build failure with a one-line fix.

### V. Defensive Variable Access (Ansible)

All Ansible registered-variable attributes MUST use `| default()` filters when
accessed in conditionals or templates.

**Rules:**

- NEVER access `.rc`, `.stdout`, `.stderr`, or `.stdout_lines` directly on a
  registered variable.
- ALWAYS use `variable.rc | default(1)` for return codes (default to failure).
- ALWAYS use `variable.stdout | default('')` for output strings.
- To test whether a task actually ran, use `variable.stdout is defined`, not
  `variable is defined`.

**Rationale:** A skipped Ansible task registers `{"skipped": true}` — a dict
with no command-result attributes. Direct access raises "object of type 'dict'
has no attribute", usually in the error path where it is least expected.

**Example — WRONG:**

```yaml
when: my_result.rc == 0
msg: "{{ my_result.stdout }}"
```

**Example — CORRECT:**

```yaml
when: my_result.rc | default(1) == 0
msg: "{{ my_result.stdout | default('N/A') }}"
```

### VI. Test Every Path That Can Skip or Fail

Conditional logic MUST be exercised under all of its conditions before merge.

**Rules:**

- Ansible roles with `when:` conditions MUST be tested with the condition both
  true AND false, and downstream tasks MUST be verified against the skipped
  variable.
- Playbooks MUST be run against a fresh system AND a system with existing
  state.
- Python behavior that a refactor could silently change MUST be pinned by a
  characterization test BEFORE the refactor lands (e.g. the WS control-frame
  silent-drop path, the Hetzner HTTP raise-vs-swallow contracts).
- Error, skip, and abort paths MUST be tested, not just the happy path.
- A bug fix MUST arrive with a regression test that fails without the fix.
- The PR description MUST say which conditional paths were exercised.

**Rationale:** Bugs hide in untested branches. The happy path is the one that
gets manual attention; the skip path is the one that reaches production.

### VII. Idempotent and Re-runnable by Default

Every operation MUST be safe to run twice.

**Rules:**

- Running the same playbook twice MUST produce an identical end state;
  Ansible tasks MUST set `changed_when` accurately and check existing state
  before modifying it.
- Registry writes MUST go through `core/registry.py`, which owns locking
  (`fcntl` on a `registry.lock` sidecar) and atomic replacement
  (`os.replace`). Direct writes to `registry.json` are FORBIDDEN.
- Schema migrations MUST be lazy, one-way, and preserve the prior file
  (legacy `known_hosts` → `known_hosts.v1.bak`).
- `remo web push` and `remo <provider> sync` MUST converge on repeat rather
  than duplicate or thrash.
- Destructive operations MUST have an explicit safeguard: a confirmation
  prompt, a `--yes` opt-out, or a backup. A `--yes` flag MUST have a real
  effect — a decorative one MUST be removed.

**Rationale:** Users re-run automation after failures, timeouts, and
uncertainty. Non-idempotent operations turn a recoverable error into a
corrupted state.

### VIII. Documentation Reflects Reality, and CI Proves It

Documentation MUST be updated in the same change as the code it describes, and
the parts that can be checked mechanically MUST be.

**Rules:**

- The `## Project Structure` diagrams in `CLAUDE.md` and `AGENTS.md` MUST match
  the real `src/remo_cli/**/*.py` tree. This is gated by
  `tests/unit/test_docs_structure.py`; a deliberate omission MUST be added to
  `EXCLUDED_FROM_DOCS` with a stated reason, never silently dropped.
- A behavior change, a new flag, or a removed flag MUST be reflected in
  `README.md` and the relevant `docs/*.md` before merge.
- Documented examples and commands MUST be real and working. Documenting a
  command that does not exist is a defect (see the removed phantom
  `remo init`).
- A dependency whose necessity is not visible from `src/remo_cli/` imports
  alone MUST carry a comment in `pyproject.toml` naming its real consumer.
- Deprecated behavior MUST be documented as deprecated at the moment it is
  deprecated, with the release in which it will be removed.

**Rationale:** Stale documentation is worse than none — it actively misleads.
Anything a test can check, a test should check, because prose drifts silently
and diagrams drift fastest.

## Technology Standards

### Python (CLI, core, providers)

- Python 3.11+; the supported matrix is 3.11, 3.12, 3.13.
- `from __future__ import annotations` at the top of every module; full type
  hints on public functions.
- No docstrings on obvious methods; docstrings SHOULD explain *why*, and
  SHOULD cite the spec/contract they implement when one exists.
- `ruff` (line length 100, `src = ["src"]`) and `mypy`
  (`python_version = "3.11"`, `files = ["src"]`) MUST both pass locally before
  commit.
- Runtime dependencies MUST be justified. An SDK used only by an Ansible
  collection MUST say so in `pyproject.toml`.

### Web Service (`src/remo_cli/web/`)

- The service is an OPTIONAL extra (`uv sync --extra web`). `remo --help` and
  every non-web command MUST work without it installed; web imports MUST be
  lazy.
- Every route MUST declare a response model. A route that returns a `Response`
  subclass directly MUST still construct its declared model, since FastAPI
  skips `response_model` validation in that case.
- The `{"error": {...}}` envelope MUST be declared only on routes that
  actually return it.
- Closed domains MUST be typed as enums; wire fields that must stay open MUST
  be typed `KnownEnum | str`.
- Enums exported into the OpenAPI artifact MUST be fixed at their declared set,
  never derived from a live registry — a third-party install MUST NOT perturb
  a generated artifact.
- Secrets, tokens, and proxy commands MUST be redacted in logs
  (`web/logging_config.py`).

### Frontend (`frontend/`)

- Service-shaped types MUST be imported from `src/api/generated/`. Console-owned
  shapes (local UI state, client-side error wrappers) MAY be hand-written and
  SHOULD be commented as such.
- Presentation maps keyed off a generated union MUST use a
  `Record<GeneratedUnion, …>` so a new enum member is a compile error — while
  RETAINING a runtime fallback for off-union values. Deleting that fallback to
  "achieve exhaustiveness" is a defect, not a cleanup.
- `npm run lint` (`tsc --noEmit`), `npm run test` (Vitest), and
  `npm run build` MUST pass before commit.

### Ansible (`ansible/`)

Before committing Ansible code, verify:

- [ ] All `.rc` accesses use `| default(1)`
- [ ] All `.stdout` accesses use `| default('')`
- [ ] All `when:` conditions handle skipped-task variables
- [ ] All `debug` messages with variable interpolation use defaults
- [ ] Jinja2 templates in `msg:` blocks use safe attribute access

Task registration pattern:

```yaml
# Pattern for tasks that may be skipped
- name: Check something
  ansible.builtin.command: some_command
  register: check_result
  changed_when: false
  failed_when: false
  when: some_condition

# Safe usage of a potentially-skipped result
- name: Use the result
  ansible.builtin.debug:
    msg: "Result: {{ check_result.stdout | default('skipped') }}"
  when: check_result.stdout is defined
```

## Quality Gates

These gates run in CI and MUST pass before merge. None may be skipped,
`xfail`ed, or made conditional to get a build green.

| Gate | Command | Enforces |
|------|---------|----------|
| Tests (3.11/3.12/3.13) | `uv run pytest` | Principles III, VI, VII |
| Architecture | `tests/unit/test_architecture.py` | Principle I |
| Docs structure | `tests/unit/test_docs_structure.py` | Principle VIII |
| Schema drift (Python) | `tests/unit/test_schema_drift.py` | Principle IV |
| Schema drift (Node) | `npm run check:types-fresh` | Principle IV |
| Lint | `uv run ruff check src/remo_cli` | Technology Standards |
| Frontend | `npm run lint && npm run test && npm run build` | Technology Standards |
| Packaging | wheel install smoke, Docker amd64+arm64 | Distribution integrity |
| Security | CodeQL, dependency review | Supply chain |

Regeneration commands, when a drift gate fails:

```bash
uv run python scripts/export_openapi.py   # openapi.json + terminal-frames.json
cd frontend && npm run generate:types      # schema.d.ts + terminal-frames.d.ts
```

**Known gap:** `mypy` is configured in `pyproject.toml` and MUST pass locally,
but is not yet wired into a CI job. Until it is, reviewers MUST treat a type
regression as a blocking finding.

## Development Workflow

### Pre-Commit Checklist

1. **Layer boundaries**: no Click in `providers/`, no provider names in
   `core/`, no `sys.exit` in `providers/`.
2. **Variable safety** (Ansible): grep for `.rc ==` and `.stdout` without
   `| default`.
3. **Conditional coverage**: list every `when:`/branch touched and confirm both
   sides are exercised.
4. **Regeneration**: if a service model or WS frame changed, regenerate and
   commit the four artifacts.
5. **Documentation sync**: diff `README.md`, `docs/*.md`, and the structure
   diagrams against the change.
6. **Idempotency**: run the mutating path twice; verify the second run is a
   no-op.

### Code Review Focus Areas

Reviewers MUST specifically check:

- Layer-boundary violations and new escape hatches
- Registered-variable access patterns (Ansible)
- Error type selection, exit-code correctness, and message actionability
- Hand-written types that duplicate a generated shape
- Removed behavior — especially fallbacks and defaults deleted in the name of
  strictness
- Conditional path coverage and regression tests
- Documentation completeness

## Governance

This constitution establishes non-negotiable standards for the Remo project.
All contributions MUST comply. It supersedes ad-hoc convention; where a
principle and an existing pattern conflict, the principle wins and the pattern
is the defect.

**Amendment Process:**

1. Propose changes via PR with rationale.
2. Document what problem the amendment solves.
3. Update all affected templates, gates, and documentation in the same PR.
4. Increment the version according to the versioning policy below.

**Versioning Policy:**

- **MAJOR**: a principle is removed or redefined, or a new binding constraint
  applies retroactively to existing code.
- **MINOR**: a principle or section is added, or guidance is materially
  expanded.
- **PATCH**: clarification, wording, or typo fixes with no semantic change.

**Compliance:**

- PRs that violate principles MUST be revised before merge.
- Existing violations SHOULD be fixed when the containing file is modified.
- Constitution violations discovered in production are P1 bugs.
- A gate that is failing MUST be fixed or the gate amended by PR. Disabling a
  gate to unblock a merge is FORBIDDEN.

**Runtime guidance:** `CLAUDE.md` and `AGENTS.md` carry the operational detail
(current tree, commands, recent changes). They elaborate this constitution;
they never override it.

**Version**: 2.0.0 | **Ratified**: 2026-01-06 | **Last Amended**: 2026-07-27
