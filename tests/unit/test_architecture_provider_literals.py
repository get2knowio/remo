"""Gate (spec 027 FR-016): the modules this feature de-literalized must stay
provider-name-free. Discovery code, the registry's parse/serialize/validate
paths and the sync scope know no built-in provider's name — every per-type
fact comes from a ``ProviderDescriptor``. Zero-tolerance: the allowlist is empty.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE = REPO_ROOT / "src" / "remo_cli" / "core"

#: The built-in provider type names that must not appear as string literals.
BUILTIN_TYPE_NAMES = frozenset({"incus", "proxmox", "aws", "hetzner"})

#: Modules whose provider-neutrality this feature established.
PROVIDER_NEUTRAL_MODULES = (
    CORE / "provider_plugins.py",
    CORE / "provider_registry.py",
    CORE / "registry.py",
    CORE / "reconcile.py",
)

ALLOWLIST: set[tuple[str, int]] = set()


def _literal_hits(path: Path) -> set[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(REPO_ROOT).as_posix()
    hits: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().lower() in BUILTIN_TYPE_NAMES:
                hits.add((rel, node.lineno))
    return hits


def test_provider_neutral_core_modules_name_no_builtin_provider() -> None:
    found: set[tuple[str, int]] = set()
    for path in PROVIDER_NEUTRAL_MODULES:
        found |= _literal_hits(path)
    unexpected = found - ALLOWLIST
    assert not unexpected, (
        "built-in provider type literal(s) crept back into provider-neutral core "
        f"modules (spec 027 FR-016): {sorted(unexpected)}. Move the per-type fact "
        "onto ProviderDescriptor instead."
    )
    stale = ALLOWLIST - found
    assert not stale, f"ALLOWLIST entries no longer correspond to real sites: {sorted(stale)}"
