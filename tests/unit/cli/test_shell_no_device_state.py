"""US2 T051: assert `remo shell` does not write device-specific identifiers to instance paths."""

from __future__ import annotations

import re
from pathlib import Path


def test_no_device_bound_writes_to_broker_paths():
    """Regression guard: cli/shell.py must not write to /etc/remo-broker/ or /run/remo-broker/.

    Anything device-specific (laptop hostname, machine-id, MAC, etc.) makes
    multi-device access break (US2). This sweeps the file for any literal
    write to broker paths.
    """
    shell_py = Path("src/remo_cli/cli/shell.py").read_text(encoding="utf-8")

    # Write surfaces we care about
    forbidden_patterns = [
        r'write_text\([^)]*/etc/remo-broker',
        r'write_bytes\([^)]*/etc/remo-broker',
        r'write_text\([^)]*/run/remo-broker',
        r'open\([^)]*/etc/remo-broker[^)]*[\'"]w',
        r'open\([^)]*/run/remo-broker[^)]*[\'"]w',
    ]
    for pat in forbidden_patterns:
        assert re.search(pat, shell_py) is None, f"shell.py writes to broker path: {pat}"


def test_no_machine_id_lookups_in_shell():
    """`remo shell` must not stamp the laptop's machine-id into the instance."""
    shell_py = Path("src/remo_cli/cli/shell.py").read_text(encoding="utf-8")
    assert "machine-id" not in shell_py
    assert "/etc/machine-id" not in shell_py
    assert "uuid.getnode" not in shell_py
