"""Host-agnostic attach-argv builder shared by the web console and the
SSM connector launcher (specs/025-ssm-connector, research R4, FR-009/FR-010).

Extracted from `web/terminal.py::build_attach_argv` (which now wraps this
function) so a connector can build the identical `ssh -tt ... "remo-host
sessions attach --project X"` argv without importing `remo_cli.web` — the
connector must work with remo installed without the web extra (FR-010).

Imports only `core.ssh`, `core.remo_host_client`, `core.validation`, and
`models.host` — no provider knowledge, no `remo_cli.web` import.
"""

from __future__ import annotations

from remo_cli.core.remo_host_client import build_remo_host_shell_cmd
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.core.validation import validate_project_name
from remo_cli.models.host import KnownHost

__all__ = ["build_attach_argv"]


def build_attach_argv(
    host: KnownHost,
    project: str,
    *,
    control_dir: str | None = None,
    identity_file: str | None = None,
    known_hosts_file: str | None = None,
    use_registry_identity: bool = True,
) -> list[str]:
    """Build the ``ssh -tt ... "remo-host sessions attach --project X"`` argv.

    The host-agnostic body of what used to be `web/terminal.py`'s
    `build_attach_argv` (see that module's docstring for the mechanics this
    replicates byte-for-byte): `validate_project_name`, then
    `build_ssh_base_cmd(host, tty=True, multiplex=True, ...)` with
    `-o BatchMode=yes` inserted right after `ssh`, then the shlex-quoted
    remote `remo-host sessions attach --project X` command appended as a
    single argv element.

    Every caller supplies its own *identity_file*/*known_hosts_file*/
    *control_dir* — this function has no notion of "the web service" or "the
    connector"; both callers resolve their own paths and pass them in.

    Raises
    ------
    ValueError
        If *project* fails :func:`~remo_cli.core.validation.validate_project_name`.
    """
    validate_project_name(project)
    base = build_ssh_base_cmd(
        host,
        tty=True,
        multiplex=True,
        control_dir=control_dir,
        identity_file=identity_file,
        # Callers decide whether a registry-stored identity path (recorded on
        # the *workstation*) is safe to trust in their own filesystem; see
        # `use_registry_identity`'s docstring on `core.ssh.build_ssh_opts`.
        use_registry_identity=use_registry_identity,
        known_hosts_file=known_hosts_file,
    )
    remote_cmd = build_remo_host_shell_cmd("sessions attach", project=project)
    # base == ["ssh", *opts, "-tt", target]; keep BatchMode right after "ssh".
    return [base[0], "-o", "BatchMode=yes", *base[1:], remote_cmd]
