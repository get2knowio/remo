"""`remo-host host stats --json` on macOS (spec 029 FR-019, research R9).

Darwin is simulated: a `uname` shim says so, there is no /proc or /sys to read,
and sysctl/vm_stat/uptime/ps/df are shims returning what the macOS tools print.
The contract's shape must not move, and the verb must never exit non-zero.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

KEYS = [
    "protocol_version", "uptime_s", "load_1", "load_5", "load_15", "cpu_count", "cpu_used_pct",
    "mem_total", "mem_used", "mem_available", "swap_total", "swap_used", "disks", "temps",
]

TOOLS = (
    "bash sh sed grep awk cat cut date dirname basename mktemp mv rm mkdir find sort tr tail "
    "head sleep id hostname env printf chmod stat kill wc cp touch ln"
).split()


def _setup(tmp_path: Path, shims: dict[str, str]) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "sys"
    root.mkdir()
    bin_dir = root / "bin"
    bin_dir.mkdir()
    for name in TOOLS:
        real = shutil.which(name)
        if real:
            (bin_dir / name).symlink_to(real)
    for name, body in shims.items():
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    lib = home / ".local" / "bin" / "remo-lib.sh"
    env = Environment(autoescape=False)
    lib.write_text(env.from_string((TEMPLATES / "remo-lib.sh.j2").read_text()).render())
    script = home / ".local" / "bin" / "remo-host"
    script.write_text(
        env.from_string((TEMPLATES / "remo-host.sh.j2").read_text()).render(
            dev_workspace_dir="", devcontainer_cli_bin="devcontainer"
        )
    )
    return script, {
        "PATH": str(bin_dir),
        "HOME": str(home),
        # No Linux interface may be consulted on Darwin.
        "REMO_HOST_PROC_ROOT": str(root / "no-proc"),
        "REMO_HOST_SYS_ROOT": str(root / "no-sys"),
    }


def _stats(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, str(script), "host", "stats", "--json"], capture_output=True, text=True, env=env, timeout=60
    )


def test_darwin_reports_real_values_from_the_macos_tools(tmp_path: Path) -> None:
    boot = int(time.time()) - 3600
    shims = {
        "uname": 'echo Darwin',
        "sysctl": (
            'case "$2" in\n'
            "  hw.ncpu) echo 8;;\n"
            "  hw.memsize) echo 17179869184;;\n"
            f'  kern.boottime) echo "{{ sec = {boot}, usec = 0 }} Thu Nov 26 10:00:00 2026";;\n'
            '  vm.swapusage) echo "total = 2048.00M  used = 1024.00M  free = 1024.00M  (encrypted)";;\n'
            "  *) exit 1;;\n"
            "esac"
        ),
        "vm_stat": (
            'echo "Mach Virtual Memory Statistics: (page size of 16384 bytes)"\n'
            'echo "Pages free:                               1000."\n'
            'echo "Pages inactive:                           3000."\n'
            'echo "Pages speculative:                        2000."\n'
            'echo "Pages active:                             9999."'
        ),
        "uptime": 'echo " 10:00  up  1:00, 3 users, load averages: 1.50 2.50 3.50"',
        "ps": 'printf "50.0\\n30.0\\n20.0\\n"',
        "df": (
            '[ "$1" = -B1 ] && { echo "df: illegal option -- B" >&2; exit 1; }\n'
            'echo "Filesystem 1024-blocks Used Available Capacity Mounted on"\n'
            'echo "/dev/disk3s1 1000 400 600 40% /"'
        ),
    }
    script, env = _setup(tmp_path, shims)
    result = _stats(script, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert list(payload) == KEYS  # contract shape and key order unchanged
    assert payload["protocol_version"] == 1
    assert 3590 <= payload["uptime_s"] <= 3700
    assert (payload["load_1"], payload["load_5"], payload["load_15"]) == (1.5, 2.5, 3.5)
    assert payload["cpu_count"] == 8
    assert payload["cpu_used_pct"] == 12.5  # 100% summed over 8 cores
    assert payload["mem_total"] == 17179869184
    assert payload["mem_available"] == (1000 + 3000 + 2000) * 16384
    assert payload["mem_used"] == 17179869184 - 6000 * 16384
    assert payload["swap_total"] == 2048 * 1024 * 1024
    assert payload["swap_used"] == 1024 * 1024 * 1024
    assert payload["disks"] == [
        {"mount": "/", "size_bytes": 1024000, "used_bytes": 409600, "avail_bytes": 614400}
    ]
    assert payload["temps"] == []  # reported unavailable, not invented


def test_darwin_degrades_to_zeros_and_never_fails(tmp_path: Path) -> None:
    """Only `uname` exists: every other source is missing."""
    script, env = _setup(tmp_path, {"uname": "echo Darwin"})
    result = _stats(script, env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert list(payload) == KEYS
    assert payload["mem_total"] == 0 and payload["mem_used"] == 0
    assert payload["cpu_count"] == 1
    assert payload["disks"] == []
    assert payload["temps"] == []


def test_linux_path_is_not_taken_when_uname_says_linux(tmp_path: Path) -> None:
    script, env = _setup(tmp_path, {"uname": "echo Linux", "nproc": "echo 4"})
    result = _stats(script, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["cpu_count"] == 4
