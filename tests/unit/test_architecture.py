"""Architecture-enforcement gates for the provider-abstraction migration.

See specs/018-provider-abstraction/research.md (R4) and
specs/018-provider-abstraction/contracts/errors.md ("Prohibitions").

Gate 1: `sys.exit(...)` must not appear in the business/providers layer
(src/remo_cli/providers/) -- business verbs must raise a typed
core.errors.ProviderError subclass instead; the CLI factory's
`provider_command` wrapper is the single translation boundary.

Gate 2: the cli layer (src/remo_cli/cli/) must not reach into a
`remo_cli.providers.*` module's private (leading-underscore) helpers.

Both violations are pre-existing debt, tracked and removed by later tasks
(T029-T032/T037 for Gate 1, T043 for Gate 2). Until then, each gate is
*transitional*: an explicit allowlist pins today's known sites so the
suite stays green, while any additional/undocumented site fails the
build immediately, and any allowlist entry that no longer corresponds to
a real site also fails (so the list cannot silently rot into
over-permissiveness). Shrinking or emptying an allowlist later requires
only deleting entries here -- no structural change to this test.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_DIR = REPO_ROOT / "src" / "remo_cli" / "providers"
CLI_DIR = REPO_ROOT / "src" / "remo_cli" / "cli"
TERMINALS_API_FILE = REPO_ROOT / "src" / "remo_cli" / "web" / "api" / "terminals.py"


def _relpath(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _iter_python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


# ---------------------------------------------------------------------------
# Gate 1: sys.exit(...) inside src/remo_cli/providers/
# ---------------------------------------------------------------------------


def _find_sys_exit_calls(directory: Path) -> set[tuple[str, int]]:
    """Return {(relative_path, lineno), ...} for every `sys.exit(...)` call."""
    found: set[tuple[str, int]] = set()
    for path in _iter_python_files(directory):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relpath(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "exit"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "sys"
            ):
                found.add((rel, node.lineno))
    return found


# T037 (Phase 5): all four providers now raise typed ProviderError
# subclasses instead of calling sys.exit (T029-T032); the allowlist is
# empty — zero tolerance from here on.
SYS_EXIT_ALLOWLIST: set[tuple[str, int]] = set()


def test_no_new_sys_exit_in_providers_layer() -> None:
    """Gate 1 (research.md R4): sys.exit is banned from the business layer.

    Transitional: permits exactly SYS_EXIT_ALLOWLIST's pre-existing sites
    (migrated one-by-one in Phase 5 / T029-T032) and fails on any
    additional call site, or on a listed site that has since disappeared
    without its allowlist entry being removed.
    """
    found = _find_sys_exit_calls(PROVIDERS_DIR)

    unexpected = found - SYS_EXIT_ALLOWLIST
    assert not unexpected, (
        "New sys.exit(...) call site(s) found in src/remo_cli/providers/ "
        f"that are not in the transitional allowlist: {sorted(unexpected)}. "
        "Business-logic code must raise a core.errors.ProviderError "
        "subclass instead (see "
        "specs/018-provider-abstraction/contracts/errors.md)."
    )

    stale = SYS_EXIT_ALLOWLIST - found
    assert not stale, (
        "Transitional sys.exit allowlist entries no longer found in "
        f"source (remove them from SYS_EXIT_ALLOWLIST): {sorted(stale)}."
    )


# ---------------------------------------------------------------------------
# Gate 2: private cross-module reach-ins from src/remo_cli/cli/ into
# remo_cli.providers.* internals
# ---------------------------------------------------------------------------


def _provider_module_aliases(tree: ast.Module) -> dict[str, str]:
    """Map local names bound to a `remo_cli.providers[.X]` module to its
    dotted module path, e.g. {"providers_incus": "remo_cli.providers.incus"}.

    Only import forms that bind a *module* (not a specific function/class)
    are tracked, since only module aliases can be the base of a private
    attribute reach-in such as `providers_incus._lookup_incus_host(...)`.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "remo_cli.providers" or alias.name.startswith(
                    "remo_cli.providers."
                ):
                    bound = alias.asname or alias.name.split(".")[0]
                    aliases[bound] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "remo_cli.providers":
                # `from remo_cli.providers import incus [as providers_incus]`
                # binds a submodule.
                for alias in node.names:
                    bound = alias.asname or alias.name
                    aliases[bound] = f"{module}.{alias.name}"
    return aliases


def _resolve_dotted(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """Resolve an attribute-access chain rooted at a tracked module alias
    to its full dotted path (e.g. "remo_cli.providers.incus._foo").
    Returns None if the chain does not root in a tracked alias.
    """
    if isinstance(node, ast.Name):
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve_dotted(node.value, aliases)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _is_private_not_dunder(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _find_private_provider_reachins(directory: Path) -> set[tuple[str, int]]:
    """Return {(relative_path, lineno), ...} for every attribute access that
    reaches a private (non-dunder) member through an imported
    `remo_cli.providers.*` module alias.
    """
    found: set[tuple[str, int]] = set()
    for path in _iter_python_files(directory):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        rel = _relpath(path)
        aliases = _provider_module_aliases(tree)
        if not aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            if not _is_private_not_dunder(node.attr):
                continue
            dotted = _resolve_dotted(node, aliases)
            if dotted is not None and dotted.startswith("remo_cli.providers."):
                found.add((rel, node.lineno))
    return found


def _find_noqa_slf001_markers(directory: Path) -> set[tuple[str, int]]:
    """Return {(relative_path, lineno), ...} for every line carrying the
    literal `# noqa: SLF001` suppression comment.

    A plain text scan is used deliberately for this half of the gate: the
    `noqa: SLF001` marker IS the ground truth for "this exact
    private-attribute access was manually reviewed and suppressed" under
    this codebase's ruff configuration, so matching the literal marker is
    simpler and no less reliable than reconstructing ruff's SLF001 AST
    logic. It is cross-checked against the independent AST scan below so
    the two views cannot silently drift apart.
    """
    found: set[tuple[str, int]] = set()
    for path in _iter_python_files(directory):
        rel = _relpath(path)
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if "noqa: SLF001" in line:
                found.add((rel, lineno))
    return found


# Emptied by T021 (Phase 3): the four hand-written cli/providers/*.py modules
# that contained every private cross-module reach-in were deleted outright
# (replaced by the generated CLI, which never reaches into providers/*'s
# private helpers). Originally slated for T043 (Phase 6) to empty gradually;
# deleting the modules did it in one step. No new site may appear undetected.
SLF001_ALLOWLIST: set[tuple[str, int]] = set()


def test_private_provider_reachins_are_marked_and_allowlisted() -> None:
    """Gate 2 (research.md R4): the cli layer may not reach into
    providers/*'s private (leading-underscore) helpers except through
    allowlisted, ruff-suppressed transitional sites.

    Cross-checks three independent views -- an AST scan for private
    attribute access through a tracked `remo_cli.providers.*` module
    alias, a text scan for the `# noqa: SLF001` marker, and the
    transitional allowlist -- and requires all three to agree exactly, so
    neither a new undocumented reach-in nor a stray/stale marker or
    allowlist entry can slip through unnoticed.
    """
    ast_hits = _find_private_provider_reachins(CLI_DIR)
    noqa_hits = _find_noqa_slf001_markers(CLI_DIR)

    undocumented = ast_hits - noqa_hits
    assert not undocumented, (
        "Private remo_cli.providers.* attribute access found without a "
        f"`# noqa: SLF001` marker on the same line: {sorted(undocumented)}. "
        "Either avoid the private reach-in or mark and allowlist it "
        "explicitly as a transitional exception."
    )

    stray_markers = noqa_hits - ast_hits
    assert not stray_markers, (
        "`# noqa: SLF001` marker(s) found with no corresponding private "
        "remo_cli.providers.* attribute access detected: "
        f"{sorted(stray_markers)}. Remove the stale suppression comment."
    )

    unexpected = ast_hits - SLF001_ALLOWLIST
    assert not unexpected, (
        "New private cross-module reach-in(s) found in src/remo_cli/cli/ "
        f"that are not in the transitional allowlist: {sorted(unexpected)}. "
        "The cli layer must not access providers/*'s private helpers (see "
        "specs/018-provider-abstraction/contracts/errors.md Prohibitions)."
    )

    stale = SLF001_ALLOWLIST - ast_hits
    assert not stale, (
        "Transitional SLF001 allowlist entries no longer found in source "
        f"(remove them from SLF001_ALLOWLIST): {sorted(stale)}."
    )


# ---------------------------------------------------------------------------
# Gate 3: ad-hoc control-frame dict literals in web/api/terminals.py
# ---------------------------------------------------------------------------
#
# See specs/020-openapi-type-generation/contracts/terminal-frames-v1.md (F-2,
# SC-012): every WS control frame must be constructed through a
# remo_cli.web.frames model, never a bare dict literal. Every one of the
# frame literals this replaces shared exactly one trait: a `"v"` key. Walking
# the file's AST for any `ast.Dict` node with a `"v"` key catches the whole
# family without depending on the rest of each literal's shape.


def _find_frame_dict_literals(path: Path) -> set[int]:
    """Return line numbers of every `ast.Dict` node with a `"v"` key in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and key.value == "v":
                found.add(node.lineno)
                break
    return found


# Zero-tolerance from day one (SC-012): the T051 refactor eliminated all 5
# pre-existing ad-hoc frame literals, so there is no transitional debt to
# allowlist here, unlike Gates 1/2 above.
FRAME_DICT_LITERAL_ALLOWLIST: set[int] = set()


def test_no_adhoc_control_frame_dict_literals_in_terminals_api() -> None:
    """Gate 3 (terminal-frames-v1.md F-2): no bare `{"v": 1, ...}` literals.

    All six WS control frames must be constructed via remo_cli.web.frames
    models (ResizeFrame/PingFrame/ReadyFrame/ExitFrame/ErrorFrame/PongFrame),
    never as ad-hoc dict literals in web/api/terminals.py.
    """
    found = _find_frame_dict_literals(TERMINALS_API_FILE)

    unexpected = found - FRAME_DICT_LITERAL_ALLOWLIST
    assert not unexpected, (
        'Ad-hoc control-frame dict literal(s) (a dict with a "v" key) found in '
        f"{_relpath(TERMINALS_API_FILE)} at line(s) {sorted(unexpected)}. "
        "Construct control frames through remo_cli.web.frames models instead "
        "(see specs/020-openapi-type-generation/contracts/terminal-frames-v1.md)."
    )

    stale = FRAME_DICT_LITERAL_ALLOWLIST - found
    assert not stale, (
        "Transitional frame-dict-literal allowlist entries no longer found "
        f"in source (remove them from FRAME_DICT_LITERAL_ALLOWLIST): {sorted(stale)}."
    )
