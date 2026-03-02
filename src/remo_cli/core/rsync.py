"""Rsync-based file transfer for remo cp."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from remo_cli.core.output import print_error


def transfer(
    ssh_opts: list[str],
    ssh_target: str,
    sources: list[str],
    dest: str,
    recursive: bool = False,
    progress: bool = False,
) -> int:
    """Execute an rsync transfer using the given SSH options.

    Parameters
    ----------
    ssh_opts:
        SSH option flags (flat list, e.g. ``["-o", "StrictHostKeyChecking=no"]``).
    ssh_target:
        The ``user@host`` string (used only for building the ``-e`` option;
        the caller embeds it into *sources* or *dest* as appropriate).
    sources:
        One or more source paths.  For downloads these will contain the
        ``user@host:`` prefix; for uploads they are plain local paths.
    dest:
        The destination path.  For uploads this will contain the
        ``user@host:`` prefix; for downloads it is a plain local path.
    recursive:
        When ``True``, add ``-r`` to the rsync invocation.
    progress:
        When ``True``, add ``--progress`` and let rsync write directly to
        the terminal's stdout so the user sees live progress.

    Returns
    -------
    int
        The rsync exit code (0 on success).
    """
    rsync_cmd: list[str] = ["rsync", "-az"]

    if recursive:
        rsync_cmd.append("-r")

    if progress:
        rsync_cmd.append("--progress")

    # Build a quoted -e string so rsync correctly handles SSH options that
    # contain spaces (e.g. ProxyCommand with arguments).  rsync's -e parser
    # supports double quotes, which protects spaces inside option values.
    ssh_cmd = "ssh"
    for opt in ssh_opts:
        ssh_cmd += f' "{opt}"'
    rsync_cmd.extend(["-e", ssh_cmd])

    rsync_cmd.extend(sources)
    rsync_cmd.append(dest)

    # Capture stderr to a temp file for error reporting.
    stderr_log = tempfile.NamedTemporaryFile(
        prefix="remo-cp-", suffix=".log", delete=False, mode="w"
    )

    try:
        if progress:
            # Let stdout pass through to the terminal for live progress display.
            result = subprocess.run(rsync_cmd, stderr=stderr_log)
        else:
            result = subprocess.run(rsync_cmd, stdout=subprocess.DEVNULL, stderr=stderr_log)

        rc = result.returncode
    except FileNotFoundError:
        print_error("rsync is not installed. Please install rsync and try again.")
        stderr_log.close()
        return 1
    finally:
        stderr_log.close()

    if rc != 0:
        # Read and display the full stderr content.
        try:
            with open(stderr_log.name) as f:
                stderr_content = f.read().strip()
        except OSError:
            stderr_content = ""

        print_error("Transfer failed.")
        if stderr_content:
            sys.stderr.write(stderr_content + "\n")

    # Clean up the temp file.
    try:
        os.unlink(stderr_log.name)
    except OSError:
        pass

    return rc
