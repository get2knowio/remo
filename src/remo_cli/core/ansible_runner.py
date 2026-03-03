"""Ansible playbook runner for remo.

Invokes ansible-playbook as a subprocess, with optional filtered output that
shows only PLAY and TASK names, or verbose pass-through mode.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from remo_cli.core.config import get_ansible_dir, get_remo_home, is_verbose
from remo_cli.core.output import BLUE, NC, print_error, print_info

# ANSI color reset for inline use
_RESET = NC


def _find_ansible_cmd() -> str:
    """Return the path to ansible-playbook.

    Checks the same bin directory as the running Python interpreter first
    (covers uv tool installs and venv installs), then falls back to PATH.
    """
    co_installed = Path(sys.executable).parent / "ansible-playbook"
    if co_installed.is_file() and os.access(co_installed, os.X_OK):
        return str(co_installed)
    return "ansible-playbook"


def _ensure_collections() -> None:
    """Install Ansible Galaxy collections if requirements.yml has changed.

    Uses a hash of requirements.yml stored in REMO_HOME as a marker so the
    install only runs once (or again when requirements change).  Collections
    are installed to REMO_HOME/ansible/collections/.
    """
    ansible_dir = get_ansible_dir()
    requirements_file = ansible_dir / "requirements.yml"
    if not requirements_file.is_file():
        return

    remo_home = get_remo_home()
    marker_file = remo_home / "collections.lock"

    current_hash = hashlib.sha256(requirements_file.read_bytes()).hexdigest()
    if marker_file.is_file() and marker_file.read_text().strip() == current_hash:
        return

    print_info("Installing Ansible collections (first run)...")

    galaxy_cmd = str(Path(sys.executable).parent / "ansible-galaxy")
    if not Path(galaxy_cmd).is_file():
        galaxy_cmd = "ansible-galaxy"

    result = subprocess.run(
        [
            galaxy_cmd,
            "collection",
            "install",
            "--upgrade",
            "-r",
            str(requirements_file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print_error(f"Failed to install Ansible collections:\n{result.stderr.strip()}")
        sys.exit(1)

    marker_file.write_text(current_hash)


def _filter_line(line: str, pending: list[str]) -> str | None:
    """Apply the awk-equivalent filter logic to a single output line.

    ``pending`` is a single-element list used as a mutable cell holding the
    currently buffered task name (or empty string for none).

    Returns the string to print, or ``None`` if the line should be suppressed.
    Side-effects: may update ``pending[0]``.
    """
    # PLAY [ ... ] ****  → print in blue, stripping trailing **
    if re.match(r"^PLAY \[", line):
        output_parts: list[str] = []
        if pending[0]:
            output_parts.append(f"\r  · {pending[0]}")
            pending[0] = ""
        cleaned = re.sub(r" \*{2,}$", "", line)
        output_parts.append(f"\r\n\r{BLUE}{cleaned}{NC}")
        return "\n".join(output_parts)

    # PLAY RECAP → flush pending, suppress line
    if re.match(r"^PLAY RECAP", line):
        if pending[0]:
            result = f"\r  · {pending[0]}"
            pending[0] = ""
            return result
        return None

    # TASK [ ... ] ****  → buffer the name
    if re.match(r"^TASK \[", line):
        task = line
        task = re.sub(r"^TASK \[", "", task)
        task = re.sub(r"\] \*{2,}$", "", task)
        task = re.sub(r"^[a-zA-Z_]+ : ", "", task)
        if re.match(r"^Display ", task):
            pending[0] = ""
        else:
            pending[0] = task
        return None

    # skipping: → discard pending task (task was skipped)
    if re.match(r"^skipping:", line):
        pending[0] = ""
        return None

    # Execution lines → flush pending task name if buffered
    if re.match(
        r"^ok:|^changed:|^fatal:|^failed:|^included:|^RUNNING HANDLER|^FAILED - RETRYING:",
        line,
    ):
        if pending[0]:
            result = f"\r  · {pending[0]}"
            pending[0] = ""
            return result
        return None

    # All other lines are suppressed in filtered mode
    return None


def run_playbook(
    playbook: str,
    extra_vars: list[str] | None = None,
    inventory: str | None = None,
    verbose: bool = False,
) -> int:
    """Run an Ansible playbook.

    Parameters
    ----------
    playbook:
        Name of the playbook file (relative to the ansible/ directory).
    extra_vars:
        Additional arguments to pass verbatim to ansible-playbook (e.g.
        ``["-e", "key=value", "-e", "other=val"]``).
    inventory:
        If provided, ``-i <inventory>`` is appended to the command.
    verbose:
        When ``True`` (or when the ``REMO_VERBOSE`` env var is ``"1"``),
        ansible-playbook output passes through directly rather than being
        filtered.

    Returns
    -------
    int
        The exit code of ansible-playbook (0 on success).
    """
    _ensure_collections()

    ansible_dir = get_ansible_dir()
    ansible_cmd = _find_ansible_cmd()

    cmd: list[str] = [ansible_cmd, playbook]
    if extra_vars:
        cmd.extend(extra_vars)
    if inventory is not None:
        cmd.extend(["-i", inventory])

    use_verbose = verbose or is_verbose()

    if use_verbose:
        result = subprocess.run(cmd, cwd=str(ansible_dir))
        return result.returncode

    # --- Filtered mode ---
    log_fd, log_path = tempfile.mkstemp(prefix="remo-playbook.")
    os.close(log_fd)

    proc: subprocess.Popen[bytes] | None = None

    def _cleanup() -> None:
        if proc is not None:
            try:
                proc.kill()
            except OSError:
                pass
        try:
            os.unlink(log_path)
        except OSError:
            pass

    def _signal_handler(signum: int, frame: object) -> None:  # noqa: ARG001
        _cleanup()
        sys.exit(130)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    env = os.environ.copy()
    env["ANSIBLE_NOCOLOR"] = "1"

    try:
        with open(log_path, "wb") as log_file_handle:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ansible_dir),
                stdout=log_file_handle,
                stderr=subprocess.STDOUT,
                env=env,
            )

        # Incrementally read new lines from the log file and feed through filter
        pending: list[str] = [""]
        lines_read = 0

        while True:
            still_running = proc.poll() is None

            # Read any new lines from the log
            with open(log_path, "r", errors="replace") as lf:
                all_lines = lf.readlines()

            total_lines = len(all_lines)
            if total_lines > lines_read:
                new_lines = all_lines[lines_read:total_lines]
                lines_read = total_lines
                for raw_line in new_lines:
                    text = raw_line.rstrip("\n").rstrip("\r")
                    output = _filter_line(text, pending)
                    if output is not None:
                        sys.stdout.write(output + "\n")
                        sys.stdout.flush()

            if not still_running:
                break

            time.sleep(0.5)

        # Final flush: read any lines appended after poll() returned
        time.sleep(0.2)
        with open(log_path, "r", errors="replace") as lf:
            all_lines = lf.readlines()
        total_lines = len(all_lines)
        if total_lines > lines_read:
            new_lines = all_lines[lines_read:total_lines]
            for raw_line in new_lines:
                text = raw_line.rstrip("\n").rstrip("\r")
                output = _filter_line(text, pending)
                if output is not None:
                    sys.stdout.write(output + "\n")
                    sys.stdout.flush()
        # Flush any remaining pending task at end
        if pending[0]:
            sys.stdout.write(f"\r  · {pending[0]}\n")
            sys.stdout.flush()
            pending[0] = ""

        rc = proc.wait()

        if rc != 0:
            print("")
            print_error("Playbook failed. Full output:")
            print("─────────────────────────────────────────────────")
            with open(log_path, "r", errors="replace") as lf:
                sys.stdout.write(lf.read())
            print("─────────────────────────────────────────────────")
            os.unlink(log_path)
            signal.signal(signal.SIGINT, signal.SIG_DFL)
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            return rc

        os.unlink(log_path)
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        return rc

    except Exception:
        _cleanup()
        raise
