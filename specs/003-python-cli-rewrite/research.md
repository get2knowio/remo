# Research: Python CLI Rewrite

**Feature Branch**: `003-python-cli-rewrite`
**Date**: 2026-02-28

## 1. CLI Framework

**Decision**: Click (v8.1+)

**Rationale**: Click's `@group` / `@command` hierarchy maps directly to remo's command tree (`remo -> {incus,hetzner,aws} -> {create,destroy,...}`). Every flag pattern in the existing bash script — `-L` short-only repeatable options, `-v` meaning different things at different nesting levels, `--only`/`--skip` repeatable options, `--no-open`, `--yes/-y`, hyphenated command names like `self-update` — has a first-class Click API. Zero transitive dependencies. Click's `CliRunner` enables in-process testing without spawning subprocesses.

**Alternatives considered**:
- **argparse** (stdlib): Nested subcommand groups are not officially supported — they work but produce broken help text and confusing error messages. The boilerplate for 20+ subcommands across 3 provider groups would be substantial.
- **Typer** (v0.24): Built on Click, so it adds Click as a dependency plus `rich` and `shellingham`. Its main benefit (type-hint inference) provides no value when porting an existing CLI where every flag name is predetermined. Still pre-1.0 with API stability risk.

## 2. Interactive Picker (fzf Replacement)

**Decision**: InquirerPy

**Rationale**: InquirerPy is the only Python library with built-in fuzzy search matching (via pfzy algorithm), closely replicating fzf behavior. It supports scrollable lists, type-to-filter, and works across macOS and Linux terminals. Dependencies are prompt-toolkit and pfzy (both lightweight). The library is feature-complete and stable for the selection use case.

**Risk**: InquirerPy has not had a release in 12+ months. The library is stable for the fuzzy-select use case but may not receive fixes for future terminal compatibility issues.

**Mitigation**: The picker is isolated behind a single function in the codebase. If InquirerPy becomes unusable, swapping to simple-term-menu (zero deps, actively maintained, regex search) or questionary (prompt-toolkit based, substring filter) requires changing one module.

**Alternatives considered**:
- **questionary**: Actively maintained but only supports regex/substring filtering, not fuzzy matching.
- **simple-term-menu**: Zero dependencies, actively maintained, but uses Vim-style `/` search rather than type-to-filter. Less intuitive for users accustomed to fzf.
- **pick**: Too minimal — no search/filter capability at all.
- **prompt_toolkit directly**: Overkill — would require building a custom widget for a simple selection menu.

## 3. Project Structure

**Decision**: `src/remo/` layout with `cli/`, `providers/`, `core/` separation

**Rationale**: The `src/` layout is the modern Python packaging standard recommended by PyPA. It prevents import shadowing bugs where tests accidentally import the local source instead of the installed package. The three-layer separation (`cli/` for Click commands, `providers/` for business logic, `core/` for shared utilities) mirrors the existing bash script's functional sections while enforcing testability — provider logic can be unit-tested without invoking Click, and core utilities have no provider or CLI dependencies.

**Alternatives considered**:
- **Flat layout** (`remo/` at repo root): Simpler but risks import conflicts with the `ansible/` directory and other repo-level files.
- **Single-file approach**: The bash script is ~3900 lines; a single Python file would be equally unmaintainable.
- **Monolithic `commands/` package**: Would mix CLI parsing with business logic, making unit testing harder.

## 4. Build Backend

**Decision**: Hatchling

**Rationale**: Default recommended by PyPA for new projects. Supports `src/` layout natively with minimal configuration. Supports dynamic version derivation from git tags (via `hatch-vcs` plugin), aligning with the existing `git describe --tags` versioning. Produces clean wheels without `setup.py`, `setup.cfg`, or `MANIFEST.in`.

**Alternatives considered**:
- **setuptools**: Works but requires more boilerplate (`setup.cfg` or `setup.py`) and doesn't handle `src/` layout as cleanly.
- **poetry**: Opinionated about dependency management (uses its own lockfile), which conflicts with the git-clone distribution model.
- **flit**: Simpler than hatchling but doesn't support `src/` layout without workarounds.

## 5. Provider SDK Dependencies

**Decision**: Optional extras via `[project.optional-dependencies]`

**Rationale**: A user who only manages Incus containers should not need to install boto3 (150+ MB). Provider SDKs are declared as extras: `pip install remo[aws]`, `pip install remo[hetzner]`, `pip install remo[all]`. Provider modules perform lazy imports and raise a clear error if the SDK is missing.

**Alternatives considered**:
- **All dependencies required**: Simpler install but wastes disk/time for single-provider users.
- **Separate packages per provider**: Over-engineered for a single-developer CLI tool.

## 6. Testing Framework

**Decision**: pytest with pytest-mock

**Rationale**: pytest is the de facto standard for Python testing. pytest-mock provides clean `mocker` fixtures for mocking subprocess calls (SSH, rsync, ansible-playbook) and API clients (boto3, hcloud). Click's `CliRunner` integrates directly with pytest for CLI integration tests.

**Alternatives considered**:
- **unittest**: More verbose, less ergonomic fixtures, no parametrize.
- **nox/tox for test orchestration**: Not needed initially — a single `pytest` invocation covers all tests.
