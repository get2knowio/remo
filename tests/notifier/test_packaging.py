"""Packaging guard (T044a / SC-007): the base install must not pull notifier deps."""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib as toml
except ModuleNotFoundError:  # pragma: no cover
    import tomli as toml  # type: ignore[import-not-found, no-redef]

_NOTIFIER_DEP_NAMES = {"fastapi", "uvicorn", "pydantic", "python-telegram-bot", "structlog"}


def _project_root() -> Path:
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


def test_base_dependencies_exclude_notifier_runtime() -> None:
    data = toml.loads((_project_root() / "pyproject.toml").read_text())
    base = _names(data["project"]["dependencies"])
    assert not (base & _NOTIFIER_DEP_NAMES), (
        f"notifier runtime deps leaked into base dependencies: {base & _NOTIFIER_DEP_NAMES}"
    )


def _resolve_extra(extras: dict, name: str, _seen: set | None = None) -> set[str]:
    """Flatten an extra, following self-references like ``remo-cli[notifier-core]``."""
    _seen = _seen or set()
    if name in _seen:
        return set()
    _seen.add(name)
    out: set[str] = set()
    for spec in extras.get(name, []):
        base = spec.split(";")[0].strip()
        if base.startswith("remo-cli["):
            inner = base[base.index("[") + 1 : base.index("]")]
            for ref in inner.split(","):
                out |= _resolve_extra(extras, ref.strip(), _seen)
        else:
            out |= _names([spec])
    return out


def test_notifier_telegram_extra_resolves_to_runtime_deps() -> None:
    data = toml.loads((_project_root() / "pyproject.toml").read_text())
    extras = data["project"]["optional-dependencies"]
    # `notifier` is a back-compat alias of `notifier-telegram`; both must pull
    # the full runtime set transitively (notifier-core + the telegram SDK).
    for name in ("notifier", "notifier-telegram"):
        resolved = _resolve_extra(extras, name)
        assert _NOTIFIER_DEP_NAMES <= resolved, (
            f"{name} missing expected deps: {_NOTIFIER_DEP_NAMES - resolved}"
        )
