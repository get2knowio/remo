"""Workstation-side adoption flow for the remo web service (011-web-adopt).

Implements the CLI half of specs/011-web-adopt/contracts/cli-web-adopt.md:

* Setup-API HTTP client over stdlib ``urllib.request`` (research R9) — this
  module must stay importable without the ``web`` extra installed, so it must
  never import anything from the web service package or its optional
  dependencies (stdlib + remo_cli.core/models only).
* Adoption payload builder (full registry mirror, FR-008/FR-012/FR-016).
* Host-key scan + workstation trust verification (research R8, FR-009/FR-010).
* Idempotent ``authorized_keys`` management on instances (research R7, FR-011).
* ``--via`` SSH local-forward tunnel helper (research R9, FR-018).
* Adopt orchestration (contract flow steps 1-7, FR-013/FR-014/FR-015/FR-017).
* Saved-credentials read/write (research R10, FR-025) — reused by
  ``remo web push`` (US4).
"""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from remo_cli.core.config import get_known_hosts_path_readonly, get_remo_home_readonly
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.output import (
    GREEN,
    NC,
    RED,
    YELLOW,
    confirm,
    print_info,
    print_success,
    print_warning,
)
from remo_cli.core.ssh import build_ssh_base_cmd
from remo_cli.models.host import KnownHost

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Adoption payload schema version (contracts/setup-api.md).
PAYLOAD_VERSION = 1

#: Default service port assumed by --via when the target URL names none.
DEFAULT_SERVICE_PORT = 8080

#: Key types requested from ssh-keyscan (research R8).
_KEYSCAN_TYPES = "ed25519,ecdsa,rsa"

#: The authorized_keys idempotence marker (research R7). Every line containing
#: this substring is filtered out before the current service key is appended,
#: so re-runs are byte-level no-ops and a stale entry from a previous
#: deployment id is replaced rather than accumulated.
AUTHORIZED_KEYS_MARKER = " remo-web@"

# Per-instance outcome values (data-model.md: AdoptionRunOutcome).
OUTCOME_ADOPTED = "adopted"
OUTCOME_SKIPPED_UNREACHABLE = "skipped_unreachable"
OUTCOME_SKIPPED_BY_DESIGN = "skipped_by_design"
OUTCOME_SKIPPED_NO_TRUST = "skipped_no_trust"
OUTCOME_SECURITY_FLAGGED = "security_flagged"

_MOUNT_CONFIGURED_MSG = (
    "this deployment is configured via read-only mounts (the registry and SSH "
    "identity are provided by the operator), so adoption does not apply. "
    "Update the mounted files to change its configuration."
)

# ---------------------------------------------------------------------------
# Typed errors (T015). All hard failures derive from AdoptError; the CLI maps
# any AdoptError to exit code 1 (contracts/cli-web-adopt.md exit codes).
# ---------------------------------------------------------------------------


class AdoptError(Exception):
    """Hard failure: the adopt/push flow could not complete (CLI exit 1)."""


class SetupApiError(AdoptError):
    """An HTTP-level failure talking to the setup API."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class SetupAuthError(SetupApiError):
    """401 — the service rejected the API token."""


class SetupNotFoundError(SetupApiError):
    """404 — setup surface disabled (no token configured) or wrong URL."""


class MountConfiguredError(SetupApiError):
    """409 mount_configured — read-only deployment, adoption does not apply (FR-017)."""


class PayloadRejectedError(SetupApiError):
    """422 — the service rejected the pushed payload."""

    def __init__(self, message: str, *, reason: str = "invalid_payload") -> None:
        super().__init__(message, status=422)
        self.reason = reason


class SetupConnectionError(SetupApiError):
    """The service could not be reached at all (DNS, refused, timeout)."""


class EmptyRegistryError(AdoptError):
    """Local registry is empty and --allow-empty was not given (FR-016)."""


class TunnelError(AdoptError):
    """The --via SSH tunnel could not be established (FR-018)."""


# ---------------------------------------------------------------------------
# T015 — Setup-API HTTP client (stdlib urllib.request, research R9)
# ---------------------------------------------------------------------------


def _normalize_base_url(url: str) -> str:
    url = url.strip()
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


class SetupApiClient:
    """Minimal JSON client for ``/api/v1/setup/*`` (contracts/setup-api.md)."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 15.0,
        verify_timeout: float = 300.0,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.token = token
        self.timeout = timeout
        # POST /setup/verify runs per-instance round-trips server-side and may
        # take ~5s per unreachable instance; give it a generous budget.
        self.verify_timeout = verify_timeout

    # -- public API --------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/setup/status")

    def get_identity(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/setup/identity")

    def put_registry(self, payload: dict[str, Any], allow_empty: bool = False) -> dict[str, Any]:
        query = "?allow_empty=true" if allow_empty else ""
        return self._request(
            "PUT", f"/api/v1/setup/registry{query}", body=payload, timeout=60.0
        )

    def post_verify(self) -> dict[str, Any]:
        return self._request("POST", "/api/v1/setup/verify", timeout=self.verify_timeout)

    # -- internals ----------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as e:
            raise self._map_http_error(e) from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            reason = getattr(e, "reason", None) or e
            raise SetupConnectionError(
                f"could not reach the service at {self.base_url}: {reason}"
            ) from e

        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            raise SetupApiError(
                f"the service at {self.base_url} returned a non-JSON response for "
                f"{method} {path} — is this really a remo web service?"
            ) from e
        if not isinstance(parsed, dict):
            raise SetupApiError(
                f"unexpected response shape from {method} {path}: expected a JSON object"
            )
        return parsed

    def _map_http_error(self, error: urllib.error.HTTPError) -> SetupApiError:
        status = error.code
        reason = ""
        detail = ""
        try:
            parsed = json.loads(error.read())
            if isinstance(parsed, dict):
                reason = str(parsed.get("reason", "") or "")
                detail = str(parsed.get("detail", "") or "")
        except (json.JSONDecodeError, OSError, ValueError):
            pass

        if status == 401:
            return SetupAuthError(
                "the service rejected the API token (HTTP 401). Check the token "
                "against the service's REMO_WEB_API_TOKEN and try again.",
                status=401,
            )
        if status == 404:
            return SetupNotFoundError(
                f"setup API not found at {self.base_url} (HTTP 404). Either the URL "
                "is wrong, or the service has no REMO_WEB_API_TOKEN configured — "
                "without a token the setup surface is disabled.",
                status=404,
            )
        if status == 409:
            return MountConfiguredError(_MOUNT_CONFIGURED_MSG, status=409)
        if status == 422:
            if reason == "empty_registry":
                return PayloadRejectedError(
                    "the service refused an empty registry (HTTP 422). If this "
                    "workstation's empty registry is really what the service should "
                    "mirror, re-run with --allow-empty — but check you are not on "
                    "the wrong workstation first.",
                    reason="empty_registry",
                )
            return PayloadRejectedError(
                f"the service rejected the pushed payload (HTTP 422, "
                f"{reason or 'invalid_payload'}): {detail or 'no detail provided'}",
                reason=reason or "invalid_payload",
            )
        message = detail or reason or (error.reason if isinstance(error.reason, str) else "")
        return SetupApiError(
            f"unexpected HTTP {status} from {self.base_url}: {message or 'no detail'}",
            status=status,
        )


# ---------------------------------------------------------------------------
# T016 — Adoption payload builder (FR-008 / FR-012 / FR-016)
# ---------------------------------------------------------------------------


def is_direct_access(host: KnownHost) -> bool:
    """True when the entry is reached over plain SSH (not SSM-routed).

    SSM entries appear in the pushed ``registry`` mirror but must never carry
    ``host_keys`` entries and are never key-authorized (FR-012).
    """
    return host.access_mode != "ssm"


def _registry_entry(host: KnownHost) -> dict[str, str]:
    return {
        "type": host.type,
        "name": host.name,
        "host": host.host,
        "user": host.user,
        "instance_id": host.instance_id,
        "access_mode": host.access_mode,
        "region": host.region,
    }


def build_adoption_payload(
    hosts: list[KnownHost],
    host_keys: dict[str, list[str]] | None = None,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Build the full-mirror ``AdoptionPayload`` body (data-model.md).

    ``host_keys`` maps registry entry *name* -> verified known_hosts lines.
    Entries are defensively filtered to direct-access registry names so an
    SSM entry can never carry host keys (FR-012) and no key can reference a
    name absent from the registry.
    """
    if not hosts and not allow_empty:
        raise EmptyRegistryError(_empty_registry_message())

    direct_names = {h.name for h in hosts if is_direct_access(h)}
    filtered_keys = {
        name: lines
        for name, lines in (host_keys or {}).items()
        if name in direct_names and lines
    }
    return {
        "version": PAYLOAD_VERSION,
        "registry": [_registry_entry(h) for h in hosts],
        "host_keys": filtered_keys,
    }


def _empty_registry_message() -> str:
    return (
        f"the local registry ({get_known_hosts_path_readonly()}) is empty. "
        "Refusing to push: an empty mirror would wipe the service's instance "
        "list, and an empty registry usually means you are on the wrong "
        "workstation. Re-run with --allow-empty if this is really intended."
    )


# ---------------------------------------------------------------------------
# T017 — Host-key scan + trust verification (research R8, FR-009/FR-010)
# ---------------------------------------------------------------------------

TrustDecision = Literal["trusted", "no_trust", "mismatch", "unreachable"]


@dataclass
class HostKeyScan:
    """Result of scanning one direct-access instance and checking local trust."""

    decision: TrustDecision
    lines: list[str] = field(default_factory=list)
    detail: str = ""


def _parse_known_hosts_pairs(text: str) -> list[tuple[str, str]]:
    """Extract (key_type, key_material) pairs from known_hosts-format text.

    Comment lines and blanks are skipped. The host field may be hashed
    (``|1|...``) — it is ignored; only key type + material are compared.
    """
    pairs: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 3:
            pairs.append((fields[1], fields[2]))
    return pairs


def _lookup_trusted_keys(hostname: str, known_hosts_file: Path) -> list[tuple[str, str]] | None:
    """Return trusted (type, key) pairs for *hostname*, or None if no record.

    Uses ``ssh-keygen -F`` so hashed known_hosts entries (HashKnownHosts yes)
    are handled transparently (research R8).
    """
    if not known_hosts_file.exists():
        return None
    try:
        result = subprocess.run(
            ["ssh-keygen", "-F", hostname, "-f", str(known_hosts_file)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    pairs = _parse_known_hosts_pairs(result.stdout)
    return pairs or None


def _render_fingerprints(lines: list[str]) -> str:
    """Render SHA256 fingerprints for scanned key lines via ``ssh-keygen -lf``."""
    fd, tmp_path = tempfile.mkstemp(prefix="remo-adopt-keys-", suffix=".pub")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        result = subprocess.run(
            ["ssh-keygen", "-lf", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        rendered = result.stdout.strip()
        return rendered or "\n".join(lines)
    except (OSError, subprocess.TimeoutExpired):
        return "\n".join(lines)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def scan_and_verify_host_key(
    hostname: str,
    *,
    known_hosts_file: Path | None = None,
    interactive: bool = False,
    confirm_fn: Callable[[str], bool] | None = None,
    scan_timeout: float = 20.0,
) -> HostKeyScan:
    """Scan *hostname*'s SSH host keys and verify them against local trust.

    Decision table (research R8, clarification Q2):

    * scan failure / timeout      -> ``unreachable``
    * trusted record matches      -> ``trusted`` (scanned lines included)
    * trusted record mismatches   -> ``mismatch`` (push nothing — FR-010)
    * no trusted record:
        interactive TTY           -> SHA256 fingerprint confirmation
                                     (accept -> ``trusted``, decline -> ``no_trust``)
        non-interactive           -> ``no_trust``
    """
    trusted_store = known_hosts_file or (Path.home() / ".ssh" / "known_hosts")
    if confirm_fn is None:
        confirm_fn = confirm

    try:
        result = subprocess.run(
            ["ssh-keyscan", "-T", "5", "-t", _KEYSCAN_TYPES, hostname],
            capture_output=True,
            text=True,
            timeout=scan_timeout,
        )
    except FileNotFoundError:
        return HostKeyScan(
            "unreachable", detail="ssh-keyscan not found on this workstation"
        )
    except subprocess.TimeoutExpired:
        return HostKeyScan(
            "unreachable", detail=f"host key scan timed out after {scan_timeout:.0f}s"
        )
    except OSError as e:
        return HostKeyScan("unreachable", detail=f"host key scan failed: {e}")

    scanned_lines = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    scanned_pairs = _parse_known_hosts_pairs("\n".join(scanned_lines))
    if not scanned_pairs:
        stderr_lines = result.stderr.strip().splitlines()
        detail = stderr_lines[-1].strip() if stderr_lines else "no host keys returned by ssh-keyscan"
        return HostKeyScan("unreachable", detail=detail)

    trusted_pairs = _lookup_trusted_keys(hostname, trusted_store)
    if trusted_pairs is not None:
        trusted_by_type: dict[str, set[str]] = {}
        for key_type, key in trusted_pairs:
            trusted_by_type.setdefault(key_type, set()).add(key)
        overlapping = [(t, k) for t, k in scanned_pairs if t in trusted_by_type]
        if overlapping:
            for key_type, key in overlapping:
                if key not in trusted_by_type[key_type]:
                    return HostKeyScan(
                        "mismatch",
                        detail=(
                            f"scanned {key_type} host key does not match the trusted "
                            f"entry in {trusted_store}"
                        ),
                    )
            return HostKeyScan(
                "trusted",
                lines=scanned_lines,
                detail="matches trusted known_hosts entry",
            )
        # A record exists but only for key types the scan didn't return —
        # nothing comparable, so fall through to the no-trusted-record path.

    if not interactive:
        return HostKeyScan(
            "no_trust",
            detail=(
                f"no trusted host key for {hostname} in {trusted_store} "
                "(non-interactive run; fingerprint confirmation skipped)"
            ),
        )

    print_warning(f"No trusted host key for {hostname} in {trusted_store}.")
    print("Scanned key fingerprints:")
    print(_render_fingerprints(scanned_lines))
    if confirm_fn(f"Trust these keys for {hostname} and include them in the push?"):
        return HostKeyScan(
            "trusted", lines=scanned_lines, detail="fingerprint confirmed interactively"
        )
    return HostKeyScan("no_trust", detail="fingerprint confirmation declined")


# ---------------------------------------------------------------------------
# T018 — Idempotent authorized_keys management (research R7, FR-011)
# ---------------------------------------------------------------------------


def build_authorize_command(public_key: str) -> str:
    """Build the single POSIX-sh command that installs the service key.

    The command (a) filters every existing line containing the
    `` remo-web@`` marker out of ``~/.ssh/authorized_keys`` (tolerating a
    missing file), (b) appends the current service public-key line, and
    (c) writes via temp-file + ``mv`` with 0600 permissions (``~/.ssh``
    ensured 0700). Re-running is a byte-level no-op; a stale entry from a
    previous deployment_id is replaced (rotation).
    """
    key = public_key.strip()
    if not key or "\n" in key or "\r" in key:
        raise ValueError("service public key must be a single non-empty line")
    if len(key.split()) < 2 or not key.startswith(("ssh-", "ecdsa-", "sk-")):
        raise ValueError(f"service public key does not look like an OpenSSH public key: {key!r}")

    quoted_key = shlex.quote(key)
    quoted_marker = shlex.quote(AUTHORIZED_KEYS_MARKER)
    return (
        "set -e; "
        "umask 077; "
        "mkdir -p ~/.ssh; "
        "chmod 700 ~/.ssh; "
        "touch ~/.ssh/authorized_keys; "
        'tmp="$(mktemp ~/.ssh/.authorized_keys.remo.XXXXXX)"; '
        f'grep -vF {quoted_marker} ~/.ssh/authorized_keys > "$tmp" || true; '
        f"printf '%s\\n' {quoted_key} >> \"$tmp\"; "
        'chmod 600 "$tmp"; '
        'mv "$tmp" ~/.ssh/authorized_keys'
    )


def authorize_service_key(
    host: KnownHost,
    public_key: str,
    *,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Install/replace the service's authorization entry on *host*.

    Runs over the user's existing (ambient) SSH access — deliberately NO
    identity_file override. Returns ``(ok, detail)``; never raises for
    per-instance connection failures (FR-013).
    """
    cmd = build_ssh_base_cmd(
        host,
        extra_opts=["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"],
    )
    cmd.append(build_authorize_command(public_key))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"SSH timed out after {timeout:.0f}s"
    except OSError as e:
        return False, f"SSH failed: {e}"

    if result.returncode == 0:
        return True, ""
    stderr = result.stderr.strip()
    if result.returncode == 255:
        return False, stderr or "SSH connection failed (exit code 255)"
    return False, f"remote command failed (exit {result.returncode}): {stderr or 'no stderr'}"


# ---------------------------------------------------------------------------
# T019 — --via SSH tunnel helper (research R9, FR-018)
# ---------------------------------------------------------------------------


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return int(port)


@contextmanager
def open_via_tunnel(
    via_host: str,
    target_url: str,
    *,
    ready_timeout: float = 15.0,
) -> Iterator[str]:
    """Open ``ssh -N -L <free-port>:127.0.0.1:<service-port> <via_host>``.

    Yields the rewritten base URL (``http://127.0.0.1:<free-port>``) once the
    forward accepts connections; guarantees teardown of the ssh process.
    The service port is taken from *target_url* (default 8080).
    """
    parsed = urllib.parse.urlsplit(_normalize_base_url(target_url))
    service_port = parsed.port or DEFAULT_SERVICE_PORT
    local_port = _free_local_port()

    cmd = [
        "ssh",
        "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-L", f"{local_port}:127.0.0.1:{service_port}",
        via_host,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as e:
        raise TunnelError(f"could not start the --via tunnel to {via_host}: {e}") from e

    try:
        deadline = time.monotonic() + ready_timeout
        while True:
            if proc.poll() is not None:
                stderr = proc.stderr.read().strip() if proc.stderr else ""
                raise TunnelError(
                    f"--via tunnel to {via_host} failed: "
                    f"{stderr or f'ssh exited with code {proc.returncode}'}"
                )
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.5):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise TunnelError(
                        f"--via tunnel to {via_host} did not become ready within "
                        f"{ready_timeout:.0f}s"
                    ) from None
                time.sleep(0.2)
        yield f"http://127.0.0.1:{local_port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# Saved adoption credentials (research R10, FR-025) — used at the end of a
# successful adopt and by `remo web push` (US4).
# ---------------------------------------------------------------------------


@dataclass
class SavedCredentials:
    url: str
    token: str
    deployment_id: str


def credentials_path() -> Path:
    """Path of the saved-credentials file (``~/.config/remo/web-service.json``)."""
    return get_remo_home_readonly() / "web-service.json"


def load_saved_credentials() -> SavedCredentials | None:
    """Load saved credentials, or None when absent/unreadable/malformed."""
    path = credentials_path()
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    url = parsed.get("url")
    token = parsed.get("token")
    deployment_id = parsed.get("deployment_id")
    if not (isinstance(url, str) and isinstance(token, str) and isinstance(deployment_id, str)):
        return None
    return SavedCredentials(url=url, token=token, deployment_id=deployment_id)


def save_credentials(credentials: SavedCredentials) -> Path:
    """Write credentials to ``credentials_path()`` with 0600 permissions.

    Only ever called with explicit consent (``--save`` or an interactive
    yes) — FR-025; the caller enforces that.
    """
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "url": credentials.url,
            "token": credentials.token,
            "deployment_id": credentials.deployment_id,
        },
        indent=2,
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload + "\n")
    os.chmod(path, 0o600)  # in case the file pre-existed with wider perms
    return path


# ---------------------------------------------------------------------------
# T020 — Adopt orchestration (contract flow steps 1-7)
# ---------------------------------------------------------------------------


@dataclass
class InstanceOutcome:
    """Per-instance result of an adopt/push run (AdoptionRunOutcome)."""

    host: KnownHost
    outcome: str
    detail: str = ""
    remediation: str = ""

    @property
    def label(self) -> str:
        return f"{self.host.type}/{self.host.name}"


@dataclass
class AdoptResult:
    """Result of a completed adopt flow. Completion (even with per-instance
    skips/flags) maps to CLI exit code 0; hard failures raise AdoptError."""

    outcomes: list[InstanceOutcome]
    verify: dict[str, Any]
    applied: dict[str, Any]
    deployment_id: str

    @property
    def all_verified(self) -> bool:
        return bool(self.verify.get("all_passed"))


def _process_instance(
    host: KnownHost,
    public_key: str,
    *,
    interactive: bool,
    host_keys: dict[str, list[str]],
    known_hosts_file: Path | None = None,
) -> InstanceOutcome:
    """Handle one registry entry: trust-verify + authorize. Never raises (FR-013)."""
    if not is_direct_access(host):
        return InstanceOutcome(
            host,
            OUTCOME_SKIPPED_BY_DESIGN,
            detail="SSM-routed instance (AWS-managed transport)",
            remediation=(
                "No action needed: SSM instances are excluded from host-key and "
                "service-key push by design."
            ),
        )

    try:
        scan = scan_and_verify_host_key(
            host.host,
            known_hosts_file=known_hosts_file,
            interactive=interactive,
        )
        if scan.decision == "unreachable":
            return InstanceOutcome(
                host,
                OUTCOME_SKIPPED_UNREACHABLE,
                detail=scan.detail,
                remediation=(
                    "Check the instance is running and reachable from this "
                    "workstation, then re-run `remo web adopt`."
                ),
            )
        if scan.decision == "mismatch":
            return InstanceOutcome(
                host,
                OUTCOME_SECURITY_FLAGGED,
                detail=scan.detail,
                remediation=(
                    "Do NOT trust this instance until you have investigated. If it "
                    f"was legitimately rebuilt, run `ssh-keygen -R {host.host}`, "
                    "reconnect once to re-trust it, then re-run adopt."
                ),
            )
        if scan.decision == "no_trust":
            return InstanceOutcome(
                host,
                OUTCOME_SKIPPED_NO_TRUST,
                detail=scan.detail,
                remediation=(
                    f"Connect once (e.g. `remo shell`) to trust {host.host}'s key, or "
                    "re-run adopt interactively and confirm the fingerprint."
                ),
            )

        ok, error = authorize_service_key(host, public_key)
        if not ok:
            return InstanceOutcome(
                host,
                OUTCOME_SKIPPED_UNREACHABLE,
                detail=f"host key verified, but authorizing the service key failed: {error}",
                remediation=(
                    f"Check you can `ssh {host.user}@{host.host}` from this "
                    "workstation, then re-run `remo web adopt`."
                ),
            )

        host_keys[host.name] = scan.lines
        return InstanceOutcome(
            host,
            OUTCOME_ADOPTED,
            detail="host key verified; service key authorized",
        )
    except Exception as e:  # noqa: BLE001 — per-instance failures are never fatal (FR-013)
        return InstanceOutcome(
            host,
            OUTCOME_SKIPPED_UNREACHABLE,
            detail=f"unexpected error: {e}",
            remediation=(
                "Re-run `remo web adopt`; if this persists, re-run with "
                "REMO_VERBOSE=1 and inspect the error."
            ),
        )


def render_summary(outcomes: list[InstanceOutcome]) -> None:
    """Render the per-instance summary table (contract output contract)."""
    print()
    print("Adoption summary:")
    if not outcomes:
        print("  (registry is empty — nothing to process)")
        return

    name_width = max(len(o.label) for o in outcomes)
    outcome_width = max(len(o.outcome) for o in outcomes)
    for o in outcomes:
        color = GREEN if o.outcome == OUTCOME_ADOPTED else YELLOW
        line = (
            f"  {o.label:<{name_width}}  "
            f"{color}{o.outcome:<{outcome_width}}{NC}  {o.detail}"
        )
        if o.outcome == OUTCOME_SECURITY_FLAGGED:
            # Prominent MITM warning (FR-010 / output contract).
            line = (
                f"  {RED}{o.label:<{name_width}}  "
                f"{o.outcome:<{outcome_width}}  {o.detail}  "
                f"** WARNING: POTENTIAL MITM — nothing was pushed for this instance **{NC}"
            )
        print(line)
        if o.remediation:
            print(f"      -> {o.remediation}")


def render_verification(verify: dict[str, Any], outcomes: list[InstanceOutcome]) -> None:
    """Render the service-side verification report, annotating FR-014 cases."""
    print()
    print("Service-side verification:")
    results = verify.get("results")
    if not isinstance(results, list) or not results:
        print("  (no verification results returned)")
        return

    adopted_labels = {o.label for o in outcomes if o.outcome == OUTCOME_ADOPTED}
    for result in results:
        if not isinstance(result, dict):
            continue
        name = str(result.get("name", ""))
        passed = bool(result.get("passed"))
        detail = str(result.get("detail") or "")
        remediation = result.get("remediation")
        status = f"{GREEN}PASS{NC}" if passed else f"{RED}FAIL{NC}"
        print(f"  [{status}] {name}: {detail}")
        if not passed:
            instance_label = name.removeprefix("instance ")
            if name.startswith("instance ") and instance_label in adopted_labels:
                # FR-014: the CLI just reached this instance; the service cannot.
                print_warning(
                    "      reachable from workstation but not from the service — "
                    "check the network path from the service container to this "
                    "instance (DNS, routing, firewall)."
                )
            if remediation:
                print(f"      remediation: {remediation}")

    if verify.get("all_passed"):
        print_success("All service-side checks passed.")
    else:
        print_warning("Some service-side checks failed (see above).")


def run_adopt(
    url: str,
    token: str,
    *,
    via: str | None = None,
    allow_empty: bool = False,
    assume_yes: bool = False,
    save: bool = False,
    interactive: bool | None = None,
) -> AdoptResult:
    """Run the full adopt flow (contract steps 1-7). Raises AdoptError on hard
    failure; returns an AdoptResult when the flow completed (CLI exit 0, even
    with per-instance skips/flags)."""
    if interactive is None:
        interactive = sys.stdin.isatty() and not assume_yes

    if via:
        print_info(f"Opening SSH tunnel via {via}...")
        with open_via_tunnel(via, url) as tunneled_url:
            client = SetupApiClient(tunneled_url, token)
            try:
                return _adopt_flow(
                    client,
                    original_url=url,
                    allow_empty=allow_empty,
                    interactive=interactive,
                    save=save,
                )
            except SetupApiError as e:
                if e.status in (400, 403):
                    raise AdoptError(
                        f"the service rejected the tunneled request (HTTP {e.status}) "
                        "— most likely its Host allowlist. When adopting through "
                        "--via, the service's REMO_WEB_ALLOWED_HOSTS must include "
                        "127.0.0.1."
                    ) from e
                raise

    client = SetupApiClient(url, token)
    return _adopt_flow(
        client,
        original_url=url,
        allow_empty=allow_empty,
        interactive=interactive,
        save=save,
    )


def _adopt_flow(
    client: SetupApiClient,
    *,
    original_url: str,
    allow_empty: bool,
    interactive: bool,
    save: bool,
) -> AdoptResult:
    # Step 1: status precheck (FR-017).
    status = client.get_status()
    state = str(status.get("state", "unknown"))
    if state == "mount_configured":
        raise MountConfiguredError(_MOUNT_CONFIGURED_MSG)
    print_info(
        f"Service state: {state} "
        f"({status.get('registry_instances', 0)} instances currently registered)"
    )

    # Step 2: service identity.
    identity = client.get_identity()
    deployment_id = str(identity.get("deployment_id") or "")
    public_key = str(identity.get("public_key") or "")
    if not public_key:
        raise AdoptError(
            "the service returned no public key, so it cannot be authorized on "
            "any instance. The service identity may be missing — check the "
            "service's state volume and logs."
        )
    print_info(f"Service identity: remo-web@{deployment_id or 'unknown'}")

    # Step 3: build the mirror from the local registry (FR-008/FR-016).
    hosts = get_known_hosts()
    if not hosts and not allow_empty:
        raise EmptyRegistryError(_empty_registry_message())

    # Step 4: per-instance loop (FR-009..FR-013), failures never fatal.
    outcomes: list[InstanceOutcome] = []
    host_keys: dict[str, list[str]] = {}
    for host in hosts:
        print_info(f"Processing {host.type}/{host.name} ({host.host})...")
        outcomes.append(
            _process_instance(
                host,
                public_key,
                interactive=interactive,
                host_keys=host_keys,
            )
        )

    # Step 5: push the mirror (guard already applied above).
    payload = build_adoption_payload(hosts, host_keys, allow_empty=True)
    applied = client.put_registry(payload, allow_empty=allow_empty)
    print_success(
        f"Registry pushed: {applied.get('registry_instances', len(hosts))} instances, "
        f"host keys for {applied.get('host_key_instances', len(host_keys))}."
    )

    # Step 6: service-side verification (FR-014).
    print_info("Running service-side verification...")
    verify = client.post_verify()

    render_summary(outcomes)
    render_verification(verify, outcomes)

    # Step 7: saved-credentials offer (FR-025). --save is explicit consent;
    # an interactive yes is explicit consent; --yes alone never saves.
    should_save = save or (
        interactive
        and confirm(
            f"Save service URL and token to {credentials_path()} for `remo web push`?"
        )
    )
    if should_save:
        saved_path = save_credentials(
            SavedCredentials(
                url=original_url, token=client.token, deployment_id=deployment_id
            )
        )
        print_success(f"Credentials saved to {saved_path} (mode 0600).")

    return AdoptResult(
        outcomes=outcomes,
        verify=verify,
        applied=applied,
        deployment_id=deployment_id,
    )
