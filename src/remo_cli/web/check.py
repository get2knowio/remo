"""`remo web check` diagnostic (T051, FR-046).

Implements the full readiness + reachability diagnostic surfaced by the
`remo web check` CLI command. This is a strict superset of what
`GET /api/v1/ready` checks (see `health.py`):

* Registry readability, SSH identity availability, runtime-directory
  writability, and the `ssh` executable — reused directly from `health.py`'s
  `_check_registry`/`_check_ssh_identity`/`_check_runtime_dir` (same
  underlying logic, no duplication), with richer human-facing detail layered
  on top for CLI display.
* `aws_cli`/`ssm_plugin` executable checks, gated on whether any registered
  instance actually uses SSM access (`GET /ready` can't do this — it never
  reads the registry contents).
* Per-instance reachability + protocol-compatibility: for every registered
  instance, a `remo-host capabilities` round-trip over the same
  `build_ssh_base_cmd` transport the rest of the service uses, with a short
  timeout distinct from `WebSettings.discovery_timeout_s` (this is an
  interactive CLI diagnostic, not a background discovery cycle).

Deliberately never opens an interactive session (FR-046): only the
`capabilities` verb is invoked, never `sessions attach` / `project-launch`.

Redaction note (FR-028): failure detail strings here intentionally avoid
`str(exc)` for SSH-transport-layer failures and any unclassified exception,
since ssh's own stderr can (in edge cases) echo back invocation details that
include the SSM `ProxyCommand`. Detail text for those cases is built from
`web.discovery`'s existing typed classification (`code`/`remediation`) only.
Errors surfaced by `remo-host` itself (`RemoHostCommandError`, malformed
JSON, protocol incompatibility) come from the *remote* script's own stdout/
stderr, which never contains local secrets, so those messages are shown
as-is for actionable debugging.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from remo_cli.core.remo_host_client import (
    IncompatibleProtocolError,
    MalformedResponseError,
    PayloadTooLargeError,
    RemoHostCommandError,
    SshTransportError,
    get_capabilities,
)
from remo_cli.core.config import get_known_hosts_path_readonly
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.models.host import KnownHost
from remo_cli.web import health
from remo_cli.web.config import WebSettings
from remo_cli.web.discovery import (
    _classify_ssh_transport,
    _looks_like_missing_remo_host,
    _read_known_hosts_readonly,
)

__all__ = ["CheckResult", "all_passed", "format_results", "run_checks"]

#: Per-instance reachability timeout. Deliberately short and independent of
#: `WebSettings.discovery_timeout_s` (default 10s): `remo web check` is a
#: synchronous, interactive CLI diagnostic run by an operator, and an
#: offline instance should fail fast rather than stall the whole report.
_INSTANCE_CHECK_TIMEOUT_S = 5.0

_REMEDIATE_REGISTRY_MISSING = (
    "Mount the Remo registry read-only (see docs) at the configured "
    "REMO_HOME / XDG_CONFIG_HOME path."
)
_REMEDIATE_SSH_IDENTITY = (
    "The registry is metadata, not authentication material — mount a "
    "private key read-only (see docs), e.g. via $REMO_WEB_SSH_IDENTITY_FILE "
    "or ~/.ssh/id_ed25519."
)
_REMEDIATE_RUNTIME_DIR = (
    "Mount a writable tmpfs at the configured path (default /run/remo-ssh; see docs)."
)
_REMEDIATE_UPDATE_HOST_TOOLS = "Update this instance's Remo host tools (re-run configure)."
_REMEDIATE_CHECK_REACHABLE = "Check instance is running / reachable."


@dataclass(frozen=True)
class CheckResult:
    """One PASS/FAIL line of `remo web check` output."""

    name: str
    passed: bool
    detail: str
    remediation: str | None = None


def _is_ssm_host(host: KnownHost) -> bool:
    """Mirrors `KnownHost.to_line`'s default: instance_id set + no explicit mode -> ssm."""
    return host.access_mode == "ssm" or bool(host.instance_id and not host.access_mode)


# ---------------------------------------------------------------------------
# Config/environment checks (reuse health.py's underlying logic)
# ---------------------------------------------------------------------------


def _registry_check(hosts: list[KnownHost]) -> CheckResult:
    status = health._check_registry()
    path = get_known_hosts_path_readonly()
    if status == "ok":
        return CheckResult("registry", True, f"readable at {path} ({len(hosts)} instances)")
    if status == "missing":
        return CheckResult("registry", False, f"not found at {path}", _REMEDIATE_REGISTRY_MISSING)
    return CheckResult(
        "registry", False, f"{path} exists but is not readable", _REMEDIATE_REGISTRY_MISSING
    )


def _ssh_identity_check() -> CheckResult:
    status = health._check_ssh_identity()
    if status == "ok":
        return CheckResult("ssh_identity", True, "identity file found")
    return CheckResult("ssh_identity", False, "no SSH private key found", _REMEDIATE_SSH_IDENTITY)


def _runtime_dir_check(control_dir: str) -> CheckResult:
    status = health._check_runtime_dir(control_dir)
    if status == "ok":
        return CheckResult("runtime_dir", True, f"{control_dir} (writable)")
    return CheckResult(
        "runtime_dir", False, f"{control_dir} is not writable", _REMEDIATE_RUNTIME_DIR
    )


def _executable_check(name: str, binary: str) -> CheckResult:
    path = shutil.which(binary)
    if path:
        return CheckResult(name, True, path)
    return CheckResult(
        name,
        False,
        f"'{binary}' not found on PATH",
        f"Install '{binary}' inside the runtime environment.",
    )


# ---------------------------------------------------------------------------
# Per-instance reachability + protocol compatibility
# ---------------------------------------------------------------------------


def _instance_check(host: KnownHost, settings: WebSettings) -> CheckResult:
    name = f"instance {host.type}/{host.name}"
    # Same transport the rest of the service uses (R6): in adopted mode the
    # WebSettings properties resolve to the service identity/known_hosts under
    # web-identity/; in every other mode they are None and the argv is
    # byte-identical to before (FR-005/FR-023).
    ssh_argv_prefix = build_ssh_base_cmd(
        host,
        control_dir=settings.ssh_control_dir,
        identity_file=settings.ssh_identity_file,
        known_hosts_file=settings.ssh_known_hosts_file,
    )

    try:
        capability = get_capabilities(ssh_argv_prefix, timeout=_INSTANCE_CHECK_TIMEOUT_S)
    except IncompatibleProtocolError as exc:
        return CheckResult(name, False, str(exc), _REMEDIATE_UPDATE_HOST_TOOLS)
    except (MalformedResponseError, PayloadTooLargeError) as exc:
        return CheckResult(
            name,
            False,
            f"remo-host on this instance returned an unexpected response: {exc}",
            _REMEDIATE_UPDATE_HOST_TOOLS,
        )
    except RemoHostCommandError as exc:
        if _looks_like_missing_remo_host(exc):
            return CheckResult(name, False, "remo-host not installed", _REMEDIATE_UPDATE_HOST_TOOLS)
        return CheckResult(name, False, str(exc), _REMEDIATE_UPDATE_HOST_TOOLS)
    except SshTransportError as exc:
        _status, code, _retryable, remediation = _classify_ssh_transport(exc)
        return CheckResult(name, False, code, remediation)
    except Exception:  # noqa: BLE001 - one instance's failure must never abort
        # the rest of the check run (mirrors discovery's host-failure
        # isolation), and the exception text is deliberately not surfaced
        # here (see module docstring's redaction note).
        return CheckResult(name, False, "unreachable", _REMEDIATE_CHECK_REACHABLE)

    return CheckResult(
        name, True, f"capabilities OK (protocol_version={capability.protocol_version})"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_checks(
    settings: WebSettings | None = None, *, include_instances: bool = True
) -> list[CheckResult]:
    """Run every `remo web check` diagnostic and return ordered results.

    Never opens an interactive session (FR-046): only `capabilities` is
    invoked against registered instances, never `sessions attach` /
    `project-launch`.

    *include_instances* (default ``True``) runs the per-instance
    reachability/protocol round-trips. Callers that use this purely as a
    *startup gate* for config/mount validity (the Docker entrypoint) pass
    ``False``: an instance that is merely powered off / unreachable must NOT
    block the whole service from starting (FR-006/US1 — unreachable instances
    stay visible with actionable status, they don't gate boot). The config,
    SSH-identity, runtime-dir, and executable checks still run in that mode,
    so genuinely broken config/mounts still fail fast.
    """
    settings = settings or WebSettings()
    hosts = _read_known_hosts_readonly()

    results = [
        _registry_check(hosts),
        _ssh_identity_check(),
        _runtime_dir_check(settings.ssh_control_dir),
        _executable_check("ssh", "ssh"),
    ]

    ssm_hosts = [h for h in hosts if _is_ssm_host(h)]
    if ssm_hosts:
        aws_cli = _executable_check("aws_cli", "aws")
        if aws_cli.passed:
            aws_cli = CheckResult(
                aws_cli.name,
                True,
                f"{aws_cli.detail} ({len(ssm_hosts)} SSM instances registered)",
            )
        results.append(aws_cli)
        results.append(_executable_check("ssm_plugin", "session-manager-plugin"))

    if include_instances:
        for host in hosts:
            results.append(_instance_check(host, settings))

    return results


def all_passed(results: list[CheckResult]) -> bool:
    return all(r.passed for r in results)


def format_results(results: list[CheckResult]) -> str:
    """Render `results` as the CLI's per-check PASS/FAIL report."""
    lines = ["remo web check"]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"  [{status}] {r.name}: {r.detail}")
        if not r.passed and r.remediation:
            lines.append(f"         → {r.remediation}")
    return "\n".join(lines)
