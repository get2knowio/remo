"""SSM connector contract: constants, the v1 target codec, exposure gating,
the error-line format, and connector-state file I/O (specs/025-ssm-connector,
research R2/R3/R5/R8/R9, data-model E1/E2/E3/E4/E6).

Pure logic only — no Click, no `sys.exit`, no provider knowledge, and no
`remo_cli.web` import (FR-010: the connector must work without the web
extra). Consumed by `providers/connector.py` (business logic) and
`cli/connector.py` (Click wiring).
"""

from __future__ import annotations

import base64
import binascii
import importlib.resources
import json
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import click

from remo_cli.core.attach import build_attach_argv
from remo_cli.core.validation import validate_name, validate_project_name

if TYPE_CHECKING:
    from remo_cli.models.host import KnownHost

__all__ = [
    "DOCUMENT_NAME",
    "DOCUMENT_VERSION",
    "ERROR_LINE_PREFIX",
    "INSTALL_DIR",
    "LAUNCHER_PATH",
    "PROJECT_MAX_BYTES",
    "RUN_AS_USER_DEFAULT",
    "STATE_DIR_DEFAULT",
    "STATE_DIR_ENV",
    "SUPPORTED_TARGET_VERSIONS",
    "TARGET_MAX_CHARS",
    "TARGET_PATTERN",
    "ConnectorRefusal",
    "ConnectorState",
    "ErrorCode",
    "ExposureConfig",
    "TargetV1",
    "connector_attach_argv",
    "decode_target",
    "dump_connector_state",
    "encode_target",
    "format_error_line",
    "load_connector_state",
    "load_document_text",
    "load_exposure",
    "state_dir",
]

# ---------------------------------------------------------------------------
# Contract v1 constants (contracts/session-document.md, contracts/cli.md)
# ---------------------------------------------------------------------------

DOCUMENT_NAME = "remo-attach"
DOCUMENT_VERSION = 1
SUPPORTED_TARGET_VERSIONS: frozenset[int] = frozenset({1})
TARGET_PATTERN = r"^[A-Za-z0-9_-]{1,1024}$"
TARGET_MAX_CHARS = 1024
PROJECT_MAX_BYTES = 255
LAUNCHER_PATH = "/opt/remo-connector/bin/remo"
INSTALL_DIR = "/opt/remo-connector"
STATE_DIR_DEFAULT = "/var/lib/remo-connector"
STATE_DIR_ENV = "REMO_CONNECTOR_STATE_DIR"
RUN_AS_USER_DEFAULT = "remo-connector"
ERROR_LINE_PREFIX = "remo-connector-error:"

_TARGET_PATTERN_RE = re.compile(TARGET_PATTERN)


class ErrorCode(StrEnum):
    """Stable error-line codes (contracts/session-document.md)."""

    BAD_TARGET = "bad-target"
    UNSUPPORTED_VERSION = "unsupported-version"
    INVALID_NAME = "invalid-name"
    NOT_EXPOSED = "not-exposed"
    RUN_AS_NOT_IN_EFFECT = "run-as-not-in-effect"
    CONFIG_UNREADABLE = "config-unreadable"
    SSH_MISSING = "ssh-missing"


class ConnectorRefusal(Exception):
    """A pre-exec launcher refusal: one documented code + an actionable message."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def format_error_line(code: ErrorCode, message: str) -> str:
    """Render the one-line ``remo-connector-error: <code> <message>`` contract.

    Newlines in *message* are collapsed to spaces so the line stays exactly
    one line on the PTY stream (research R3).
    """
    single_line = " ".join(str(message).splitlines())
    return f"{ERROR_LINE_PREFIX} {code.value} {single_line}"


# ---------------------------------------------------------------------------
# E1: encoded target codec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetV1:
    host: str
    project: str


def encode_target(host: str, project: str) -> str:
    """Encode *host*/*project* as the contract v1 ``target`` parameter value."""
    payload = json.dumps(
        {"v": 1, "host": host, "project": project},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).rstrip(b"=")
    return encoded.decode("ascii")


def decode_target(raw: str) -> TargetV1:
    """Decode and fully validate a ``target`` parameter value.

    Validation order (data-model.md E1): length/pattern -> base64 decode ->
    JSON object -> ``v`` -> field presence/types -> host name -> project
    name/byte cap. Raises :class:`ConnectorRefusal` with the first
    applicable code.
    """
    if not raw or len(raw) > TARGET_MAX_CHARS or not _TARGET_PATTERN_RE.fullmatch(raw):
        raise ConnectorRefusal(
            ErrorCode.BAD_TARGET,
            "target is not a well-formed base64url value (see contracts/session-document.md); "
            "re-encode it",
        )

    padded = raw + "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise ConnectorRefusal(ErrorCode.BAD_TARGET, "target is not valid base64url") from exc

    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConnectorRefusal(
            ErrorCode.BAD_TARGET, "decoded target is not valid UTF-8"
        ) from exc

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorRefusal(ErrorCode.BAD_TARGET, "decoded target is not valid JSON") from exc

    if not isinstance(obj, dict):
        raise ConnectorRefusal(ErrorCode.BAD_TARGET, "decoded target must be a JSON object")

    version = obj.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ConnectorRefusal(
            ErrorCode.BAD_TARGET, "decoded target is missing an integer 'v' field"
        )
    if version not in SUPPORTED_TARGET_VERSIONS:
        raise ConnectorRefusal(
            ErrorCode.UNSUPPORTED_VERSION,
            f"target version {version} is not supported by this launcher "
            f"(supported: {sorted(SUPPORTED_TARGET_VERSIONS)}); use a client that speaks "
            f"document v{DOCUMENT_VERSION}",
        )

    host = obj.get("host")
    project = obj.get("project")
    if not isinstance(host, str) or not host:
        raise ConnectorRefusal(
            ErrorCode.BAD_TARGET, "decoded target is missing a non-empty 'host' field"
        )
    if not isinstance(project, str) or not project:
        raise ConnectorRefusal(
            ErrorCode.BAD_TARGET, "decoded target is missing a non-empty 'project' field"
        )

    try:
        validate_name(host, label="host")
    except click.BadParameter as exc:
        raise ConnectorRefusal(
            ErrorCode.INVALID_NAME, f"invalid host name in target: {exc}"
        ) from exc

    try:
        validate_project_name(project)
    except ValueError as exc:
        raise ConnectorRefusal(
            ErrorCode.INVALID_NAME, f"invalid project name in target: {exc}"
        ) from exc

    if len(project.encode("utf-8")) > PROJECT_MAX_BYTES:
        raise ConnectorRefusal(
            ErrorCode.INVALID_NAME,
            f"project name exceeds the {PROJECT_MAX_BYTES}-byte contract v1 limit",
        )

    return TargetV1(host=host, project=project)


# ---------------------------------------------------------------------------
# R5: connector state dir
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    """Resolve the connector state dir: ``$REMO_CONNECTOR_STATE_DIR`` (tests)
    else :data:`STATE_DIR_DEFAULT`."""
    override = os.environ.get(STATE_DIR_ENV)
    return Path(override) if override else Path(STATE_DIR_DEFAULT)


# ---------------------------------------------------------------------------
# E2: exposure configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExposureConfig:
    exposures: dict[str, frozenset[str]]

    def is_exposed(self, host: str, project: str) -> bool:
        return project in self.exposures.get(host, frozenset())


def load_exposure(path: Path) -> ExposureConfig:
    """Load the connector's exposure allow-list (E2). Default deny.

    Missing file, an empty/absent ``exposures`` list, or ``version != 1``
    all resolve to "nothing exposed" (default deny). A file that exists but
    is not valid JSON, or whose top level is not an object, raises
    :class:`ConnectorRefusal` (``config-unreadable``), naming *path*.
    """
    if not path.exists():
        return ExposureConfig(exposures={})

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"could not read exposure config at {path}: {exc}"
        ) from exc

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"exposure config at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(doc, dict):
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"exposure config at {path} is not a JSON object"
        )

    if doc.get("version") != 1:
        return ExposureConfig(exposures={})

    raw_exposures = doc.get("exposures")
    if raw_exposures is None:
        return ExposureConfig(exposures={})
    if not isinstance(raw_exposures, list):
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"exposure config at {path}: 'exposures' must be a list"
        )

    exposures: dict[str, frozenset[str]] = {}
    for entry in raw_exposures:
        if not isinstance(entry, dict):
            continue
        host = entry.get("host")
        projects = entry.get("projects")
        if not isinstance(host, str) or not isinstance(projects, list):
            continue
        exposures[host] = frozenset(p for p in projects if isinstance(p, str))

    return ExposureConfig(exposures=exposures)


# ---------------------------------------------------------------------------
# E3: connector state record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConnectorState:
    version: int
    name: str
    node_id: str
    region: str
    run_as_user: str
    remo_version: str
    document_version: int
    enrolled_at: str


def load_connector_state(path: Path) -> ConnectorState:
    """Load ``connector.json`` (E3). Raises :class:`ConnectorRefusal`
    (``config-unreadable``) when the file is missing, unreadable, not valid
    JSON, or missing/malformed required fields."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"could not read connector state at {path}: {exc}"
        ) from exc

    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"connector state at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(doc, dict):
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE, f"connector state at {path} is not a JSON object"
        )

    try:
        return ConnectorState(
            version=int(doc["version"]),
            name=str(doc["name"]),
            node_id=str(doc["node_id"]),
            region=str(doc["region"]),
            run_as_user=str(doc["run_as_user"]),
            remo_version=str(doc["remo_version"]),
            document_version=int(doc["document_version"]),
            enrolled_at=str(doc["enrolled_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE,
            f"connector state at {path} is missing or has malformed fields: {exc}",
        ) from exc


def dump_connector_state(state: ConnectorState) -> str:
    """Serialize *state* to the ``connector.json`` contract (E3), trailing newline."""
    doc = {
        "version": state.version,
        "name": state.name,
        "node_id": state.node_id,
        "region": state.region,
        "run_as_user": state.run_as_user,
        "remo_version": state.remo_version,
        "document_version": state.document_version,
        "enrolled_at": state.enrolled_at,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# E4: session document
# ---------------------------------------------------------------------------


def load_document_text() -> str:
    """Return the shipped ``remo-attach`` session document, byte-for-byte.

    Loaded via :mod:`importlib.resources` (package data), not a repo-relative
    path, so the wheel-install smoke job exercises the shipped file (R14).
    """
    return (
        importlib.resources.files("remo_cli.core")
        .joinpath("remo_attach_document.json")
        .read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# Launcher argv (R5)
# ---------------------------------------------------------------------------


def connector_attach_argv(host: KnownHost, project: str, state: Path) -> list[str]:
    """Build the attach argv from the connector's own state-dir paths.

    Uses the shared, host-agnostic :func:`core.attach.build_attach_argv` with
    the connector's own identity/known-hosts/ControlMaster paths —
    byte-identical in shape to what the web console builds for the same host
    and project (SC-002), never the operator's registry-stored identity.
    """
    return build_attach_argv(
        host,
        project,
        control_dir=str(state / "ssh"),
        identity_file=str(state / "id_ed25519"),
        known_hosts_file=str(state / "known_hosts"),
        use_registry_identity=False,
    )
