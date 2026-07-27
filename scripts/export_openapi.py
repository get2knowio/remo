#!/usr/bin/env python3
"""Export the remo-web service's contract artifacts (feature 020).

Writes ``frontend/src/api/generated/openapi.json`` (the REST/OpenAPI
document) from ``create_app().openapi()``, and
``frontend/src/api/generated/terminal-frames.json`` (the separately-versioned
``remo-terminal.v1`` control-frame contract, contracts/terminal-frames-v1.md)
from ``TypeAdapter(...).json_schema()`` over ``remo_cli.web.frames``. No
server is started, no port is bound, no registry or credential state is
touched (FR-006) -- both build entirely from in-process defaults.

Determinism (FR-007, SC-005): serialization is always
``json.dumps(doc, indent=2, sort_keys=True)`` plus a single trailing newline.
Three consecutive runs on unchanged sources must be byte-identical.

Usage::

    uv run python scripts/export_openapi.py            # write the checked-in artifact
    uv run python scripts/export_openapi.py --stdout    # print the REST contract, for hashing

``tests/unit/test_schema_drift.py`` imports ``build_openapi_document`` and
``render_json`` directly (via ``importlib``, since ``scripts/`` is not a
package) so the drift check and this script can never silently diverge in how
the document is built or serialized.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATED_DIR = REPO_ROOT / "frontend" / "src" / "api" / "generated"
OPENAPI_OUT = GENERATED_DIR / "openapi.json"
FRAMES_OUT = GENERATED_DIR / "terminal-frames.json"

#: R-6: every generated artifact names its regeneration command and states it
#: is not hand-edited. For the two JSON artifacts (this file's output, and
#: the frame contract) that header lives in a `x-generated-by` OpenAPI/JSON
#: Schema extension field rather than a comment, since JSON has no comment
#: syntax and the artifact must stay strictly parseable.
GENERATED_NOTICE = (
    "GENERATED FILE -- do not hand-edit. Regenerate with: "
    "uv run python scripts/export_openapi.py. See docs/maintaining-generated-types.md."
)


def _import_web_attr(module_path: str, attr: str) -> Any:
    """Import ``attr`` from ``module_path``, failing actionably (not with a
    raw traceback) if the ``web`` extra is not installed (FR-008). Shared by
    every entry point below so the "web extra missing" message can't
    word-drift between call sites -- only ``module_path`` (named in the
    message) varies per caller."""
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        print(
            f"error: could not import {module_path} -- the 'web' extra is not "
            "installed.\nInstall it with: uv sync --extra web",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return getattr(module, attr)


def _create_app_factory() -> Any:
    """Import and return `create_app`, failing actionably if the `web`
    extra is not installed (FR-008)."""
    return _import_web_attr("remo_cli.web.app", "create_app")


def build_openapi_document() -> dict[str, Any]:
    """Build the annotated OpenAPI document (FR-006/FR-007).

    No server, no credentials, no registry state: `create_app()` builds
    entirely from `WebSettings` defaults.
    """
    create_app = _create_app_factory()
    doc = create_app().openapi()
    doc.setdefault("info", {})["x-generated-by"] = GENERATED_NOTICE
    return doc


def build_frames_document() -> dict[str, Any]:
    """Build the ``remo-terminal.v1`` control-frame contract document (F-5,
    contracts/terminal-frames-v1.md §4).

    Kept deliberately separate from ``build_openapi_document`` (FR-023/F-6):
    the frame contract versions on its own cadence and is never folded into
    the REST OpenAPI document, since no path references these schemas.
    """
    from pydantic import TypeAdapter

    # Reuses the module-level `INBOUND_FRAME_ADAPTER` frames.py already builds
    # (and `_handle_control` relies on not being rebuilt per-message) instead
    # of constructing a second, independent `TypeAdapter(InboundFrame)` here.
    inbound_adapter = _import_web_attr("remo_cli.web.frames", "INBOUND_FRAME_ADAPTER")
    OutboundFrame = _import_web_attr("remo_cli.web.frames", "OutboundFrame")

    inbound_schema = inbound_adapter.json_schema()
    outbound_schema = TypeAdapter(OutboundFrame).json_schema()
    return {
        "protocol": "remo-terminal.v1",
        "frame_version": 1,
        "inbound": inbound_schema,
        "outbound": outbound_schema,
        "x-generated-by": GENERATED_NOTICE,
    }


def render_json(doc: dict[str, Any]) -> str:
    """R-5: deterministic serialization -- sorted keys, 2-space indent, one
    trailing newline. Shared by the export script and the drift check so
    the two can never disagree on what "the same document" serializes to."""
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print the REST contract to stdout instead of writing the checked-in "
        "artifact (used by the determinism check, SC-005). Logging stays on stderr.",
    )
    args = parser.parse_args(argv)

    text = render_json(build_openapi_document())

    if args.stdout:
        sys.stdout.write(text)
        return 0

    frames_text = render_json(build_frames_document())

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    OPENAPI_OUT.write_text(text)
    print(f"wrote {OPENAPI_OUT.relative_to(REPO_ROOT)}", file=sys.stderr)
    FRAMES_OUT.write_text(frames_text)
    print(f"wrote {FRAMES_OUT.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
