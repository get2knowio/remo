"""Service SSH trust-file helpers (`web-identity/known_hosts`) (023).

`_write_lines_atomically` / `known_hosts_line_error` moved here from
`web/api/setup.py` (which imports them back — no behavior change) so the
registry-admin API can maintain per-instance slices of the flat trust file:

* :func:`set_instance_host_keys` — replace exactly the lines belonging to one
  lookup key (`core.web_adopt.known_hosts_lookup_key` form: bare host for port
  22, ``[host]:port`` otherwise). Sound because every line the service stores
  is ``ssh-keyscan``-sourced — plain, never hashed.
* :func:`remove_instance_host_keys` — delete a host's lines via
  ``ssh-keygen -R`` (handles hashed lines defensively, should any ever
  appear), discarding the ``.old`` backup it leaves.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

#: Plausible OpenSSH key-type token, e.g. ssh-ed25519, ecdsa-sha2-nistp256,
#: sk-ssh-ed25519@openssh.com, ssh-rsa-cert-v01@openssh.com.
_HOST_KEY_TYPE_RE = re.compile(r"^(sk-)?(ssh|ecdsa)-[a-z0-9-]+(@[a-z0-9.-]+)?$")
_BASE64_RE = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
_KNOWN_HOSTS_MARKERS = ("@cert-authority", "@revoked")


def known_hosts_line_error(line: str) -> str | None:
    """Basic structural validation of one `known_hosts` line; None when OK."""
    stripped = line.strip()
    if not stripped:
        return "empty line"
    if stripped.startswith("#"):
        return "comment line"
    fields = stripped.split()
    if fields[0].startswith("@"):
        if fields[0] not in _KNOWN_HOSTS_MARKERS:
            return f"unknown marker {fields[0]!r}"
        fields = fields[1:]
    if len(fields) < 3:
        return "fewer than 3 fields (expected: hosts, key type, base64 key)"
    key_type, key_material = fields[1], fields[2]
    if not _HOST_KEY_TYPE_RE.match(key_type):
        return f"implausible key type {key_type!r}"
    if len(key_material) < 16 or not _BASE64_RE.match(key_material):
        return "key material is not plausible base64"
    return None


def write_lines_atomically(path: Path, lines: list[str]) -> None:
    """Write *lines* to *path* atomically via a same-directory temp file + rename.

    Used only for the service's own SSH ``known_hosts`` trust file (not the
    remo registry, which goes through :mod:`core.registry`'s own atomic writer).
    """
    dir_ = path.parent
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(dir=dir_, prefix=".known_hosts_tmp_")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w") as fh:
            for line in lines:
                fh.write(line + "\n")
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _line_hosts_field(line: str) -> str | None:
    """The hosts field of a known_hosts line, or None for blank/comment lines."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    fields = stripped.split()
    if fields[0] in _KNOWN_HOSTS_MARKERS:
        fields = fields[1:]
    if not fields:
        return None
    return fields[0]


def line_matches_lookup_key(line: str, lookup_key: str) -> bool:
    """Whether *line* records keys for *lookup_key*.

    The hosts field may be a comma-separated list (never the case for
    keyscan-sourced lines, but the check costs nothing).
    """
    hosts_field = _line_hosts_field(line)
    if hosts_field is None:
        return False
    return lookup_key in hosts_field.split(",")


def set_instance_host_keys(path: Path, lookup_key: str, lines: list[str]) -> None:
    """Replace *lookup_key*'s lines in the flat trust file with *lines*.

    Reads the file (absent -> empty), drops every line whose hosts field
    matches *lookup_key*, appends *lines*, and writes back atomically. All
    other instances' lines pass through untouched.
    """
    try:
        existing = path.read_text().splitlines()
    except OSError:
        existing = []
    kept = [line for line in existing if not line_matches_lookup_key(line, lookup_key)]
    kept.extend(line.strip() for line in lines if line.strip())
    write_lines_atomically(path, kept)


def remove_instance_host_keys(path: Path, lookup_key: str) -> None:
    """Best-effort removal of *lookup_key*'s lines via ``ssh-keygen -R``.

    ``ssh-keygen -R`` also matches hashed lines (which :func:`
    line_matches_lookup_key` cannot), so deletion goes through the real
    binary; its ``<file>.old`` backup is discarded. Failures (missing file,
    missing binary) are swallowed — removal is cleanup, never a gate.
    """
    if not path.is_file():
        return
    try:
        subprocess.run(
            ["ssh-keygen", "-R", lookup_key, "-f", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    Path(str(path) + ".old").unlink(missing_ok=True)
