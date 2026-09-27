"""SSM connector business logic: launcher (`attach`), the shipped document
(`document`/`document_name`), enrollment lifecycle (`enroll`/`status`/
`unenroll`), and the pure helpers enrollment needs
(specs/025-ssm-connector, contracts/cli.md).

No Click, no `sys.exit` (Constitution I/III) — every refusal is a
`core.errors` exception (enrollment/status/unenroll) or a printed
`remo-connector-error:` line + `int` return (the launcher, per contracts/
cli.md's own contract, which IS this feature's error-line surface — see
research R3 for why the launcher does not raise through `provider_command`
like every other command here). No `remo_cli.web` import.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable

import remo_cli
from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.connector import (
    DOCUMENT_NAME,
    DOCUMENT_VERSION,
    LAUNCHER_PATH,
    RUN_AS_USER_DEFAULT,
    ConnectorRefusal,
    ErrorCode,
    connector_attach_argv,
    decode_target,
    format_error_line,
    load_document_text,
    load_exposure,
    state_dir,
)
from remo_cli.core.errors import OperationFailedError, PreconditionError, UserAbortedError
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.registry import RegistryError, known_host_to_entry
from remo_cli.core.ssh import build_ssh_base_cmd, build_ssh_opts
from remo_cli.core.validation import validate_project_name, validate_region
from remo_cli.models.host import KnownHost

#: The Session-Manager-assigned user a session runs as when the account
#: owner has NOT configured Run As — the backstop the launcher refuses
#: under (FR-007, research R10).
_SSM_UNCONFIGURED_RUN_AS_USER = "ssm-user"

# NOTE: exposure.json is deliberately NOT in this list. Its absence is a
# valid, expected "nothing exposed yet" state handled by load_exposure()
# itself (default deny -> not-exposed, matching data-model.md E2 and the
# spec's "empty or missing exposure file" acceptance scenario) — these three
# are enrollment artifacts that must always exist post-enrollment, so their
# absence really does mean a broken/incomplete install.
_REQUIRED_STATE_FILES = ("registry.json", "id_ed25519", "known_hosts")


# ---------------------------------------------------------------------------
# US1/US2: the launcher
# ---------------------------------------------------------------------------


def attach(raw_target: str) -> int:
    """Launcher entry point invoked by the `remo-attach` session document.

    Order (contracts/cli.md): run-as -> ssh on PATH -> decode/validate the
    target -> state dir readable -> host in the connector registry ->
    pair exposed -> exec. Any failure before exec prints exactly one
    `remo-connector-error: <code> <message>` line to stdout and returns 1;
    on success this function `os.execvp`s and never returns.
    """
    try:
        argv = _prepare_attach_argv(raw_target)
    except ConnectorRefusal as refusal:
        print(format_error_line(refusal.code, refusal.message), flush=True)
        return 1
    except Exception as exc:  # noqa: BLE001 - FR-011: one error line, never a traceback
        # Anything unforeseen while reading the connector's own state must
        # still honor the one-line contract on the caller's PTY; the message
        # names the state dir (what to fix) and the exception type only.
        print(
            format_error_line(
                ErrorCode.CONFIG_UNREADABLE,
                f"unexpected {type(exc).__name__} while reading connector state at "
                f"{state_dir()}; re-run 'remo connector enroll'",
            ),
            flush=True,
        )
        return 1

    os.execvp(argv[0], argv)
    return 1  # pragma: no cover - os.execvp never returns on success


def _prepare_attach_argv(raw_target: str) -> list[str]:
    _check_run_as()
    if shutil.which("ssh") is None:
        raise ConnectorRefusal(
            ErrorCode.SSH_MISSING,
            "no 'ssh' executable found on PATH; install openssh-client on the connector",
        )

    target = decode_target(raw_target)

    state = state_dir()
    os.environ["REMO_HOME"] = str(state)

    for filename in _REQUIRED_STATE_FILES:
        path = state / filename
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ConnectorRefusal(
                ErrorCode.CONFIG_UNREADABLE,
                f"{path} is missing or unreadable; re-run 'remo connector enroll'",
            )

    try:
        host = next((h for h in get_known_hosts() if h.name == target.host), None)
    except RegistryError as exc:
        raise ConnectorRefusal(
            ErrorCode.CONFIG_UNREADABLE,
            f"connector registry at {state / 'registry.json'} is unreadable: {exc}",
        ) from exc

    exposure = load_exposure(state / "exposure.json")
    if host is None or not exposure.is_exposed(target.host, target.project):
        # Deliberately does not name the host/project (spec edge case): the
        # only thing a caller may learn is exposed-or-not, never whether the
        # host exists in the registry.
        raise ConnectorRefusal(
            ErrorCode.NOT_EXPOSED,
            "that host/project pair is not exposed on this connector; the operator can "
            "add it with: remo connector enroll <connector> ... --expose HOST/PROJECT",
        )

    return connector_attach_argv(host, target.project, state)


def _effective_user() -> str:
    """Name of the EFFECTIVE uid — never ``$USER``/``$LOGNAME`` (which
    `getpass.getuser` prefers), since a session's environment may be
    inherited from the agent rather than reflect the uid it runs as."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def _check_run_as() -> None:
    user = _effective_user()
    if os.geteuid() == 0 or user == _SSM_UNCONFIGURED_RUN_AS_USER:
        raise ConnectorRefusal(
            ErrorCode.RUN_AS_NOT_IN_EFFECT,
            f"session is running as '{user}'; enable Session Manager Run As for user "
            f"'{RUN_AS_USER_DEFAULT}' (preference or SSMSessionRunAs tag)",
        )


# ---------------------------------------------------------------------------
# US4: the shipped document
# ---------------------------------------------------------------------------


def document() -> int:
    """Print the shipped `remo-attach` document byte-for-byte and return 0."""
    sys.stdout.write(load_document_text())
    return 0


def document_name() -> str:
    return DOCUMENT_NAME


# ---------------------------------------------------------------------------
# US3: enrollment lifecycle — pure helpers
# ---------------------------------------------------------------------------


def parse_expose(values: tuple[str, ...]) -> dict[str, list[str]]:
    """Parse repeated ``--expose HOST/PROJECT`` values into ``{host: [project, ...]}``.

    Splits on the LAST ``/`` (a project name never contains one, but a
    host name may, e.g. ``proxmox-1/dev`` — data-model.md E7). Raises
    :class:`PreconditionError` for a malformed value or an invalid project
    name; never calls the playbook (pre-flight, contracts/cli.md).
    """
    exposed: dict[str, list[str]] = {}
    for value in values:
        if "/" not in value:
            raise PreconditionError(
                f"invalid --expose value '{value}': expected HOST/PROJECT"
            )
        host, _, project = value.rpartition("/")
        if not host or not project:
            raise PreconditionError(
                f"invalid --expose value '{value}': both HOST and PROJECT are required"
            )
        try:
            validate_project_name(project)
        except ValueError as exc:
            raise PreconditionError(
                f"invalid project in --expose '{value}': {exc}"
            ) from exc
        projects = exposed.setdefault(host, [])
        if project not in projects:
            projects.append(project)
    return exposed


def _connection_vars(host: KnownHost) -> dict[str, str]:
    """Derive ``host``/``user``/``port``/``identity``/``common_args`` from
    :func:`core.ssh.build_ssh_opts`, so a connector reaches an exposed host
    (or reaches the connector itself, from the operator's workstation)
    exactly as `remo shell` would — no `host.type` branching (Constitution II).
    """
    import shlex as _shlex

    ssh_opts, ssh_target = build_ssh_opts(host)
    user, _, address = ssh_target.partition("@")

    port = str(host.ssh_port)
    identity = ""
    common_args: list[str] = []
    i = 0
    while i < len(ssh_opts):
        opt, value = ssh_opts[i], ssh_opts[i + 1] if i + 1 < len(ssh_opts) else ""
        if opt == "-o" and value.startswith("Port="):
            port = value.split("=", 1)[1]
        elif opt == "-o" and value.startswith("IdentityFile="):
            # Surfaced separately (ansible_ssh_private_key_file) so the
            # operator's workstation reaches the host with exactly the key
            # `remo shell` would use — including the $REMO_SSH_IDENTITY_FILE
            # fallback build_ssh_opts applies, not just the registry value.
            identity = value.split("=", 1)[1]
        elif opt == "-o" and (
            value.startswith("IdentitiesOnly=") or value.startswith("UserKnownHostsFile=")
        ):
            # The connector uses its OWN identity/known-hosts (R9/E5).
            pass
        else:
            common_args.extend([opt, value] if value else [opt])
        i += 2

    return {
        "host": address,
        "user": user,
        "port": port,
        "identity": identity,
        "common_args": _shlex.join(common_args),
    }


def _connection_extra_vars(conn: dict[str, str]) -> dict[str, str]:
    """The ``remo_ssh_*`` extra-vars both connector playbooks take, as a dict
    for a JSON ``-e @file`` (never ``-e key=value`` — see :func:`enroll`)."""
    extra = {
        "remo_ssh_host": conn["host"],
        "remo_ssh_user": conn["user"],
        "remo_ssh_port": conn["port"],
    }
    if conn["identity"]:
        extra["remo_ssh_identity"] = conn["identity"]
    if conn["common_args"]:
        extra["remo_ssh_common_args"] = conn["common_args"]
    return extra


def build_connector_registry_json(exposed: list[KnownHost], connector_name: str) -> str:
    """Serialize *exposed* hosts as a v2 registry document for the connector
    (data-model.md E5): every exposed host becomes a ``type: ssh`` entry
    addressed directly, using the connector's own identity. The connector
    itself (self-target) is addressed at ``localhost``.
    """
    entries = []
    for host in exposed:
        conn = _connection_vars(host)
        address = "localhost" if host.name == connector_name else conn["host"]
        entry_host = KnownHost(
            type="ssh",
            name=host.name,
            host=address,
            user=conn["user"],
            instance_id=conn["port"],
            access_mode="direct",
            region="/var/lib/remo-connector/id_ed25519",
        )
        entries.append(known_host_to_entry(entry_host))
    entries.sort(key=lambda e: (str(e.get("type", "")), str(e.get("name", ""))))
    doc = {"version": 2, "hosts": entries}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# US3: enrollment lifecycle — enroll / status / unenroll
# ---------------------------------------------------------------------------


def enroll(
    name: str,
    *,
    activation_id: str,
    region: str,
    expose: tuple[str, ...],
    run_as_user: str,
    remo_version: str | None,
    remo_source: str | None,
    code: str,
    verbose: bool,
) -> int:
    """Enroll `name` as an SSM connector (idempotent; contracts/cli.md).

    *code* is the activation code, already read by the CLI layer (prompt or
    stdin) — this function never reads input itself. An empty/whitespace
    *code* is a :class:`PreconditionError` raised BEFORE the playbook runs.
    """
    if not code or not code.strip():
        raise PreconditionError(
            "Activation code is required; paste it at the prompt or pipe it on stdin"
        )
    code = code.strip()

    if not activation_id or not activation_id.strip():
        raise PreconditionError("--activation-id is required")

    validate_region(region)

    if run_as_user in ("root", _SSM_UNCONFIGURED_RUN_AS_USER):
        raise PreconditionError(
            f"--run-as-user must not be 'root' or '{_SSM_UNCONFIGURED_RUN_AS_USER}'"
        )

    if remo_version and remo_source:
        raise PreconditionError("--remo-version and --remo-source are mutually exclusive")

    all_hosts = get_known_hosts()
    connector_host = next((h for h in all_hosts if h.name == name), None)
    if connector_host is None:
        raise PreconditionError(
            f"No environment named '{name}' is registered. Add it first: remo add {name} <host>"
        )

    exposures = parse_expose(expose)
    exposed_hosts: list[KnownHost] = []
    for host_name in exposures:
        entry = next((h for h in all_hosts if h.name == host_name), None)
        if entry is None:
            raise PreconditionError(
                f"--expose names unknown host '{host_name}'; register it first with 'remo add' "
                "(or a provider create/sync)"
            )
        if entry.access_mode == "ssm":
            raise PreconditionError(
                f"--expose host '{host_name}' is reachable only via AWS SSM; exposed hosts must "
                "be directly reachable from the connector"
            )
        exposed_hosts.append(entry)

    resolved_remo_version = remo_version or (None if remo_source else remo_cli.__version__)

    conn = _connection_vars(connector_host)
    registry_json = build_connector_registry_json(exposed_hosts, name)

    exposures_payload = [
        {"host": host_name, "projects": sorted(projects)}
        for host_name, projects in sorted(exposures.items())
    ]
    exposed_hosts_payload = []
    for host in exposed_hosts:
        host_conn = _connection_vars(host)
        exposed_hosts_payload.append(
            {
                "name": host.name,
                "address": "localhost" if host.name == name else host_conn["host"],
                "user": host_conn["user"],
                "port": host_conn["port"],
                "identity": host_conn["identity"],
                "common_args": host_conn["common_args"],
            }
        )

    fd, path = tempfile.mkstemp(prefix="remo-connector-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        # Free-form values (ssh common args with spaces/quotes, identity
        # paths, a PEP 508 `remo_source`) travel in this JSON file, never as
        # `-e key=value`: ansible-playbook splits a key=value extra-var on
        # whitespace, so `remo_ssh_common_args=-o SendEnv=TZ` would arrive as
        # just "-o".
        payload: dict[str, object] = {
            "ssm_activation_code": code,
            "connector_exposures": exposures_payload,
            "connector_registry_json": registry_json,
            "connector_exposed_hosts": exposed_hosts_payload,
        }
        payload.update(_connection_extra_vars(conn))
        if remo_source:
            payload["remo_source"] = remo_source
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

        extra: list[str] = [
            "-e", f"@{path}",
            "-e", f"ssm_activation_id={activation_id}",
            "-e", f"ssm_region={region}",
            "-e", f"connector_name={name}",
            "-e", f"connector_run_as_user={run_as_user}",
        ]
        if not remo_source:
            extra += ["-e", f"remo_version={resolved_remo_version}"]

        rc = run_playbook("ssm_connector_enroll.yml", extra, verbose=verbose)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if rc != 0:
        raise OperationFailedError(
            f"Enrollment failed (ansible-playbook rc={rc}). Re-run with --verbose for details."
        )

    return status(name)


def status(name: str) -> int:
    """Print the connector's agent state, managed-node id, region, and
    exposed host/project pairs (contracts/cli.md). Reads over SSH via
    `sudo -n`; never touches AWS."""
    host = next((h for h in get_known_hosts() if h.name == name), None)
    if host is None:
        raise PreconditionError(
            f"No environment named '{name}' is registered. Enroll it first: "
            f"remo connector enroll {name} --activation-id ID --region REGION "
            "--expose HOST/PROJECT"
        )

    remote_cmd = (
        "sudo -n sh -c '"
        "cat /var/lib/remo-connector/connector.json; echo ---; "
        "cat /var/lib/remo-connector/exposure.json; echo ---; "
        "systemctl is-active amazon-ssm-agent; "
        "/opt/remo-connector/bin/remo --version'"
    )
    cmd = build_ssh_base_cmd(host, extra_opts=["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
    cmd.append(remote_cmd)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise OperationFailedError(f"Could not reach '{name}': {exc}") from exc

    if result.returncode != 0:
        raise PreconditionError(
            f"'{name}' is not enrolled, or its state could not be read; run: "
            f"remo connector enroll {name} --activation-id ID --region REGION "
            "--expose HOST/PROJECT"
        )

    sections = result.stdout.split("---\n")
    if len(sections) != 3:
        raise OperationFailedError(f"Unexpected status output from '{name}': {result.stdout!r}")
    connector_text, exposure_text, remainder = sections

    try:
        connector_doc = json.loads(connector_text)
    except json.JSONDecodeError as exc:
        raise PreconditionError(
            f"'{name}' is not enrolled; run: remo connector enroll {name} ..."
        ) from exc

    try:
        exposure_doc = json.loads(exposure_text)
    except json.JSONDecodeError:
        exposure_doc = {"exposures": []}

    lines = remainder.strip().splitlines()
    agent_state = lines[0].strip() if lines else "unknown"
    remo_version_line = lines[1].strip() if len(lines) > 1 else "unknown"

    exposures = exposure_doc.get("exposures", []) if isinstance(exposure_doc, dict) else []
    if exposures:
        exposed_block = "\n".join(
            f"               {e.get('host')}: {', '.join(e.get('projects', []))}"
            for e in exposures
        )
    else:
        exposed_block = "               (none)"

    print(f"Connector:     {connector_doc.get('name', name)} ({host.host})")
    print(f"Agent:         {agent_state} (amazon-ssm-agent)")
    print(f"Managed node:  {connector_doc.get('node_id', '?')}")
    print(f"Region:        {connector_doc.get('region', '?')}")
    print(f"Run-as user:   {connector_doc.get('run_as_user', '?')}")
    print(
        f"Document:      {DOCUMENT_NAME} v{connector_doc.get('document_version', DOCUMENT_VERSION)}"
    )
    print(f"remo:          {remo_version_line} at {LAUNCHER_PATH}")
    print(f"Exposed:       {exposed_block.strip()}")
    return 0


def unenroll(
    name: str,
    *,
    purge: bool,
    assume_yes: bool,
    confirm: Callable[[str], bool],
) -> int:
    """Stop and disable the agent and remove local registration material
    (contracts/cli.md). Confirms unless `assume_yes` (Constitution VII)."""
    host = next((h for h in get_known_hosts() if h.name == name), None)
    if host is None:
        raise PreconditionError(f"No environment named '{name}' is registered.")

    if not assume_yes and not confirm(f"Unenroll connector '{name}'?"):
        raise UserAbortedError("Aborted.")

    node_id = "?"
    region = "?"
    try:
        probe_cmd = build_ssh_base_cmd(
            host, extra_opts=["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        )
        probe_cmd.append("sudo -n cat /var/lib/remo-connector/connector.json")
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=20)
        if probe.returncode == 0:
            doc = json.loads(probe.stdout)
            node_id = doc.get("node_id", node_id)
            region = doc.get("region", region)
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
        pass  # best-effort: unenroll proceeds even if the pre-read fails.

    conn = _connection_vars(host)
    # Connection vars via a JSON vars file for the same whitespace-splitting
    # reason as enroll() (no secret here, but mkstemp is 0600 regardless).
    fd, path = tempfile.mkstemp(prefix="remo-connector-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(_connection_extra_vars(conn), fh)
        extra: list[str] = [
            "-e", f"@{path}",
            "-e", f"connector_purge={'true' if purge else 'false'}",
        ]
        rc = run_playbook("ssm_connector_unenroll.yml", extra)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if rc != 0:
        raise OperationFailedError(f"Unenroll failed (playbook rc={rc}).")

    print(
        "Local registration removed. The managed node still exists in your AWS account "
        "until you run:"
    )
    print(f"  aws ssm deregister-managed-instance --region {region} --instance-id {node_id}")
    print(
        "Note: an expired activation does not disconnect a node that already registered; "
        "deregister it explicitly."
    )
    return 0
