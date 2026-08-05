"""The shipped cloud-init example must stay valid.

`docs/examples/orbstack-cloud-init.yaml` is an artifact users run verbatim
against a fresh VM, where a mistake surfaces as a half-provisioned machine and
a cloud-init log they have to go digging for. These checks are cheap and pin
the properties that a careless edit would break: it must parse, its shell
blocks must be syntactically valid, and it must keep installing the two
packages that remo's configure play hard-fails without.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "docs" / "examples" / "orbstack-cloud-init.yaml"


@pytest.fixture(scope="module")
def config() -> dict:
    assert EXAMPLE.is_file(), f"{EXAMPLE} is referenced from README.md"
    data = yaml.safe_load(EXAMPLE.read_text())
    assert isinstance(data, dict)
    return data


def test_declares_the_cloud_config_header() -> None:
    # cloud-init dispatches on this exact first line; without it the file is
    # silently treated as an unknown format and nothing runs.
    assert EXAMPLE.read_text().startswith("#cloud-config\n")


@pytest.mark.parametrize(
    ("package", "why"),
    [
        ("python3", "Ansible needs an interpreter on the target"),
        (
            "util-linux-extra",
            "provides hwclock; community.general.timezone — the first task in "
            "remo's shared role list — hard-fails without it on Ubuntu 24.04",
        ),
    ],
)
def test_installs_packages_configure_depends_on(config, package: str, why: str) -> None:
    assert package in config["packages"], why


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_runcmd_shell_blocks_are_valid_bash(config) -> None:
    for i, cmd in enumerate(config["runcmd"], start=1):
        if not isinstance(cmd, str) or "\n" not in cmd:
            continue  # a single command, no shell syntax to check
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as fh:
            fh.write(cmd)
            fh.flush()
            result = subprocess.run(["bash", "-n", fh.name], capture_output=True, text=True)
        assert result.returncode == 0, f"runcmd[{i}] is not valid bash:\n{result.stderr}"


def test_placeholder_key_is_obviously_a_placeholder(config) -> None:
    # The guard in runcmd keys off this marker to avoid disabling password
    # auth (and locking the operator out) when no real key was supplied.
    raw = EXAMPLE.read_text()
    assert "REPLACE_WITH_YOUR_PUBLIC_KEY" in raw
    assert raw.count("REPLACE_WITH_YOUR_PUBLIC_KEY") >= 2, (
        "the marker appears in both ssh_authorized_keys and the runcmd guard; "
        "dropping it from the guard would harden SSH with no key installed"
    )


def test_does_not_duplicate_what_remo_configure_installs(config) -> None:
    # Duplicating the toolchain here would drift from the Ansible role list.
    for owned_by_configure in ("docker-ce", "docker.io", "nodejs", "zellij"):
        assert owned_by_configure not in config["packages"]
