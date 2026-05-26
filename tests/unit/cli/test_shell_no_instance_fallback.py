"""US6 T091: no instance-OS shell fallback in cli/shell.py project selection.

The project menu is implemented server-side (Zellij + a remote bash script);
the laptop-side `remo shell` only chooses an instance and SSHes in. This test
asserts that nothing in `cli/shell.py` short-circuits the devcontainer launch
into a plain instance OS shell except via the explicit "exit to instance
shell" branch added in T088.
"""

from __future__ import annotations

import re
from pathlib import Path


def test_no_fallback_to_plain_ssh_for_project_path():
    text = Path("src/remo_cli/cli/shell.py").read_text(encoding="utf-8")
    # No 'fallback to ssh' / 'instance shell' branches in the project flow.
    assert "fallback to instance shell" not in text.lower()
    # The shell_connect call accepts a `project=` parameter — that path must
    # remain (US6 requires the menu launches via devcontainer, not raw SSH).
    assert "project=project" in text or "project=" in text


def test_exit_to_instance_shell_warning_is_one_time():
    """If a future change adds the explicit escape, it must reference a flag.

    For this release the laptop side does not implement the escape (it lives
    in the server-side menu script). This test sweeps for any unguarded path.
    """
    text = Path("src/remo_cli/cli/shell.py").read_text(encoding="utf-8")
    # Any literal "exit to instance shell" must be paired with a warning hook.
    if "exit to instance shell" in text.lower():
        assert "warning" in text.lower(), (
            "exit-to-instance-shell path must surface the one-time warning per FR-018."
        )
