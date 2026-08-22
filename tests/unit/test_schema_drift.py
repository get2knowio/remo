"""REST schema-freshness drift gate (feature 020, User Story 1 -- check A).

Compares ``create_app().openapi()`` (the "live" contract the running service
actually serves) against the checked-in
``frontend/src/api/generated/openapi.json`` baseline, byte-for-byte.

Normative spec: specs/020-openapi-type-generation/contracts/drift-checks.md
(rules R-1..R-6, failure-message requirements M-1..M-6, test matrix
T-1..T-10). This module implements check **A** only (T-1..T-9, T27a). Check
**C** (the terminal-frames contract, T-10/T048a) lands in a later pass once
``src/remo_cli/web/frames.py`` and
``frontend/src/api/generated/terminal-frames.json`` exist; per FR-025/T048a it
MUST reuse the same ``render_failure_message`` helper defined here so the two
checks "read as one family" -- that is why ``Finding``/``render_failure_message``
below are kept generic (a human-readable ``kind`` heading + a one-line
``detail``) rather than hardcoded to OpenAPI-shaped findings. A future
``test_t10_...`` and a ``check_frame_document(...)``-style entry point should
be addable alongside the ones here without touching this generic layer.

Contributor how-to for fixing a failure: docs/maintaining-generated-types.md.

``scripts/export_openapi.py`` is not an importable package (no ``__init__.py``,
not on ``sys.path`` via any packaging config), so it is loaded here via
``importlib.util.spec_from_file_location``, the same pattern
``tests/unit/test_docs_structure.py`` would use for a non-package module --
this ensures the drift check and the exporter script can never silently
disagree on how the document is built (``build_openapi_document``) or
serialized (``render_json``).
"""

from __future__ import annotations

import importlib.util
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_PATH = REPO_ROOT / "frontend" / "src" / "api" / "generated" / "openapi.json"
ARTIFACT_REL = "frontend/src/api/generated/openapi.json"
FRAMES_ARTIFACT_PATH = REPO_ROOT / "frontend" / "src" / "api" / "generated" / "terminal-frames.json"
FRAMES_ARTIFACT_REL = "frontend/src/api/generated/terminal-frames.json"
#: M-source description for the frame check's failure messages (T048a).
FRAMES_SOURCE_DESCRIPTION = "the frame model set (src/remo_cli/web/frames.py)"

#: M-4: the exact regeneration command named in every failure message.
EXPORT_SCRIPT_CMD = "uv run python scripts/export_openapi.py"
#: M-5: the contributor how-to. The doc does not exist yet (T057, Phase 7)
#: -- referencing its path in message text is still correct; the file will
#: exist by the time this feature ships.
MAINTAINING_DOC = "docs/maintaining-generated-types.md"


def _load_export_module() -> ModuleType:
    """Import scripts/export_openapi.py by file path (scripts/ is not a
    package -- see module docstring)."""
    spec = importlib.util.spec_from_file_location(
        "export_openapi", REPO_ROOT / "scripts" / "export_openapi.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_export = _load_export_module()
build_openapi_document = _export.build_openapi_document
build_frames_document = _export.build_frames_document
render_json = _export.render_json


# ---------------------------------------------------------------------------
# Generic finding/message layer -- shared with the future frame check
# (T048a). Deliberately has no knowledge of "path", "component", "frame",
# etc.: a caller supplies already-human-readable group headings and detail
# lines.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One unit of drift.

    ``kind`` is a human-readable group heading (M-2, e.g. "Paths present in
    the app but not in the checked-in schema"); ``detail`` is a single line
    naming the concrete thing that drifted (M-3, e.g. "/api/v1/pairing/mint
    (post)" or "InstanceOut"). Generic across artifact kinds on purpose --
    the REST schema check and the later frame check both produce
    ``Finding``s and both render through the same ``render_failure_message``.
    """

    kind: str
    detail: str


def render_failure_message(
    artifact_path: str,
    findings: list[Finding],
    *,
    source_description: str = "the FastAPI application",
    fix_command: str = EXPORT_SCRIPT_CMD,
    doc_link: str = MAINTAINING_DOC,
) -> str:
    """Render an M-1..M-6-compliant failure message.

    Generic (contracts/drift-checks.md §3, adapted for reuse across check A
    and the future check C, T048a):

    - M-1: ``artifact_path`` is named up front.
    - M-2: findings are grouped by ``kind``, with a count per group.
    - M-3: one item per line, taken verbatim from ``Finding.detail``.
    - M-4: closes with a ``To fix:`` block naming ``fix_command`` exactly.
    - M-5: links ``doc_link``.
    - M-6: states the dependency-bump case explicitly, so a contributor does
      not hunt for a first-party source change that does not exist.
    """
    grouped: dict[str, list[Finding]] = defaultdict(list)
    kind_order: list[str] = []
    for f in findings:
        if f.kind not in grouped:
            kind_order.append(f.kind)
        grouped[f.kind].append(f)

    parts = [f"{artifact_path} is out of sync with {source_description}."]

    for kind in kind_order:
        items = grouped[kind]
        parts.append(f"\n  {kind} ({len(items)}):")
        for f in items:
            parts.append(f"    - {f.detail}")

    parts.append(f"\nTo fix: regenerate and commit the artifact:\n\n    {fix_command}\n")
    parts.append(
        "If you did not change the API, a FastAPI/Pydantic/generator dependency "
        "upgrade can also cause this failure with no first-party source change -- "
        "regenerating and committing is still the correct fix."
    )
    parts.append(f"\nSee {doc_link}.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# R-4: missing/unparseable artifact -- own message shape, never a diff
# against empty.
# ---------------------------------------------------------------------------


def _missing_artifact_message(artifact_path: str, *, artifact_description: str = "OpenAPI") -> str:
    return (
        f"{artifact_path} is missing.\n\n"
        f"The checked-in {artifact_description} artifact does not exist, so there is nothing "
        "to compare the live application against (this is not a diff against "
        "an empty document -- the artifact must exist).\n\n"
        f"To fix: generate it:\n\n    {EXPORT_SCRIPT_CMD}\n\n"
        f"See {MAINTAINING_DOC}."
    )


def _unparseable_artifact_message(
    artifact_path: str, error: str, *, artifact_description: str = "OpenAPI"
) -> str:
    return (
        f"{artifact_path} exists but is not valid JSON: {error}\n\n"
        f"The checked-in {artifact_description} artifact could not be parsed, so there is "
        "nothing to compare the live application against.\n\n"
        f"To fix: regenerate it:\n\n    {EXPORT_SCRIPT_CMD}\n\n"
        f"See {MAINTAINING_DOC}."
    )


# ---------------------------------------------------------------------------
# REST-schema-specific comparison (check A). Pure: operates on already-loaded
# dicts, never touches disk itself -- callers (the T-1 real-repo test, or a
# synthetic test) decide where the "artifact_text" comes from.
# ---------------------------------------------------------------------------

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


def _path_operations(doc: dict[str, Any]) -> set[tuple[str, str]]:
    ops: set[tuple[str, str]] = set()
    for path, item in doc.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            if method in _HTTP_METHODS:
                ops.add((path, method))
    return ops


def _diff_named_schemas(
    live_map: dict[str, Any],
    artifact_map: dict[str, Any],
    *,
    added_kind: str,
    removed_kind: str,
    differ_kind: str,
) -> list[Finding]:
    """Three-way name-keyed diff (added / removed / differ) shared by the
    REST check's `components.schemas` comparison and the frame check's
    per-direction `$defs` comparison below -- the only difference between the
    two call sites is which name->schema map is being compared and what to
    call each kind of drift."""
    findings: list[Finding] = []
    for name in sorted(set(live_map) - set(artifact_map)):
        findings.append(Finding(added_kind, name))
    for name in sorted(set(artifact_map) - set(live_map)):
        findings.append(Finding(removed_kind, name))
    for name in sorted(set(live_map) & set(artifact_map)):
        if live_map[name] != artifact_map[name]:
            findings.append(Finding(differ_kind, name))
    return findings


def compute_findings(live_doc: dict[str, Any], artifact_doc: dict[str, Any]) -> list[Finding]:
    """Semantic diff used only to build a helpful message (M-3) once R-2's
    byte comparison has already determined that drift exists."""
    findings: list[Finding] = []

    live_ops = _path_operations(live_doc)
    artifact_ops = _path_operations(artifact_doc)

    for path, method in sorted(live_ops - artifact_ops):
        findings.append(
            Finding(
                "Paths present in the app but not in the checked-in schema",
                f"{path}  ({method})",
            )
        )
    for path, method in sorted(artifact_ops - live_ops):
        findings.append(
            Finding(
                "Paths present in the checked-in schema but removed from the app",
                f"{path}  ({method})",
            )
        )

    findings.extend(
        _diff_named_schemas(
            live_doc.get("components", {}).get("schemas", {}),
            artifact_doc.get("components", {}).get("schemas", {}),
            added_kind="Component schemas added but not in the checked-in schema",
            removed_kind="Component schemas removed from the app",
            differ_kind="Component schemas that differ",
        )
    )

    return findings


@dataclass(frozen=True)
class SchemaCheckResult:
    failure_message: str | None


def check_openapi_document(
    live_doc: dict[str, Any],
    artifact_text: str | None,
    *,
    artifact_path: str = ARTIFACT_REL,
) -> SchemaCheckResult:
    """Compare *live_doc* (already-built, e.g. from ``build_openapi_document``)
    against *artifact_text* (the checked-in file's raw contents, or ``None``
    if the file does not exist).

    R-1: purely a comparison -- never writes anything.
    R-2: the pass/fail decision is exact-byte comparison against
    ``render_json(live_doc)``; the semantic diff (``compute_findings``) only
    decides what the failure message says.
    R-4: a missing or unparseable artifact gets its own message shape.
    """
    if artifact_text is None:
        return SchemaCheckResult(failure_message=_missing_artifact_message(artifact_path))

    try:
        artifact_doc = json.loads(artifact_text)
    except json.JSONDecodeError as exc:
        return SchemaCheckResult(
            failure_message=_unparseable_artifact_message(artifact_path, str(exc))
        )

    live_text = render_json(live_doc)
    if live_text == artifact_text:
        return SchemaCheckResult(failure_message=None)

    findings = compute_findings(live_doc, artifact_doc)
    if not findings:
        # Bytes differ (formatting/whitespace/key order) even though our
        # semantic comparators see no difference -- still drift per R-2, so
        # still a failure, just with a generic finding rather than an empty
        # findings list.
        findings = [
            Finding(
                "Serialized bytes differ",
                "content is not byte-identical to a fresh export (formatting/whitespace/ordering)",
            )
        ]

    return SchemaCheckResult(
        failure_message=render_failure_message(artifact_path, findings)
    )


# ---------------------------------------------------------------------------
# Frame-contract-specific comparison (check C / T-10, T048a). Same generic
# Finding/render_failure_message layer as check A above -- this is what makes
# FR-025 ("the two must read as one family") a structural guarantee rather
# than something asserted once by hand. Compares the `inbound`/`outbound`
# JSON-Schema `$defs` (one entry per frame model) plus each union's
# discriminator/membership, instead of OpenAPI `paths`/`components.schemas`.
# ---------------------------------------------------------------------------


def _frame_defs(direction_schema: dict[str, Any]) -> dict[str, Any]:
    defs = direction_schema.get("$defs", {})
    return defs if isinstance(defs, dict) else {}


def compute_frame_findings(live_doc: dict[str, Any], artifact_doc: dict[str, Any]) -> list[Finding]:
    """Semantic diff used only to build a helpful message (M-3) once R-2's
    byte comparison has already determined that drift exists."""
    findings: list[Finding] = []

    for direction in ("inbound", "outbound"):
        live_schema = live_doc.get(direction, {}) or {}
        artifact_schema = artifact_doc.get(direction, {}) or {}
        live_defs = _frame_defs(live_schema)
        artifact_defs = _frame_defs(artifact_schema)

        findings.extend(
            _diff_named_schemas(
                live_defs,
                artifact_defs,
                added_kind=f"Frames added to the {direction} union but not in the checked-in artifact",
                removed_kind=(
                    f"Frames present in the checked-in {direction} artifact but removed "
                    "from the union"
                ),
                differ_kind=f"Frame schemas that differ ({direction})",
            )
        )

        if live_schema.get("discriminator") != artifact_schema.get(
            "discriminator"
        ) or live_schema.get("oneOf") != artifact_schema.get("oneOf"):
            findings.append(
                Finding(
                    "Union membership/discriminator differs",
                    f"the {direction} union",
                )
            )

    if live_doc.get("protocol") != artifact_doc.get("protocol") or live_doc.get(
        "frame_version"
    ) != artifact_doc.get("frame_version"):
        findings.append(
            Finding("Top-level contract fields differ", "protocol / frame_version")
        )

    return findings


def check_frames_document(
    live_doc: dict[str, Any],
    artifact_text: str | None,
    *,
    artifact_path: str = FRAMES_ARTIFACT_REL,
) -> SchemaCheckResult:
    """Compare *live_doc* (e.g. from ``build_frames_document``) against
    *artifact_text* (the checked-in file's raw contents, or ``None`` if the
    file does not exist). Mirrors ``check_openapi_document`` exactly (R-1,
    R-2, R-4) but for the frame contract's shape."""
    if artifact_text is None:
        return SchemaCheckResult(
            failure_message=_missing_artifact_message(
                artifact_path, artifact_description="terminal-frames"
            )
        )

    try:
        artifact_doc = json.loads(artifact_text)
    except json.JSONDecodeError as exc:
        return SchemaCheckResult(
            failure_message=_unparseable_artifact_message(
                artifact_path, str(exc), artifact_description="terminal-frames"
            )
        )

    live_text = render_json(live_doc)
    if live_text == artifact_text:
        return SchemaCheckResult(failure_message=None)

    findings = compute_frame_findings(live_doc, artifact_doc)
    if not findings:
        findings = [
            Finding(
                "Serialized bytes differ",
                "content is not byte-identical to a fresh export (formatting/whitespace/ordering)",
            )
        ]

    return SchemaCheckResult(
        failure_message=render_failure_message(
            artifact_path, findings, source_description=FRAMES_SOURCE_DESCRIPTION
        )
    )


# ---------------------------------------------------------------------------
# T-1: the real repository, post-implementation. Must pass with zero
# findings right now (Phase 2 already produced a matching baseline).
# ---------------------------------------------------------------------------


def test_t1_real_repository_schema_matches_checked_in_artifact() -> None:
    try:
        live_doc = build_openapi_document()
    except SystemExit as exc:
        # NOT pytest.skip: a missing `web` extra must fail this check loudly
        # (R-3/FR-017), mirroring test_docs_structure.py's real-repository
        # test refusing to skip on a missing heading -- a skip would leave CI
        # green with zero coverage over the REST contract.
        pytest.fail(
            "could not build the OpenAPI document: importing remo_cli.web "
            f"failed ({exc}). Install the 'web' extra: uv sync --extra web. "
            "This check must fail, not skip, when the toolchain is unavailable."
        )
        return  # unreachable; keeps type-checkers happy about live_doc below

    assert ARTIFACT_PATH.is_file(), (
        f"{ARTIFACT_REL} does not exist. Generate it with: {EXPORT_SCRIPT_CMD}"
    )
    artifact_text = ARTIFACT_PATH.read_text(encoding="utf-8")
    result = check_openapi_document(live_doc, artifact_text)
    assert result.failure_message is None, result.failure_message


# ---------------------------------------------------------------------------
# T-2 .. T-5: hermetic synthetic tests. No tracked file is ever mutated --
# these construct minimal in-memory OpenAPI-shaped dicts.
# ---------------------------------------------------------------------------


def test_t2_path_missing_from_artifact_names_path_and_method() -> None:
    live_doc = {
        "paths": {
            "/api/v1/hosts": {"get": {}},
            "/api/v1/pairing/mint": {"post": {}},
        },
        "components": {"schemas": {}},
    }
    artifact_doc = {"paths": {"/api/v1/hosts": {"get": {}}}, "components": {"schemas": {}}}
    artifact_text = render_json(artifact_doc)

    result = check_openapi_document(live_doc, artifact_text)

    assert result.failure_message is not None
    assert "/api/v1/pairing/mint" in result.failure_message
    assert "post" in result.failure_message


def test_t3_component_schema_diff_names_component() -> None:
    live_doc = {
        "paths": {"/api/v1/hosts": {"get": {}}},
        "components": {
            "schemas": {
                "InstanceOut": {
                    "type": "object",
                    "properties": {"instance_id": {"type": "string"}, "status": {"type": "string"}},
                }
            }
        },
    }
    artifact_doc = {
        "paths": {"/api/v1/hosts": {"get": {}}},
        "components": {
            "schemas": {
                "InstanceOut": {
                    "type": "object",
                    "properties": {"instance_id": {"type": "string"}},
                }
            }
        },
    }
    artifact_text = render_json(artifact_doc)

    result = check_openapi_document(live_doc, artifact_text)

    assert result.failure_message is not None
    assert "InstanceOut" in result.failure_message


def test_t4_missing_artifact_file_gets_r4_message() -> None:
    live_doc = {"paths": {}, "components": {"schemas": {}}}

    result = check_openapi_document(live_doc, None)

    assert result.failure_message is not None
    assert ARTIFACT_REL in result.failure_message
    assert "missing" in result.failure_message.lower()
    # R-4: not a diff-against-empty. No finding-group headings should appear.
    assert "Paths present" not in result.failure_message


def test_t5_unparseable_artifact_gets_r4_message_naming_parse_problem() -> None:
    live_doc = {"paths": {}, "components": {"schemas": {}}}

    result = check_openapi_document(live_doc, "{this is not json")

    assert result.failure_message is not None
    assert ARTIFACT_REL in result.failure_message
    assert "not valid JSON" in result.failure_message
    assert "Paths present" not in result.failure_message


# ---------------------------------------------------------------------------
# T-6: determinism (SC-005). Three consecutive in-process runs on unchanged
# sources must be byte-identical.
# ---------------------------------------------------------------------------


def test_t6_export_is_byte_identical_across_three_runs() -> None:
    outputs = [render_json(build_openapi_document()) for _ in range(3)]
    assert outputs[0] == outputs[1]
    assert outputs[1] == outputs[2]


# ---------------------------------------------------------------------------
# T-7: no side effects (R-1, FR-019). Running the check against a drifted
# live document must not modify the checked-in artifact.
# ---------------------------------------------------------------------------


def test_t7_check_never_mutates_the_checked_in_artifact() -> None:
    before_bytes = ARTIFACT_PATH.read_bytes()
    before_mtime_ns = ARTIFACT_PATH.stat().st_mtime_ns

    drifted_live_doc = {
        "paths": {"/api/v1/totally-new-endpoint": {"get": {}}},
        "components": {"schemas": {}},
    }
    result = check_openapi_document(drifted_live_doc, ARTIFACT_PATH.read_text(encoding="utf-8"))
    assert result.failure_message is not None  # sanity: this really was drift

    after_bytes = ARTIFACT_PATH.read_bytes()
    after_mtime_ns = ARTIFACT_PATH.stat().st_mtime_ns
    assert before_bytes == after_bytes
    assert before_mtime_ns == after_mtime_ns


# ---------------------------------------------------------------------------
# T-8: KnownProviderType vs. the built-in provider set (FR-004a).
# ---------------------------------------------------------------------------


def test_t8_known_provider_type_matches_builtin_descriptors() -> None:
    from remo_cli.core import provider_registry
    from remo_cli.web.api.hosts import KnownProviderType

    descriptor_names = {d.type_name for d in provider_registry.all_descriptors()}
    enum_names = {member.value for member in KnownProviderType}

    assert enum_names == descriptor_names, (
        "KnownProviderType (src/remo_cli/web/api/hosts.py) has drifted from the "
        "built-in provider set reported by core/provider_registry.all_descriptors() "
        f"(enum={sorted(enum_names)!r}, descriptors={sorted(descriptor_names)!r}). "
        "When a first-party provider is added (or removed), update "
        "KnownProviderType's members to match, then regenerate: "
        f"{EXPORT_SCRIPT_CMD}"
    )


# ---------------------------------------------------------------------------
# T-9: a third-party provider registration must never perturb the exported
# artifact (SC-011) -- KnownProviderType is a fixed literal enum, not derived
# from the live registry at export time.
# ---------------------------------------------------------------------------


def test_t9_third_party_provider_registration_leaves_artifact_byte_identical() -> None:
    from remo_cli.core.provider_registry import (
        ConnectionSpec,
        NameFormat,
        ProviderDescriptor,
        temporary_registration,
    )

    before = render_json(build_openapi_document())

    # Minimal valid descriptor. `implementation` points at a module that does
    # not exist -- fine, because this test only exercises all_descriptors()/
    # register()/temporary_registration() via build_openapi_document(), never
    # get_provider(), so the dotted path is never imported.
    fake_descriptor = ProviderDescriptor(
        type_name="vultr",
        display_name="Vultr",
        default_instance_name="",
        name_format=NameFormat.FLAT,
        registry_fields=(),
        connection=ConnectionSpec(),
        implementation="remo_cli.providers._nonexistent_vultr_module",
    )

    with temporary_registration(fake_descriptor):
        during = render_json(build_openapi_document())

    after = render_json(build_openapi_document())

    assert before == during, (
        "registering a third-party provider type changed the exported OpenAPI "
        "document -- KnownProviderType must stay a fixed literal enum, never "
        "derived from the live provider registry at export time (SC-011)."
    )
    assert before == after


# ---------------------------------------------------------------------------
# T-10: the frame model set vs. the checked-in terminal-frames.json (check C,
# FR-022). Mirrors T-1's real-repository comparison for check A.
# ---------------------------------------------------------------------------


def test_t10_real_repository_frame_models_match_checked_in_artifact() -> None:
    try:
        live_doc = build_frames_document()
    except SystemExit as exc:
        # NOT pytest.skip (R-3/FR-017): a missing `web` extra must fail this
        # check loudly, exactly like T-1's REST counterpart.
        pytest.fail(
            "could not build the frame contract document: importing "
            f"remo_cli.web.frames failed ({exc}). Install the 'web' extra: "
            "uv sync --extra web. This check must fail, not skip, when the "
            "toolchain is unavailable."
        )
        return  # unreachable; keeps type-checkers happy about live_doc below

    assert FRAMES_ARTIFACT_PATH.is_file(), (
        f"{FRAMES_ARTIFACT_REL} does not exist. Generate it with: {EXPORT_SCRIPT_CMD}"
    )
    artifact_text = FRAMES_ARTIFACT_PATH.read_text(encoding="utf-8")
    result = check_frames_document(live_doc, artifact_text)
    assert result.failure_message is None, result.failure_message


def test_t10_frame_removed_from_union_names_the_frame() -> None:
    live_doc = {
        "protocol": "remo-terminal.v1",
        "frame_version": 1,
        "inbound": {
            "$defs": {"PingFrame": {"type": "object"}},
            "discriminator": {"propertyName": "type", "mapping": {"ping": "#/$defs/PingFrame"}},
            "oneOf": [{"$ref": "#/$defs/PingFrame"}],
        },
        "outbound": {"$defs": {}, "discriminator": {}, "oneOf": []},
    }
    artifact_doc = {
        "protocol": "remo-terminal.v1",
        "frame_version": 1,
        "inbound": {
            "$defs": {
                "PingFrame": {"type": "object"},
                "ResizeFrame": {"type": "object"},
            },
            "discriminator": {
                "propertyName": "type",
                "mapping": {
                    "ping": "#/$defs/PingFrame",
                    "resize": "#/$defs/ResizeFrame",
                },
            },
            "oneOf": [{"$ref": "#/$defs/PingFrame"}, {"$ref": "#/$defs/ResizeFrame"}],
        },
        "outbound": {"$defs": {}, "discriminator": {}, "oneOf": []},
    }
    artifact_text = render_json(artifact_doc)

    result = check_frames_document(live_doc, artifact_text)

    assert result.failure_message is not None
    assert "ResizeFrame" in result.failure_message
    assert FRAMES_ARTIFACT_REL in result.failure_message


def test_t10_missing_frames_artifact_gets_r4_message() -> None:
    live_doc = {"protocol": "remo-terminal.v1", "frame_version": 1, "inbound": {}, "outbound": {}}

    result = check_frames_document(live_doc, None)

    assert result.failure_message is not None
    assert FRAMES_ARTIFACT_REL in result.failure_message
    assert "missing" in result.failure_message.lower()
    assert "Frames added" not in result.failure_message


def test_t10_unparseable_frames_artifact_gets_r4_message() -> None:
    live_doc = {"protocol": "remo-terminal.v1", "frame_version": 1, "inbound": {}, "outbound": {}}

    result = check_frames_document(live_doc, "{not json")

    assert result.failure_message is not None
    assert FRAMES_ARTIFACT_REL in result.failure_message
    assert "not valid JSON" in result.failure_message


# ---------------------------------------------------------------------------
# T048a: the frame check's failure message must be produced by the SAME
# render_failure_message helper as the REST check, and carry the same
# M-1..M-6 boilerplate (FR-025 -- "the two must read as one family").
# ---------------------------------------------------------------------------


def test_t048a_frame_and_rest_failure_messages_share_the_same_renderer_boilerplate() -> None:
    frame_findings = [
        Finding(
            "Frames present in the checked-in inbound artifact but removed from the union",
            "ResizeFrame",
        )
    ]
    frame_message = render_failure_message(
        FRAMES_ARTIFACT_REL, frame_findings, source_description=FRAMES_SOURCE_DESCRIPTION
    )

    rest_findings = [Finding("Component schemas removed from the app", "InstanceOut")]
    rest_message = render_failure_message(ARTIFACT_REL, rest_findings)

    # M-4/M-5/M-6 boilerplate text is byte-identical across both call sites --
    # this is only possible because both go through render_failure_message.
    shared_boilerplate = [
        "To fix: regenerate and commit the artifact:",
        f"    {EXPORT_SCRIPT_CMD}",
        "a FastAPI/Pydantic/generator dependency",
        "upgrade can also cause this failure with no first-party source change",
        "regenerating and committing is still the correct fix.",
        f"See {MAINTAINING_DOC}.",
    ]
    for snippet in shared_boilerplate:
        assert snippet in frame_message, snippet
        assert snippet in rest_message, snippet

    # M-1 (artifact naming) and M-2/M-3 (grouped findings) still differ per
    # artifact, as expected -- only the shared boilerplate must match.
    assert FRAMES_ARTIFACT_REL in frame_message
    assert ARTIFACT_REL in rest_message
    assert "ResizeFrame" in frame_message
    assert "InstanceOut" in rest_message


def test_t048a_check_frames_document_failure_routes_through_render_failure_message() -> None:
    """Closes the loop: check_frames_document's *actual* failure path (not
    just a hand-built equivalent) produces a message with the same
    boilerplate as check_openapi_document's actual failure path."""
    live_doc = {
        "protocol": "remo-terminal.v1",
        "frame_version": 1,
        "inbound": {"$defs": {}, "discriminator": {}, "oneOf": []},
        "outbound": {"$defs": {}, "discriminator": {}, "oneOf": []},
    }
    artifact_doc = {
        "protocol": "remo-terminal.v1",
        "frame_version": 1,
        "inbound": {
            "$defs": {"PingFrame": {"type": "object"}},
            "discriminator": {},
            "oneOf": [{"$ref": "#/$defs/PingFrame"}],
        },
        "outbound": {"$defs": {}, "discriminator": {}, "oneOf": []},
    }
    frame_result = check_frames_document(live_doc, render_json(artifact_doc))
    assert frame_result.failure_message is not None

    rest_live_doc = {"paths": {}, "components": {"schemas": {}}}
    rest_artifact_doc = {
        "paths": {"/api/v1/totally-new-endpoint": {"get": {}}},
        "components": {"schemas": {}},
    }
    rest_result = check_openapi_document(rest_live_doc, render_json(rest_artifact_doc))
    assert rest_result.failure_message is not None

    for snippet in (
        "To fix: regenerate and commit the artifact:",
        f"See {MAINTAINING_DOC}.",
        "regenerating and committing is still the correct fix.",
    ):
        assert snippet in frame_result.failure_message
        assert snippet in rest_result.failure_message


# ---------------------------------------------------------------------------
# T27a: every console-called REST endpoint is present in the checked-in
# artifact with a non-empty response schema (SC-006 as an enforced gate, not
# a one-time manual check). A response that FastAPI could only describe as
# `{}` (no response_model/return-type annotation) must fail this test.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConsoleEndpoint:
    path: str
    method: str
    status: str  # the success status code expected to carry the response body
    has_body: bool  # False for a legitimately-empty 204 (no `content` at all)


#: The endpoints frontend/src/api/client.ts calls (data-model.md, plus the
#: host-detail feature's stats + host-admin surface).
#: POST /pairing/end and DELETE /terminals/{id} are declared 204-no-body by
#: design (T012/pairing.py, terminals.py) -- that is correct, not drift, so
#: they are checked for presence only, not for a response schema.
CONSOLE_ENDPOINTS: tuple[_ConsoleEndpoint, ...] = (
    _ConsoleEndpoint("/api/v1/hosts", "get", "200", True),
    _ConsoleEndpoint("/api/v1/sessions", "get", "200", True),
    _ConsoleEndpoint("/api/v1/discovery/refresh", "post", "202", True),
    _ConsoleEndpoint("/api/v1/ready", "get", "200", True),
    _ConsoleEndpoint("/api/v1/pairing/mint", "post", "200", True),
    _ConsoleEndpoint("/api/v1/pairing/end", "post", "204", False),
    _ConsoleEndpoint("/api/v1/terminals", "post", "201", True),
    _ConsoleEndpoint("/api/v1/terminals", "get", "200", True),
    _ConsoleEndpoint("/api/v1/terminals/{terminal_id}", "delete", "204", False),
    # Host detail: ungated live stats + the host-admin maintenance surface
    # (dormant-404 at runtime unless REMO_WEB_HOST_ADMIN=enabled; the OpenAPI
    # artifact still declares the routes -- the contract is the app, not the
    # deployment's gate state).
    _ConsoleEndpoint("/api/v1/hosts/{instance_id}/stats", "get", "200", True),
    _ConsoleEndpoint("/api/v1/hosts/{instance_id}/projects", "post", "202", True),
    _ConsoleEndpoint(
        "/api/v1/hosts/{instance_id}/projects/{project}", "delete", "200", True
    ),
    _ConsoleEndpoint(
        "/api/v1/hosts/{instance_id}/projects/{project}/rebuild", "post", "202", True
    ),
    _ConsoleEndpoint("/api/v1/hosts/{instance_id}/jobs/{job_id}", "get", "200", True),
)


@pytest.mark.parametrize("endpoint", CONSOLE_ENDPOINTS, ids=lambda e: f"{e.method.upper()} {e.path}")
def test_t27a_console_endpoint_has_non_empty_response_schema(endpoint: _ConsoleEndpoint) -> None:
    doc = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))

    path_item = doc.get("paths", {}).get(endpoint.path)
    assert path_item is not None, f"{endpoint.path} is missing from {ARTIFACT_REL}"

    operation = path_item.get(endpoint.method)
    assert operation is not None, (
        f"{endpoint.method.upper()} {endpoint.path} is missing from {ARTIFACT_REL}"
    )

    responses = operation.get("responses")
    assert responses, f"{endpoint.method.upper()} {endpoint.path} has no 'responses' block"

    response = responses.get(endpoint.status)
    assert response is not None, (
        f"{endpoint.method.upper()} {endpoint.path} has no {endpoint.status} response "
        f"declared in {ARTIFACT_REL}"
    )

    if not endpoint.has_body:
        # A 204 legitimately has no "content" key at all -- verified above
        # that the operation/status is declared; nothing more to check.
        return

    content = response.get("content")
    assert content, (
        f"{endpoint.method.upper()} {endpoint.path} {endpoint.status} response has no "
        f"'content' in {ARTIFACT_REL} (SC-006 requires a real response schema)."
    )
    schema = content.get("application/json", {}).get("schema")
    assert schema, (
        f"{endpoint.method.upper()} {endpoint.path} {endpoint.status} response schema is "
        f"empty ({{}} or missing) in {ARTIFACT_REL} (SC-006 requires a real response schema)."
    )
