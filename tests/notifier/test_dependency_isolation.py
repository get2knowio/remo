"""SC-006 / FR-019 (T034a): channel deps are isolated; the base CLI stays light.

Static guarantees derived from pyproject + source: the telegram extra pulls the
Telegram SDK, notifier-core does not, and the catalog/CLI surface imports no
channel/service dependency at module load.
"""

from __future__ import annotations

import ast
from pathlib import Path

try:
    import tomllib as toml
except ModuleNotFoundError:  # pragma: no cover
    import tomli as toml  # type: ignore[import-not-found, no-redef]

_CHANNEL_SDKS = {"python-telegram-bot", "telegram"}
_SERVICE_DEPS = {"fastapi", "uvicorn", "telegram", "httpx"}


def _root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise RuntimeError("pyproject.toml not found")


def _names(specs: list[str]) -> set[str]:
    out = set()
    for spec in specs:
        name = spec.split(";")[0].split("[")[0]
        for sep in (">=", "<=", "==", "~=", ">", "<", "!="):
            name = name.split(sep)[0]
        out.add(name.strip())
    return out


def _extras() -> dict:
    return toml.loads((_root() / "pyproject.toml").read_text())["project"]["optional-dependencies"]


def test_notifier_core_has_no_channel_sdk() -> None:
    core = _names(_extras()["notifier-core"])
    assert not (core & _CHANNEL_SDKS), f"channel SDK leaked into notifier-core: {core & _CHANNEL_SDKS}"
    # but it does carry the agentsh client dep.
    assert "httpx" in core


def test_notifier_telegram_pulls_the_sdk() -> None:
    extras = _extras()
    tg = _names(extras["notifier-telegram"])
    # Either a direct dep or via the self-referential core; check the direct list.
    assert "python-telegram-bot" in tg


def test_base_install_excludes_service_and_channel_deps() -> None:
    base = _names(toml.loads((_root() / "pyproject.toml").read_text())["project"]["dependencies"])
    assert not (base & _SERVICE_DEPS), f"service deps leaked into base install: {base & _SERVICE_DEPS}"


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_laptop_surface_imports_no_channel_or_service_dep() -> None:
    # cli/notifier.py + the catalog + base + descriptor must not import a channel
    # SDK or a service dep at module level (they load lazily, in-container).
    src = _root() / "src" / "remo_cli"
    surface = [
        src / "cli" / "notifier.py",
        src / "notifier" / "channels" / "catalog.py",
        src / "notifier" / "channels" / "base.py",
        src / "notifier" / "channels" / "telegram" / "descriptor.py",
    ]
    forbidden = {"fastapi", "uvicorn", "telegram", "httpx"}
    for path in surface:
        imported = _module_imports(path)
        leaked = imported & forbidden
        assert not leaked, f"{path.name} imports {leaked} at module level"
