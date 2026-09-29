"""SC-001: the v1 target codec round-trips every valid name and rejects
every hostile raw/invalid input with the documented error code.

specs/025-ssm-connector data-model.md E1, research R2.
"""

from __future__ import annotations

import base64

import pytest

from remo_cli.core.connector import (
    TARGET_MAX_CHARS,
    TARGET_PATTERN,
    ErrorCode,
    TargetV1,
    decode_target,
    encode_target,
    format_error_line,
)

# ---------------------------------------------------------------------------
# SC-001: hostile-name corpus. Each is a value a caller might try to smuggle
# in as a raw (unencoded) `target` parameter, or as a host/project name once
# properly encoded.
# ---------------------------------------------------------------------------

# Every one of these is legal as a *project name* per validate_project_name
# (spaces, quotes, Unicode, a leading dash, and shell metacharacters that
# contain neither a control character nor '/' are all permitted).
HOSTILE_PROJECT_NAMES = [
    "with space",
    'quo"te',
    "it's",
    "ünïcödé 日本",
    "-leading-dash",
    "a;b&&c",
    "`x`",
]

# Hostile strings that are NOT valid project names (rejected by
# validate_project_name itself: a control character, or an embedded '/').
# Used in test_invalid_project_names_are_invalid_name below — round-tripping
# these would defeat the purpose of validation, not exercise it.
INVALID_PROJECT_NAMES = ["$(rm -rf /)", "tab\there"]

VALID_HOST_NAMES = ["lab", "proxmox-1", "host.example", "a1"]


@pytest.mark.parametrize("project", HOSTILE_PROJECT_NAMES)
def test_round_trip_hostile_project_names(project: str) -> None:
    """Encoding then decoding reproduces the original host/project exactly —
    these names are legal as *projects* (spaces, quotes, Unicode, a leading
    dash, and shell metacharacters are all permitted by
    validate_project_name; only control chars, '/', and leading '.' are
    not)."""
    encoded = encode_target("lab", project)
    assert decode_target(encoded) == TargetV1(host="lab", project=project)


@pytest.mark.parametrize("host", VALID_HOST_NAMES)
def test_round_trip_valid_hosts(host: str) -> None:
    encoded = encode_target(host, "remo")
    assert decode_target(encoded) == TargetV1(host=host, project="remo")


# "-leading-dash" is deliberately excluded here: every character in it is
# in the base64url alphabet, so the RAW string coincidentally matches
# TARGET_PATTERN (it is still rejected later, at base64/JSON decode, by
# test_bad_base64_is_bad_target's sibling coverage) — the pattern's job is
# charset/length only, not semantic validity.
_RAW_PATTERN_FAILURES = (
    [n for n in HOSTILE_PROJECT_NAMES if n != "-leading-dash"]
    + INVALID_PROJECT_NAMES
    + [
        "../etc",
        "a/b",
        ".hidden",
        "plain ",
    ]
)


@pytest.mark.parametrize("raw", _RAW_PATTERN_FAILURES)
def test_raw_hostile_strings_fail_the_target_pattern(raw: str) -> None:
    """Every raw (unencoded) hostile string must fail the document's
    `allowedPattern` — SSM itself would refuse it before the launcher runs."""
    import re

    assert re.fullmatch(TARGET_PATTERN, raw) is None


def test_valid_encoded_target_matches_pattern_and_is_within_max_chars() -> None:
    import re

    encoded = encode_target("lab", "a" * 100)
    assert re.fullmatch(TARGET_PATTERN, encoded) is not None
    assert len(encoded) <= TARGET_MAX_CHARS


def test_project_at_255_bytes_round_trips() -> None:
    project = "a" * 255
    assert len(project.encode("utf-8")) == 255
    encoded = encode_target("lab", project)
    assert decode_target(encoded) == TargetV1(host="lab", project=project)


def test_project_over_255_bytes_is_invalid_name() -> None:
    project = "a" * 256
    encoded = encode_target("lab", project)
    with pytest.raises(Exception) as exc_info:
        decode_target(encoded)
    assert exc_info.value.code == ErrorCode.INVALID_NAME  # type: ignore[attr-defined]


def test_unsupported_version_is_rejected() -> None:
    import base64 as b64
    import json

    payload = json.dumps({"v": 2, "host": "lab", "project": "remo"}, separators=(",", ":"))
    encoded = b64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")
    with pytest.raises(Exception) as exc_info:
        decode_target(encoded)
    assert exc_info.value.code == ErrorCode.UNSUPPORTED_VERSION  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "payload",
    [
        b'{"v":1,"host":"lab"}',  # missing project
        b'{"v":1,"project":"remo"}',  # missing host
        b"not json at all",
        b'["v",1]',  # not an object
        b'{"v":"1","host":"lab","project":"remo"}',  # v not an int
    ],
)
def test_malformed_decoded_payloads_are_bad_target(payload: bytes) -> None:
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    with pytest.raises(Exception) as exc_info:
        decode_target(encoded)
    assert exc_info.value.code == ErrorCode.BAD_TARGET  # type: ignore[attr-defined]


def test_bad_base64_is_bad_target() -> None:
    # Valid per the pattern (alphanumeric), but not valid base64 padding/content
    # once the padding is re-added and it's decoded/parsed.
    with pytest.raises(Exception) as exc_info:
        decode_target("_")
    assert exc_info.value.code == ErrorCode.BAD_TARGET  # type: ignore[attr-defined]


def test_invalid_host_name_is_invalid_name() -> None:
    encoded = encode_target("Bad Host!", "remo")
    with pytest.raises(Exception) as exc_info:
        decode_target(encoded)
    assert exc_info.value.code == ErrorCode.INVALID_NAME  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "project", ["../etc", "a/b", ".hidden", *INVALID_PROJECT_NAMES]
)
def test_invalid_project_names_are_invalid_name(project: str) -> None:
    encoded = encode_target("lab", project)
    with pytest.raises(Exception) as exc_info:
        decode_target(encoded)
    assert exc_info.value.code == ErrorCode.INVALID_NAME  # type: ignore[attr-defined]


def test_extra_key_inside_v1_is_ignored() -> None:
    import json as _json

    payload = _json.dumps(
        {"v": 1, "host": "lab", "project": "remo", "future_field": "xyz"},
        separators=(",", ":"),
    )
    encoded = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode("ascii")
    assert decode_target(encoded) == TargetV1(host="lab", project="remo")


def test_format_error_line_is_exactly_one_line() -> None:
    line = format_error_line(ErrorCode.NOT_EXPOSED, "multi\nline\nmessage")
    assert "\n" not in line
    assert line.startswith("remo-connector-error: not-exposed ")
    assert "multi line message" in line


def test_target_pattern_repeat_counts_fit_re2() -> None:
    """SSM validates ``allowedPattern`` with Go's RE2, which caps counted repeats
    at 1000. ``{1,1024}`` was rejected live with ``invalid repeat count`` on the
    first remo-platform apply (2026-09-28); keep every ``{m,n}`` in the shipped
    pattern within RE2's limit so the document can be created at all."""
    import re as _re

    from remo_cli.core.connector import TARGET_MAX_CHARS, TARGET_PATTERN

    counts = [int(n) for _, n in _re.findall(r"\{(\d+),(\d+)\}", TARGET_PATTERN)]
    assert counts, "pattern is expected to carry a counted repeat"
    assert all(n <= 1000 for n in counts), counts
    assert TARGET_MAX_CHARS == max(counts)
