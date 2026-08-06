"""The shipped cloud-init examples must stay valid.

`docs/examples/orbstack-cloud-init*.yaml` are artifacts users run verbatim
against a fresh VM, where a mistake surfaces as a half-provisioned machine and
a cloud-init log they have to go digging for. These checks are cheap and pin
the properties a careless edit would break: they must parse, their shell blocks
must be syntactically valid, and they must keep installing the packages remo's
configure play hard-fails without.

The Tailscale variant gets two extra guards, both for mistakes that would ship
silently: a real auth key committed to the repo, and the peer-vs-self parsing
bug described on `test_reports_self_not_a_peer`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "docs" / "examples"
BASE = EXAMPLES_DIR / "orbstack-cloud-init.yaml"
TAILSCALE = EXAMPLES_DIR / "orbstack-cloud-init-tailscale.yaml"
ALL_EXAMPLES = [BASE, TAILSCALE]


def _load(path: Path) -> dict:
    assert path.is_file(), f"{path} is referenced from README.md"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    return data


# ---------------------------------------------------------------------------
# Shared: every example
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.name)
def test_declares_the_cloud_config_header(path: Path) -> None:
    # cloud-init dispatches on this exact first line; without it the file is
    # silently treated as an unknown format and nothing runs.
    assert path.read_text().startswith("#cloud-config\n")


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.name)
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
def test_installs_packages_configure_depends_on(path: Path, package: str, why: str) -> None:
    assert package in _load(path)["packages"], why


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.name)
def test_runcmd_shell_blocks_are_valid_bash(path: Path) -> None:
    for i, cmd in enumerate(_load(path)["runcmd"], start=1):
        if not isinstance(cmd, str) or "\n" not in cmd:
            continue  # a single command, no shell syntax to check
        with tempfile.NamedTemporaryFile("w", suffix=".sh") as fh:
            fh.write(cmd)
            fh.flush()
            result = subprocess.run(["bash", "-n", fh.name], capture_output=True, text=True)
        assert result.returncode == 0, f"runcmd[{i}] is not valid bash:\n{result.stderr}"


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.name)
def test_placeholder_key_is_obviously_a_placeholder(path: Path) -> None:
    # The guard in runcmd keys off this marker to avoid disabling password
    # auth (and locking the operator out) when no real key was supplied.
    raw = path.read_text()
    assert "REPLACE_WITH_YOUR_PUBLIC_KEY" in raw
    assert raw.count("REPLACE_WITH_YOUR_PUBLIC_KEY") >= 2, (
        "the marker appears in both ssh_authorized_keys and the runcmd guard; "
        "dropping it from the guard would harden SSH with no key installed"
    )


@pytest.mark.parametrize("path", ALL_EXAMPLES, ids=lambda p: p.name)
def test_does_not_duplicate_what_remo_configure_installs(path: Path) -> None:
    # Duplicating the toolchain here would drift from the Ansible role list.
    for owned_by_configure in ("docker-ce", "docker.io", "nodejs", "zellij"):
        assert owned_by_configure not in _load(path)["packages"]


# ---------------------------------------------------------------------------
# Tailscale variant only
# ---------------------------------------------------------------------------


class TestTailscaleExample:
    def test_no_real_auth_key_is_committed(self) -> None:
        """A live `tskey-…` in the repo is a standing credential to a tailnet.

        Tailscale keys are long-lived unless made ephemeral, and this file is
        meant to be edited locally — so the only acceptable value in git is the
        placeholder.
        """
        raw = TAILSCALE.read_text()
        for match in re.findall(r"tskey-[A-Za-z0-9_-]+", raw):
            assert "REPLACE_WITH_YOUR_AUTH_KEY" in match, (
                f"{match!r} looks like a real Tailscale auth key committed to the repo"
            )

    def test_joining_is_guarded_on_the_placeholder(self) -> None:
        # Without the guard, an unedited file runs `tailscale up` with a literal
        # placeholder and fails the boot instead of just skipping the join.
        joins = [
            c for c in _load(TAILSCALE)["runcmd"]
            if isinstance(c, str) and "tailscale up" in c
        ]
        assert joins, "no `tailscale up` invocation found"
        for block in joins:
            assert "REPLACE_WITH_YOUR_AUTH_KEY" in block, (
                "the join must skip when the key was never filled in"
            )

    def test_reports_self_not_a_peer(self) -> None:
        """The printed `remo add` line must name THIS machine.

        Peers carry a `DNSName` too, so grepping the status JSON for the first
        one can hand back a different machine on the tailnet — verified: with a
        peer listed before `Self`, a `sed`-based match printed
        `remo add mbp user@dev1.…`, an instruction that looks entirely
        plausible and points at the wrong host.
        """
        blocks = [
            c for c in _load(TAILSCALE)["runcmd"]
            if isinstance(c, str) and "DNSName" in c
        ]
        assert blocks, "no block reads DNSName"
        for block in blocks:
            assert '["Self"]' in block, "must select Self explicitly, not the first match"
            assert not re.search(r"sed[^|]*DNSName", block), (
                "a text match over the status JSON can return a peer's name"
            )

    def test_installs_tailscale_via_the_documented_script(self) -> None:
        runcmd = _load(TAILSCALE)["runcmd"]
        assert any(
            isinstance(c, str) and "tailscale.com/install.sh" in c for c in runcmd
        ), "Tailscale's own cloud-init docs use the install script"

    def test_curl_is_available_for_the_install_script(self) -> None:
        # The install script is fetched with curl; a minimal image may not have
        # it, and the failure would read as a Tailscale problem.
        assert "curl" in _load(TAILSCALE)["packages"]
