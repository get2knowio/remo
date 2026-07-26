"""Shared fixtures for remo tests."""

import json
import os
import tempfile

import pytest


@pytest.fixture
def tmp_config_dir(tmp_path):
    """Provide a temporary config directory and set REMO_HOME to it."""
    config_dir = tmp_path / "remo"
    config_dir.mkdir()
    old_home = os.environ.get("REMO_HOME")
    os.environ["REMO_HOME"] = str(config_dir)
    yield config_dir
    if old_home is None:
        os.environ.pop("REMO_HOME", None)
    else:
        os.environ["REMO_HOME"] = old_home


@pytest.fixture
def mock_subprocess(mocker):
    """Mock subprocess.run for testing commands that shell out."""
    return mocker.patch("subprocess.run")


# ---------------------------------------------------------------------------
# Registry v2 fixtures (015-registry-v2)
# ---------------------------------------------------------------------------


def legacy_line(
    type_: str,
    name: str,
    host: str,
    user: str,
    instance_id: str | None = None,
    access_mode: str | None = None,
    region: str | None = None,
) -> str:
    """Build one colon-delimited legacy registry line.

    Field count follows how many of the optional trailing args are passed
    (``None`` omits it and everything after it), matching the legacy format's
    4/6/7-field variants: ``TYPE:NAME:HOST:USER[:INSTANCE_ID[:ACCESS_MODE[:REGION]]]``.
    """
    parts = [type_, name, host, user]
    if instance_id is not None:
        parts.append(instance_id)
    if access_mode is not None:
        parts.append(access_mode)
    if region is not None:
        parts.append(region)
    return ":".join(parts)


LEGACY_FIXTURE_LINES: dict[str, str] = {
    "incus": legacy_line("incus", "nuc/dev1", "dev1.incus", "remo", "paul", "direct"),
    "proxmox": legacy_line(
        "proxmox", "pve1/dev2", "10.0.0.42", "remo", "104", "direct", "root"
    ),
    "aws": legacy_line(
        "aws", "buildbox", "203.0.113.7", "remo", "i-0abc123def456", "ssm", "us-east-1"
    ),
    "hetzner": legacy_line("hetzner", "dev1", "198.51.100.9", "remo"),
    "ssh": legacy_line(
        "ssh", "nas", "nas.lan", "admin", "2222", "direct", "/home/paul/.ssh/id_nas"
    ),
    # Legacy access-mode variants no current writer produces but old files can
    # contain (research R5) — both must map to access: "direct" (type-first rule).
    "incus_implicit_ssm": legacy_line("incus", "old/box", "box.incus", "remo", "paul", "ssm"),
    "proxmox_empty_access": legacy_line(
        "proxmox", "old/pct", "10.0.0.9", "remo", "101", "", "root"
    ),
}


def write_legacy_registry(config_dir, lines: list[str]) -> None:
    """Write *lines* (plus a trailing newline) to config_dir/known_hosts."""
    (config_dir / "known_hosts").write_text("\n".join(lines) + "\n")


def build_v2_host_entry(
    type_: str,
    name: str,
    host: str,
    user: str,
    access: str = "direct",
    **nested_fields: str | int,
) -> dict:
    """Build one v2 hostEntry dict matching contracts/registry-file-v2.md.

    ``nested_fields`` (e.g. ``instance_id="i-abc", region="us-east-1"``) are
    wrapped under the ``type_``-named nested object when any are given.
    """
    entry: dict = {"type": type_, "name": name, "host": host, "user": user, "access": access}
    if nested_fields:
        entry[type_] = dict(nested_fields)
    return entry


def write_v2_registry(config_dir, hosts: list[dict], version: int = 2) -> None:
    """Write a v2 registry.json document built from a list of hostEntry dicts."""
    doc = {"version": version, "hosts": hosts}
    (config_dir / "registry.json").write_text(json.dumps(doc, indent=2) + "\n")


def seed_registry(config_dir, hosts: list) -> None:
    """Write a v2 registry.json document from a list of ``KnownHost`` objects.

    Wraps :func:`write_v2_registry`, converting each host via the real
    ``known_host_to_entry`` serializer so seeded fixtures always match what
    the registry accessor itself would have written.
    """
    from remo_cli.core.registry import known_host_to_entry

    write_v2_registry(config_dir, [known_host_to_entry(h) for h in hosts])
