"""Discovery, synthesis, and validation of per-project broker manifests."""

from __future__ import annotations

import json
import tomllib
from importlib import resources
from pathlib import Path

import jsonschema

from remo_cli.models.manifest import ProjectManifest, SUPPORTED_SCHEMA_VERSIONS

_SCHEMA_FILENAME = "manifest-schema-v1.json"

DEFAULT_SECRETS: tuple[str, ...] = ("github_token",)
DEFAULT_HEADER = (
    "# This file was synthesized by `remo shell` because no broker manifest was found\n"
    "# in this project. It declares which backend secrets the broker may serve to this\n"
    "# project's devcontainer. Edit freely. Committed `.devcontainer/remo-broker.toml`\n"
    "# takes precedence over this file.\n"
)


class ManifestError(RuntimeError):
    """Raised on manifest parse / validation errors with surfaced TOML position info."""


def _load_schema_v1() -> dict:
    with resources.files("remo_cli._schemas").joinpath(_SCHEMA_FILENAME).open(
        "r", encoding="utf-8"
    ) as fp:
        return json.load(fp)


def discover(project_dir: Path) -> Path | None:
    """Return the manifest path Remo should read for this project, or None.

    Priority order (FR-012):
      1. <project>/.devcontainer/remo-broker.toml  (committed)
      2. <project>/.remo/broker.toml                (auto-synthesized, gitignored)
    """
    committed = project_dir / ".devcontainer" / "remo-broker.toml"
    if committed.is_file():
        return committed
    synthesized = project_dir / ".remo" / "broker.toml"
    if synthesized.is_file():
        return synthesized
    return None


def synthesize_default(project_dir: Path) -> ProjectManifest:
    """Write the default `.remo/broker.toml` and ensure `.remo/` is gitignored.

    Returns the in-memory ProjectManifest. Idempotent: if the file already
    exists, returns the parsed manifest without overwriting.
    """
    remo_dir = project_dir / ".remo"
    remo_dir.mkdir(parents=True, exist_ok=True)
    target = remo_dir / "broker.toml"

    if not target.exists():
        secrets_line = ", ".join(f'"{s}"' for s in DEFAULT_SECRETS)
        content = (
            DEFAULT_HEADER
            + "schema_version = 1\n"
            + "\n"
            + "[mcp]\n"
            + f"secrets = [{secrets_line}]\n"
        )
        target.write_text(content, encoding="utf-8")

    _ensure_gitignore(project_dir)
    return load(target)


def _ensure_gitignore(project_dir: Path) -> None:
    """Append `.remo/` to project .gitignore if missing. Idempotent, append-only."""
    gitignore = project_dir / ".gitignore"
    needle = ".remo/"
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        for line in existing.splitlines():
            if line.strip() == needle:
                return
        suffix = "" if existing.endswith("\n") else "\n"
        gitignore.write_text(existing + suffix + needle + "\n", encoding="utf-8")
    else:
        gitignore.write_text(needle + "\n", encoding="utf-8")


def load(path: Path) -> ProjectManifest:
    """Parse + validate a manifest file. Raises ManifestError on any failure."""
    try:
        with path.open("rb") as fp:
            raw = tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{path}: TOML parse error: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"{path}: read failed: {exc}") from exc

    return _validate_raw(raw, source=str(path))


def validate(manifest: ProjectManifest) -> None:
    """Validate an in-memory manifest (re-checks schema + name patterns)."""
    mcp: dict[str, object] = {"secrets": list(manifest.secrets)}
    if manifest.notes is not None:
        mcp["notes"] = manifest.notes
    raw: dict[str, object] = {
        "schema_version": manifest.schema_version,
        "mcp": mcp,
    }
    _validate_raw(raw, source="<memory>")


def _validate_raw(raw: dict, *, source: str) -> ProjectManifest:
    schema = _load_schema_v1()
    schema_version = raw.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ManifestError(
            f"{source}: unsupported schema_version {schema_version!r}; "
            f"this remo build supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path) or "<root>"
        raise ManifestError(f"{source}: schema validation failed at {path}: {exc.message}") from exc

    mcp = raw.get("mcp") or {}
    secrets_raw = mcp.get("secrets") or []
    # De-duplicate while preserving order.
    seen: set[str] = set()
    secrets: list[str] = []
    for s in secrets_raw:
        if s not in seen:
            seen.add(s)
            secrets.append(s)
    notes = mcp.get("notes")
    return ProjectManifest(
        schema_version=int(schema_version),
        secrets=secrets,
        notes=notes,
    )
