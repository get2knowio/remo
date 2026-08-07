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
* Non-secret push cache read/write (012 R10) — reused by ``remo web push``.
* Push orchestration (``run_push``, US4).

Credential model (012-web-adopt-pairing)
----------------------------------------

011 sent a static ``REMO_WEB_API_TOKEN`` and saved it (with the URL) for later
``remo web push``. 012 replaces that with an **ephemeral pairing code** minted
by the adopt page: the CLI sends whatever code it is handed as the bearer, and
**nothing durable is persisted** (FR-018/FR-019). Both ``adopt`` and ``push``
resolve URL + code the same way every time (option / env / prompt). When a
setup call returns the dormant ``404`` (the code expired or was rotated by a
page reopen), the CLI tells the operator to reopen the page for a fresh code
(FR-020).

Push delta-cache design (non-secret optimization)
-------------------------------------------------

The service has no registry-read endpoint, so "unchanged since the last push"
is decided workstation-side by a **non-secret** cache
(``~/.config/remo/web-service.json``, ``cache_version: 2``) mapping each
service ``deployment_id`` to ``{instance name -> {fingerprint, host_keys}}`` —
no URL and no code are ever stored. The ``fingerprint`` is a SHA256 over the
canonical sorted-key JSON of the instance's v2 hostEntry (registry-file-v2.md)
and ``host_keys`` are the verified known_hosts lines pushed for that instance.
Any cache without ``cache_version: 2`` (including every pre-015 file, which had
no version field at all) is treated as empty, forcing one full
re-verification push after an upgrade (research R10).

On ``remo web push``, a direct-access instance whose current fingerprint matches
the cache for the service's ``deployment_id`` skips keyscan + authorize
(reported as ``unchanged``) and its cached host-key lines are reused in the
payload — necessary because ``PUT /setup/registry`` replaces the service's
known_hosts wholesale, so every mirrored instance must contribute its lines on
every push. New or changed instances get the full adopt treatment. The full
registry mirror is always PUT regardless (removals propagate; the service
identity is NOT auto-de-authorized on removed instances — that stays a manual,
documented action). The cache is rewritten atomically (0600) only after a
successful PUT.
"""

from __future__ import annotations

import getpass
import hashlib
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

from remo_cli.core import registry
from remo_cli.core.config import (
    DEFAULT_SSH_PORT,
    get_known_hosts_path_readonly,
    get_remo_home_readonly,
)
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

#: Adoption payload schema version this workstation SENDS (specs/015-registry-v2/
#: contracts/mirror-payload-v2.md §2) — the v2 hostEntry shape, no overloaded
#: fields on the wire.
PAYLOAD_VERSION = 2

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
#: Push-only outcome (FR-026): the instance matches the delta cache from the
#: last successful push, so keyscan/authorize were skipped (already adopted).
OUTCOME_UNCHANGED = "unchanged"
#: Push-only outcome (#122): the instance was skipped as ``unchanged``, the
#: service-side verification pass then failed to authenticate to it, and the
#: push re-ran keyscan/authorize for it. See `_repair_auth_failures`.
OUTCOME_REPAIRED = "repaired"

#: The `web.discovery` error code for an instance the service reaches but
#: cannot authenticate to. Matched against the *detail* of a failed
#: verification result because `POST /setup/verify` reports the code there and
#: carries no separate machine-readable field — and a newer CLI has to keep
#: working against an older service, so this cannot become a wire change.
_VERIFY_AUTH_FAILED = "auth_failed"

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
    """401 — legacy auth rejection (012: the setup surface returns 404 instead)."""


class SetupNotFoundError(SetupApiError):
    """404 — dormant setup surface (code expired/rotated / no live session) or wrong URL."""


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


class UnsupportedPayloadVersionError(AdoptError):
    """The service does not advertise support for this workstation's payload
    version (specs/015-registry-v2/contracts/mirror-payload-v2.md §1, FR-021).
    Raised BEFORE any instance processing or mutation — fail truly fast."""


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

    def post_end(self) -> dict[str, Any]:
        """End the pairing session, returning the setup surface to dormant.

        Called once the flow has succeeded (#158). Verify used to end the
        session itself, which severed the self-heal pass that runs after it —
        so the close is now an explicit step. An older service without this
        route answers 404, which the caller treats as "already ended".
        """
        return self._request("POST", "/api/v1/setup/end")

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
                "the service returned HTTP 401. Reopen the adopt page (or the "
                "dashboard's re-sync affordance) to mint a fresh pairing code, "
                "then retry.",
                status=401,
            )
        if status == 404:
            return SetupNotFoundError(
                f"the pairing code is no longer valid — the setup surface at "
                f"{self.base_url} is dormant (HTTP 404). The code may have expired "
                "or been rotated by a page reopen (or the URL is wrong). Reopen "
                "the adopt page (or the dashboard's re-sync affordance) to mint a "
                "fresh code, then retry.",
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
    ``host_keys`` entries and are never key-authorized (FR-012). Registry v2
    entries always carry an explicit, normalized ``access`` of ``"direct"`` or
    ``"ssm"`` (the legacy implicit-empty-access-mode-means-ssm quirk is
    resolved at the accessor boundary — see core/registry.py — so no fallback
    inference is needed here anymore).
    """
    return host.access_mode != "ssm"


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
        "registry": [registry.known_host_to_entry(h) for h in hosts],
        "host_keys": filtered_keys,
    }


def _check_payload_version_supported(status: dict[str, Any]) -> None:
    """FR-021: abort before any mutation if the service doesn't speak our
    payload version. ``payload_versions`` absent on ``GET /setup/status``
    means a pre-015 service, which only ever spoke v1.
    """
    versions = status.get("payload_versions")
    if not isinstance(versions, list):
        versions = [1]
    if PAYLOAD_VERSION not in versions:
        raise UnsupportedPayloadVersionError(
            f"this remo-web deployment only accepts registry payload "
            f"v{max(versions) if versions else 1} — upgrade the remo-web "
            "container image, then re-run the push."
        )


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


def known_hosts_lookup_key(hostname: str, port: int = DEFAULT_SSH_PORT) -> str:
    """Render *hostname* the way OpenSSH records it in ``known_hosts``.

    OpenSSH brackets the host and appends the port for a non-default port
    (``[10.0.0.9]:2222``) and uses the bare host for port 22. Both directions
    matter and neither is forgiving: ``ssh-keygen -F 10.0.0.9`` does not find a
    ``[10.0.0.9]:2222`` record, and ``-F "[10.0.0.5]:22"`` does not find a bare
    ``10.0.0.5`` one. Getting this wrong makes an already-trusted host read as
    untrusted, so an added host on a custom port could never be pushed.
    """
    return hostname if port == DEFAULT_SSH_PORT else f"[{hostname}]:{port}"


def _lookup_trusted_keys(lookup_key: str, known_hosts_file: Path) -> list[tuple[str, str]] | None:
    """Return trusted (type, key) pairs for *lookup_key*, or None if no record.

    *lookup_key* comes from :func:`known_hosts_lookup_key` — a bare hostname for
    port 22, the bracketed ``[host]:port`` form otherwise.

    Uses ``ssh-keygen -F`` so hashed known_hosts entries (HashKnownHosts yes)
    are handled transparently (research R8).
    """
    if not known_hosts_file.exists():
        return None
    try:
        result = subprocess.run(
            ["ssh-keygen", "-F", lookup_key, "-f", str(known_hosts_file)],
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


def _persist_confirmed_host_keys(lines: list[str], trusted_store: Path) -> str | None:
    """Append interactively-confirmed *lines* to the workstation's known_hosts.

    Issue #157: confirming a fingerprint used to affect only the *push payload*
    (the service's known_hosts). The workstation's own store was left untouched,
    so the very next step — :func:`authorize_service_key`, which runs ssh with
    ``BatchMode=yes`` — died with "Host key verification failed" and the instance
    was reported unreachable. The operator had just said "yes, I trust this key";
    recording that answer locally is what makes the rest of the push work.

    Returns ``None`` on success (including the no-op case) or a warning string
    the caller should print. A write failure is never fatal: the scanned lines
    are still valid for the payload, so trust is reported either way.

    Deliberately pure Python file I/O — no ``ssh-keygen``/``ssh-keyscan``
    subprocess — so the write cannot fail on a workstation without those tools.
    """
    try:
        existing = trusted_store.read_text() if trusted_store.exists() else ""
    except OSError as e:
        return f"could not read {trusted_store} to record the confirmed host key: {e}"

    already = {line.strip() for line in existing.splitlines() if line.strip()}
    # Verbatim comparison: a record may already exist for *other* key types
    # (the fall-through above), in which case only the missing lines are added.
    missing = [line for line in lines if line.strip() not in already]
    if not missing:
        return None

    payload = "".join(f"{line}\n" for line in missing)
    if existing and not existing.endswith("\n"):
        payload = "\n" + payload

    try:
        trusted_store.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # os.open with mode 0600 so a *newly created* known_hosts is never
        # briefly world-readable (a create-then-chmod would race).
        fd = os.open(trusted_store, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(payload)
    except OSError as e:
        return (
            f"could not record the confirmed host key in {trusted_store}: {e}. "
            "Authorizing the service key over ssh will likely fail until this "
            "host is trusted locally."
        )
    return None


def scan_and_verify_host_key(
    hostname: str,
    *,
    port: int = DEFAULT_SSH_PORT,
    known_hosts_file: Path | None = None,
    interactive: bool = False,
    confirm_fn: Callable[[str], bool] | None = None,
    scan_timeout: float = 20.0,
) -> HostKeyScan:
    """Scan *hostname*'s SSH host keys on *port* and verify them against trust.

    *port* is threaded through to both ``ssh-keyscan -p`` and the known_hosts
    lookup. Without it an added host on a custom port (``remo add … :2222``)
    scanned port 22 instead: at best nothing answered and the instance was
    reported unreachable — so the service key was never authorized — and at
    worst a *different* host answering on 22 had its keys pushed. ``-p`` is
    omitted for port 22 so every provider host's argv is byte-identical to
    before, and ``ssh-keyscan -p`` already emits the bracketed ``[host]:port``
    line form the service needs to match on when it connects.

    Decision table (research R8, clarification Q2):

    * scan failure / timeout      -> ``unreachable``
    * trusted record matches      -> ``trusted`` (scanned lines included)
    * trusted record mismatches   -> ``mismatch`` (push nothing — FR-010)
    * no trusted record:
        interactive TTY           -> SHA256 fingerprint confirmation
                                     (accept -> ``trusted``, decline -> ``no_trust``)
        non-interactive           -> ``no_trust``

    Side effect, confirmation branch only: an accepted fingerprint is also
    appended to *known_hosts_file* (see :func:`_persist_confirmed_host_keys`).
    Nothing else here writes to the workstation's trust store — a match needs no
    write, and mismatch/decline/non-interactive must not create one.
    """
    trusted_store = known_hosts_file or (Path.home() / ".ssh" / "known_hosts")
    if confirm_fn is None:
        confirm_fn = confirm
    lookup_key = known_hosts_lookup_key(hostname, port)

    scan_cmd = ["ssh-keyscan", "-T", "5", "-t", _KEYSCAN_TYPES]
    if port != DEFAULT_SSH_PORT:
        scan_cmd.extend(["-p", str(port)])
    scan_cmd.append(hostname)

    try:
        result = subprocess.run(
            scan_cmd,
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
        if line.strip()
        and not line.strip().startswith("#")
        # only structurally valid known_hosts lines may reach the payload;
        # anything else would bypass the match/mismatch verification below
        and len(line.split()) >= 3
    ]
    scanned_pairs = _parse_known_hosts_pairs("\n".join(scanned_lines))
    if not scanned_pairs:
        stderr_lines = result.stderr.strip().splitlines()
        detail = stderr_lines[-1].strip() if stderr_lines else "no host keys returned by ssh-keyscan"
        return HostKeyScan("unreachable", detail=detail)

    trusted_pairs = _lookup_trusted_keys(lookup_key, trusted_store)
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
                f"no trusted host key for {lookup_key} in {trusted_store} "
                "(non-interactive run; fingerprint confirmation skipped)"
            ),
        )

    print_warning(f"No trusted host key for {lookup_key} in {trusted_store}.")
    print("Scanned key fingerprints:")
    print(_render_fingerprints(scanned_lines))
    if confirm_fn(f"Trust these keys for {lookup_key} and include them in the push?"):
        # Record the answer locally too (#157) — the authorize step that follows
        # runs ssh with BatchMode=yes and fails outright on an untrusted host.
        warning = _persist_confirmed_host_keys(scanned_lines, trusted_store)
        if warning:
            print_warning(warning)
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
# Best-effort service-key revocation (017 US3, contracts/revocation.md).
# Symmetric to build_authorize_command / authorize_service_key: marker-scoped,
# atomic, idempotent, over the operator's ambient SSH access, never raises.
# ---------------------------------------------------------------------------


def build_revoke_command() -> str:
    """Build the single POSIX-sh command that removes the service line.

    Filters every `` remo-web@`` marker line out of ``~/.ssh/authorized_keys``
    (tolerating a missing file) and writes back via temp-file + ``mv`` at 0600.
    Removes ONLY marker lines — all other authorized keys are preserved. A
    missing file is a success no-op; re-running against an already-revoked
    instance is a byte-level no-op (idempotent).
    """
    quoted_marker = shlex.quote(AUTHORIZED_KEYS_MARKER)
    return (
        "set -e; "
        "umask 077; "
        "[ -f ~/.ssh/authorized_keys ] || exit 0; "
        'tmp="$(mktemp ~/.ssh/.authorized_keys.remo.XXXXXX)"; '
        f'grep -vF {quoted_marker} ~/.ssh/authorized_keys > "$tmp" || true; '
        'chmod 600 "$tmp"; '
        'mv "$tmp" ~/.ssh/authorized_keys'
    )


def revoke_service_key(
    host: KnownHost,
    *,
    timeout: float = 30.0,
) -> tuple[bool, str]:
    """Remove the service's authorization entry from *host* (best-effort).

    Mirrors :func:`authorize_service_key`: ambient SSH access (no identity
    override), ``BatchMode=yes``, bounded timeout. Returns ``(ok, detail)`` and
    never raises for per-instance connection failures — an exit 255 / timeout /
    OSError becomes ``(False, <reason>)`` (contracts/revocation.md).
    """
    cmd = build_ssh_base_cmd(
        host,
        extra_opts=["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"],
    )
    cmd.append(build_revoke_command())

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
# Non-secret push cache (012 R10) — accelerates re-push by skipping keyscan/
# authorize for unchanged instances. Keyed by the service deployment_id; holds
# NO url and NO pairing code (nothing durable is persisted, FR-019).
# ---------------------------------------------------------------------------


@dataclass
class CachedInstance:
    """Per-instance delta-cache entry from the last successful push.

    Beyond the delta fields (``fingerprint`` / ``host_keys``) the entry retains
    a **non-secret connection tuple** (``host`` / ``user`` / ``access`` /
    ``type`` / ``port`` / ``identity``) so a *removed* instance — one no longer
    in the registry, whose connection fields are therefore gone from
    ``get_known_hosts()`` — can still be reached for best-effort ``remo-web@``
    revocation (017 US3, data-model.md §1). ``identity`` is the ssh-type host's
    stored key path (a non-secret filesystem path, empty for every other type),
    without which revoking a custom-key host would fall back to ambient keys and
    fail. All new fields are optional and parsed leniently: an older cache
    missing them simply disables revocation for that instance (reported as
    could-not-be-performed), never an error.
    """

    fingerprint: str
    host_keys: list[str] = field(default_factory=list)
    host: str = ""
    user: str = ""
    access: str = ""
    type: str = ""
    port: int | None = None
    identity: str = ""


def instance_fingerprint(host: KnownHost) -> str:
    """SHA256 over the canonical sorted-key JSON of *host*'s v2 hostEntry.

    Any change to the fields the service mirrors (host, user, access, per-type
    nested fields, …) changes the fingerprint, forcing the full
    keyscan+authorize treatment on the next push. (research R10: replaces the
    legacy 7-field digest.)
    """
    canonical = json.dumps(registry.known_host_to_entry(host), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class DeploymentCache:
    """One deployment's slice of the push cache (cache v3, data-model.md §2).

    Groups the per-instance delta entries under ``instances`` alongside the
    ``mirror_generation`` this workstation last wrote/observed for the
    deployment (used for multi-workstation flap detection, 017 US5).
    """

    instances: dict[str, CachedInstance] = field(default_factory=dict)
    mirror_generation: int = 0


#: Push cache shape: deployment_id -> DeploymentCache.
PushCache = dict[str, "DeploymentCache"]

#: Push-cache file format version. Bumped 2 -> 3 (017-web-adopt-simplify): the
#: per-deployment shape is now ``{mirror_generation, instances}`` and each
#: instance entry carries the non-secret connection tuple. Any other/missing
#: value is treated as an empty cache — a one-time full re-verification push
#: after a format upgrade (FR-026 / research R7), since older entries lack the
#: fields the v3 flow depends on.
PUSH_CACHE_VERSION = 3


def push_cache_path() -> Path:
    """Path of the non-secret push cache (``~/.config/remo/web-service.json``)."""
    return get_remo_home_readonly() / "web-service.json"


def _parse_instances(raw: object) -> dict[str, CachedInstance]:
    """Leniently parse one deployment's ``{name -> instance entry}`` mapping.

    Entries keep backward-lenient parsing: only ``fingerprint`` is required;
    ``host_keys`` and the connection-tuple fields (``host``/``user``/``access``/
    ``type``/``port``) default to empty/None when absent or malformed.
    """
    instances: dict[str, CachedInstance] = {}
    if not isinstance(raw, dict):
        return instances
    for name, entry in raw.items():
        if not (isinstance(name, str) and isinstance(entry, dict)):
            continue
        fingerprint = entry.get("fingerprint")
        host_keys = entry.get("host_keys")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue
        if not (isinstance(host_keys, list) and all(isinstance(k, str) for k in host_keys)):
            host_keys = []
        host = entry.get("host")
        user = entry.get("user")
        access = entry.get("access")
        type_ = entry.get("type")
        port = entry.get("port")
        identity = entry.get("identity")
        instances[name] = CachedInstance(
            fingerprint=fingerprint,
            host_keys=list(host_keys),
            host=host if isinstance(host, str) else "",
            user=user if isinstance(user, str) else "",
            access=access if isinstance(access, str) else "",
            type=type_ if isinstance(type_, str) else "",
            port=port if isinstance(port, int) and not isinstance(port, bool) else None,
            identity=identity if isinstance(identity, str) else "",
        )
    return instances


def _parse_deployment(raw: object) -> DeploymentCache:
    """Parse one deployment's ``{mirror_generation, instances}`` slice (v3)."""
    if not isinstance(raw, dict):
        return DeploymentCache()
    generation = raw.get("mirror_generation")
    if not (isinstance(generation, int) and not isinstance(generation, bool) and generation >= 0):
        generation = 0
    return DeploymentCache(
        instances=_parse_instances(raw.get("instances")),
        mirror_generation=generation,
    )


def load_push_cache() -> PushCache:
    """Load the deployment-keyed push cache, or ``{}`` when absent/unreadable.

    Files written by the 011 credential format (top-level ``url``/``token`` +
    name-keyed ``push_cache``) do not match the deployment-keyed shape and are
    ignored. Files without ``cache_version: 3`` (any pre-017 format or a future
    incompatible one) are also treated as empty (research R7) — the next push
    simply retries in full and the next save overwrites the stale file — no
    secret is ever read.
    """
    path = push_cache_path()
    try:
        parsed = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    if parsed.get("cache_version") != PUSH_CACHE_VERSION:
        return {}
    raw_cache = parsed.get("push_cache")
    if not isinstance(raw_cache, dict):
        return {}
    cache: PushCache = {}
    for deployment_id, raw_deployment in raw_cache.items():
        if not isinstance(deployment_id, str):
            continue
        deployment = _parse_deployment(raw_deployment)
        if deployment.instances or deployment.mirror_generation:
            cache[deployment_id] = deployment
    return cache


def save_push_cache(cache: PushCache) -> Path:
    """Write the push cache to ``push_cache_path()`` atomically with 0600 perms.

    The cache holds no secret (no url, no code), but it is written 0600 anyway
    via temp-file + ``os.replace`` so a crash never leaves a partial file.
    """
    path = push_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "cache_version": PUSH_CACHE_VERSION,
            "push_cache": {
                deployment_id: {
                    "mirror_generation": deployment.mirror_generation,
                    "instances": {
                        name: {
                            "fingerprint": c.fingerprint,
                            "host_keys": c.host_keys,
                            "host": c.host,
                            "user": c.user,
                            "access": c.access,
                            "type": c.type,
                            "port": c.port,
                            "identity": c.identity,
                        }
                        for name, c in deployment.instances.items()
                    },
                }
                for deployment_id, deployment in cache.items()
            }
        },
        indent=2,
    )
    fd, tmp_path = tempfile.mkstemp(prefix=".web-service.", suffix=".json.tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(payload + "\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)
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


# Revocation outcome values (017 US3, data-model.md §5).
REVOKE_OK = "revoked"
REVOKE_FAILED = "could_not_revoke"


@dataclass
class RevocationOutcome:
    """Per removed instance, surfaced in the push summary (data-model.md §5)."""

    name: str
    result: str  # REVOKE_OK | REVOKE_FAILED
    detail: str = ""
    remediation: str = ""


@dataclass
class AdoptResult:
    """Result of a completed adopt flow. Completion (even with per-instance
    skips/flags) maps to CLI exit code 0; hard failures raise AdoptError."""

    outcomes: list[InstanceOutcome]
    verify: dict[str, Any]
    applied: dict[str, Any]
    deployment_id: str
    revocations: list[RevocationOutcome] = field(default_factory=list)

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
            # `KnownHost.ssh_port` is 22 for every provider-managed entry, so
            # this only changes behavior for `remo add` hosts on a custom port.
            port=host.ssh_port,
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
                    "workstation, then re-run `remo web push`."
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
                    "reconnect once to re-trust it, then re-run `remo web push`."
                ),
            )
        if scan.decision == "no_trust":
            return InstanceOutcome(
                host,
                OUTCOME_SKIPPED_NO_TRUST,
                detail=scan.detail,
                remediation=(
                    f"Connect once (e.g. `remo shell`) to trust {host.host}'s key, or "
                    "re-run `remo web push` interactively and confirm the fingerprint."
                ),
            )

        ok, error = authorize_service_key(host, public_key)
        if not ok:
            if "Host key verification failed" in (error or ""):
                # Name the real cause instead of sending the operator to check a
                # host that is demonstrably reachable (#157).
                lookup_key = known_hosts_lookup_key(host.host, host.ssh_port)
                remediation = (
                    f"There is no trusted host key for {lookup_key} in this "
                    "workstation's ~/.ssh/known_hosts, so ssh refused to connect. "
                    f"Connect once (e.g. `remo shell {host.name}`) to trust it, "
                    "then re-run `remo web push`."
                )
            else:
                remediation = (
                    f"Check you can `ssh {host.user}@{host.host}` from this "
                    "workstation, then re-run `remo web push`."
                )
            return InstanceOutcome(
                host,
                OUTCOME_SKIPPED_UNREACHABLE,
                detail=f"host key verified, but authorizing the service key failed: {error}",
                remediation=remediation,
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
                "Re-run `remo web push`; if this persists, re-run with "
                "REMO_VERBOSE=1 and inspect the error."
            ),
        )


def _cache_from_outcomes(
    outcomes: list[InstanceOutcome], host_keys: dict[str, list[str]]
) -> dict[str, CachedInstance]:
    """Build the push delta cache from a completed run (module docstring design).

    A direct-access instance is cached only when it ended ``adopted``,
    ``unchanged`` or ``repaired`` — those are exactly the instances whose host
    keys were verified and whose lines were included in the successful PUT; a
    skipped/flagged direct instance gets no entry so the next push retries it in
    full.

    SSM instances are cached too (they end ``skipped_by_design``) even though
    they carry no keyscan/authorize state: their registry entry IS mirrored on
    every push, so caching their fingerprint lets ``remo web status`` track them
    (otherwise every SSM instance reads as perpetually ``new``) and lets a later
    removal be surfaced. Their ``host_keys`` stays empty, so they never take the
    push fast-path (which is gated on ``is_direct_access``).
    """
    cache: dict[str, CachedInstance] = {}
    for o in outcomes:
        if is_direct_access(o.host):
            if o.outcome not in (OUTCOME_ADOPTED, OUTCOME_UNCHANGED, OUTCOME_REPAIRED):
                continue
        elif o.outcome != OUTCOME_SKIPPED_BY_DESIGN:
            continue
        cache[o.host.name] = CachedInstance(
            fingerprint=instance_fingerprint(o.host),
            host_keys=list(host_keys.get(o.host.name, [])),
            # Non-secret connection tuple retained so a later removal can be
            # reached for best-effort revocation (017 US3, data-model.md §1).
            host=o.host.host,
            user=o.host.user,
            access=o.host.access_mode or "direct",
            type=o.host.type,
            port=(
                o.host.ssh_port
                if o.host.type == "ssh" and o.host.ssh_port != DEFAULT_SSH_PORT
                else None
            ),
            # ssh-type stored key path (empty for every other type); without it
            # revoking a custom-key host falls back to ambient keys and fails.
            identity=o.host.ssh_identity or "",
        )
    return cache


def _update_push_cache(
    deployment_id: str,
    instances: dict[str, CachedInstance],
    mirror_generation: int = 0,
) -> None:
    """Merge one deployment's entry into the on-disk push cache (best-effort).

    ``mirror_generation`` is the generation the service reported after the PUT
    (017 US5) — preserved across writes so the next push can flap-detect. A
    write failure is non-fatal: the cache is only an optimization, so a push
    that cannot persist it still succeeds and simply retries in full next time.
    """
    try:
        cache = load_push_cache()
        cache[deployment_id] = DeploymentCache(
            instances=instances, mirror_generation=mirror_generation
        )
        save_push_cache(cache)
    except OSError as e:
        print_warning(f"could not update the push cache ({push_cache_path()}): {e}")


def _manual_revoke_remediation(target: str) -> str:
    return (
        f"revoke manually by deleting the '{AUTHORIZED_KEYS_MARKER.strip()}...' line "
        f"from ~/.ssh/authorized_keys on {target}."
    )


def _host_from_cache(name: str, cached: CachedInstance) -> KnownHost:
    """Reconstruct a minimal SSH target from a removed instance's cached tuple.

    A removed instance is gone from the registry, so its connection fields must
    come from the push cache (v3). Only the fields revocation needs are
    restored; the ``ssh``-type port round-trips through ``instance_id`` and the
    ``ssh``-type stored identity path round-trips through ``region`` (both empty
    for every other type, so their argv is unchanged) — see
    ``KnownHost.ssh_port`` / ``KnownHost.ssh_identity``.
    """
    return KnownHost(
        type=cached.type or "ssh",
        name=name,
        host=cached.host,
        user=cached.user,
        instance_id=str(cached.port) if (cached.type == "ssh" and cached.port) else "",
        access_mode=cached.access or "direct",
        region=cached.identity,
    )


def _revoke_removed(
    removed: list[str], cached_instances: dict[str, CachedInstance]
) -> list[RevocationOutcome]:
    """Best-effort ``remo-web@`` revocation for each removed instance (US3).

    Direct-access instances with a cached connection tuple are contacted over
    ambient SSH; SSM instances and instances with no cached tuple (older cache)
    are reported ``could_not_revoke`` with manual-removal remediation. Never
    fatal — a failure to revoke never changes the push's exit code (FR-015).
    """
    outcomes: list[RevocationOutcome] = []
    for name in removed:
        cached = cached_instances.get(name)
        if cached is None or not cached.host:
            outcomes.append(
                RevocationOutcome(
                    name,
                    REVOKE_FAILED,
                    detail="no cached connection details for this instance",
                    remediation=_manual_revoke_remediation("the instance"),
                )
            )
            continue
        if cached.access == "ssm":
            outcomes.append(
                RevocationOutcome(
                    name,
                    REVOKE_FAILED,
                    detail="SSM-routed instance (AWS-managed transport)",
                    remediation=_manual_revoke_remediation("the instance via SSM"),
                )
            )
            continue

        host = _host_from_cache(name, cached)
        try:
            ok, detail = revoke_service_key(host)
        except Exception as e:  # noqa: BLE001 — revocation is never fatal (FR-015)
            ok, detail = False, f"unexpected error: {e}"
        if ok:
            outcomes.append(RevocationOutcome(name, REVOKE_OK, detail="service key removed"))
        else:
            outcomes.append(
                RevocationOutcome(
                    name,
                    REVOKE_FAILED,
                    detail=detail,
                    remediation=_manual_revoke_remediation(f"{host.user}@{host.host}"),
                )
            )
    return outcomes


def _workstation_label() -> str:
    """Best-effort, non-authoritative ``hostname/user`` descriptor (US5).

    Sent to the service as informational display text only; the service stores
    it verbatim and never acts on it (FR-027).
    """
    try:
        host = socket.gethostname() or "unknown"
    except OSError:
        host = "unknown"
    user = ""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — getuser() can raise on odd environments
        user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    return f"{host}/{user}" if user else host


def _flap_warning(status: dict[str, Any], cached_generation: int) -> str | None:
    """Return a flap warning when the mirror advanced elsewhere, else None (US5).

    Warn iff the service reports a ``mirror_generation`` (a 017+ service that
    has been pushed to at least once) AND it is greater than the generation this
    workstation last recorded for the deployment. A pre-017 service (no
    generation) or a same-or-older generation is not a flap (contracts/
    setup-status-marker.md).
    """
    server_gen = status.get("mirror_generation")
    if not (isinstance(server_gen, int) and not isinstance(server_gen, bool)):
        return None
    if server_gen <= cached_generation:
        return None
    last_push = status.get("last_push")
    who = ""
    when = ""
    if isinstance(last_push, dict):
        who = str(last_push.get("workstation") or "").strip()
        when = str(last_push.get("at") or "").strip()
    by = f" by {who}" if who else ""
    at = f" at {when}" if when else ""
    return (
        f"the web deployment's mirror was last updated elsewhere (generation "
        f"{server_gen}, this workstation last saw {cached_generation}){by}{at}. "
        "Pushing now overwrites that mirror with this workstation's registry."
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
        color = (
            GREEN
            if o.outcome in (OUTCOME_ADOPTED, OUTCOME_UNCHANGED, OUTCOME_REPAIRED)
            else YELLOW
        )
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


def auth_failed_labels(verify: dict[str, Any]) -> set[str]:
    """Instance labels the service reached but could not authenticate to (#122).

    `POST /setup/verify` reports per-instance checks as ``instance <type>/<name>``
    with the `web.discovery` error code in ``detail``; ``auth_failed`` means the
    service's key is not (or no longer) accepted by that instance's
    ``authorized_keys``, or the host key changed under it.
    """
    labels: set[str] = set()
    results = verify.get("results")
    if not isinstance(results, list):
        return labels
    for result in results:
        if not isinstance(result, dict) or result.get("passed"):
            continue
        name = str(result.get("name", ""))
        if not name.startswith("instance "):
            continue
        if str(result.get("detail") or "").strip() == _VERIFY_AUTH_FAILED:
            labels.add(name.removeprefix("instance "))
    return labels


def _repair_auth_failures(
    outcomes: list[InstanceOutcome],
    verify: dict[str, Any],
    host_keys: dict[str, list[str]],
    *,
    interactive: bool,
    public_key: str,
) -> list[InstanceOutcome]:
    """Re-authorize instances the fast path skipped but verification rejected (#122).

    The ``unchanged`` fast path answers "did this instance's registry entry
    change since our last push?" from the local push cache. That is a correct
    answer to a *different* question than the one that decides whether authorize
    can be skipped — "is the service still authorized on this instance?" — which
    only the host can answer. When the two diverge (the service key is wiped
    host-side, e.g. by a provisioning pass that rewrote ``authorized_keys``),
    push reported ``unchanged`` and repaired nothing, while its own verification
    step printed the failure it had just declined to fix.

    So: treat verification as the authority it is. Any instance skipped as
    ``unchanged`` whose verification came back ``auth_failed`` gets the full
    keyscan/authorize treatment after all — the skip was an optimization whose
    premise verification just disproved. Instances that were actually processed
    this run are left alone: re-running authorize for them would not change the
    outcome, and their failure is a genuine one worth reporting.

    Mutates *outcomes* in place (and *host_keys*, via `_process_instance`) and
    returns the outcomes that were reprocessed.
    """
    failed = auth_failed_labels(verify)
    if not failed:
        return []

    repaired: list[InstanceOutcome] = []
    for index, outcome in enumerate(outcomes):
        if outcome.outcome != OUTCOME_UNCHANGED or outcome.label not in failed:
            continue
        print_warning(
            f"{outcome.label}: the service cannot authenticate to this instance, "
            "but the push skipped it as unchanged — re-authorizing."
        )
        # The cached host-key lines are stale by assumption here: a changed host
        # key is one of the ways an instance reads as auth_failed. Drop them so
        # the rescan's lines are what lands in the mirror.
        host_keys.pop(outcome.host.name, None)
        redone = _process_instance(
            outcome.host,
            public_key,
            interactive=interactive,
            host_keys=host_keys,
        )
        if redone.outcome == OUTCOME_ADOPTED:
            redone = InstanceOutcome(
                redone.host,
                OUTCOME_REPAIRED,
                detail="service key was missing host-side; re-authorized",
            )
        outcomes[index] = redone
        repaired.append(redone)
    return repaired


def render_revocations(revocations: list[RevocationOutcome]) -> None:
    """Render best-effort revocation outcomes for removed instances (FR-018).

    Printed alongside the adoption summary; a ``could_not_revoke`` never changes
    the overall exit code (the push still completes).
    """
    if not revocations:
        return
    print()
    print("Revocation (removed instances):")
    name_width = max(len(r.name) for r in revocations)
    result_width = max(len(r.result) for r in revocations)
    for r in revocations:
        color = GREEN if r.result == REVOKE_OK else YELLOW
        print(
            f"  {r.name:<{name_width}}  "
            f"{color}{r.result:<{result_width}}{NC}  {r.detail}"
        )
        if r.remediation:
            print(f"      -> {r.remediation}")


def render_verification(
    verify: dict[str, Any],
    outcomes: list[InstanceOutcome],
    *,
    service_url: str = "",
) -> None:
    """Render the service-side verification report, annotating FR-014 cases.

    *service_url* lets an ``auth_failed`` line name the exact command that
    repairs it (#122). The service's own remediation cannot: it has no idea what
    URL the operator reaches it on.
    """
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
            if name.startswith("instance ") and detail.strip() == _VERIFY_AUTH_FAILED:
                # This push already re-authorized it once and the service still
                # can't get in, so name the command that redoes the work from
                # scratch rather than leaving the operator to infer it (#122).
                target = f" {service_url}" if service_url else " <service-url>"
                print(f"      still failing after re-authorization — try: remo web push --force{target}")

    if verify.get("all_passed"):
        print_success("All service-side checks passed.")
    else:
        print_warning("Some service-side checks failed (see above).")


def _end_session_best_effort(client: SetupApiClient) -> None:
    """Return the service's setup surface to dormant (#158, FR-007).

    Best effort by design: the flow has already succeeded and the mirror is
    applied, so a failure to close cannot be allowed to fail the push. Only
    `SetupApiError` is swallowed — a 404 from a service that predates
    `/setup/end` (or one that already ended the session on verify) is the
    expected skew case. The idle TTL and the page-hide beacon remain the
    backstop either way.
    """
    try:
        client.post_end()
    except SetupApiError:
        pass


def _run_flow_maybe_tunneled(
    url: str,
    token: str,
    via: str | None,
    verb: str,
    flow: Callable[[SetupApiClient], AdoptResult],
) -> AdoptResult:
    """Run *flow* against a `SetupApiClient`, optionally through a `--via` SSH
    tunnel. A 400/403 seen through the tunnel is remapped to Host-allowlist
    guidance (FR-018); *verb* ("adopting"/"pushing") tailors that message.

    On success — and only on success — the pairing session is ended. A failed
    or aborted flow deliberately leaves it live so the operator can retry with
    the same code instead of minting (and rotating to) a new one.
    """
    if via:
        print_info(f"Opening SSH tunnel via {via}...")
        with open_via_tunnel(via, url) as tunneled_url:
            client = SetupApiClient(tunneled_url, token)
            try:
                result = flow(client)
                # Inside the `with`: the tunnel must still be up for the call.
                _end_session_best_effort(client)
                return result
            except SetupApiError as e:
                if e.status in (400, 403):
                    raise AdoptError(
                        f"the service rejected the tunneled request (HTTP {e.status}) "
                        f"— most likely its Host allowlist. When {verb} through "
                        "--via, the service's REMO_WEB_ALLOWED_HOSTS must include "
                        "127.0.0.1."
                    ) from e
                raise
    client = SetupApiClient(url, token)
    result = flow(client)
    _end_session_best_effort(client)
    return result


# ---------------------------------------------------------------------------
# Unified push orchestration (017 US1) — ONE code path for "adopt on first use,
# re-sync afterwards". `remo web adopt` is a deprecated alias delegating here.
# ---------------------------------------------------------------------------


def run_push(
    url: str,
    token: str,
    *,
    via: str | None = None,
    allow_empty: bool = False,
    assume_yes: bool = False,
    force: bool = False,
    interactive: bool | None = None,
) -> AdoptResult:
    """Run the unified push flow (`remo web push`, 017 US1). ``token`` is a
    pairing code.

    Adopts a not-yet-adopted deployment on first use and re-syncs an
    already-adopted one afterwards — auto-detected from the delta cache being
    empty vs. populated for the deployment; the operator never chooses. URL +
    code are supplied every time (option / env / prompt) — nothing durable is
    saved (FR-018/FR-019). The service's ``deployment_id`` selects the matching
    push-cache entry: instances whose registry entry matches the last successful
    push skip keyscan/authorize (``unchanged``) and reuse their cached host-key
    lines; new/changed instances get the full per-instance treatment; removed
    instances get best-effort ``remo-web@`` revocation. With ``force`` every
    direct-access instance is re-scanned and re-authorized (the ``unchanged``
    fast-path is bypassed, FR-019/FR-020). Raises AdoptError on hard failure;
    returns AdoptResult on completion.
    """
    if interactive is None:
        interactive = sys.stdin.isatty() and not assume_yes
    return _run_flow_maybe_tunneled(
        url,
        token,
        via,
        "pushing",
        # display_url is the URL the operator typed, not client.base_url: under
        # --via the latter is the local tunnel address, which is useless in a
        # "re-run this command" remediation (#122).
        lambda client: _push_flow(
            client,
            allow_empty=allow_empty,
            interactive=interactive,
            force=force,
            display_url=url,
        ),
    )


def run_adopt(
    url: str,
    token: str,
    *,
    via: str | None = None,
    allow_empty: bool = False,
    assume_yes: bool = False,
    force: bool = False,
    interactive: bool | None = None,
) -> AdoptResult:
    """Deprecated alias for :func:`run_push` (017 US1 / FR-008).

    Retained for one release so external callers and the deprecated
    ``remo web adopt`` command keep working; there is no separate adopt code
    path anymore ("adopt vs. re-sync" is auto-detected inside ``run_push``).
    """
    return run_push(
        url,
        token,
        via=via,
        allow_empty=allow_empty,
        assume_yes=assume_yes,
        force=force,
        interactive=interactive,
    )


def _push_flow(
    client: SetupApiClient,
    *,
    allow_empty: bool,
    interactive: bool,
    force: bool = False,
    display_url: str = "",
) -> AdoptResult:
    # Step 1: status precheck (FR-017) — a mount-configured service is read-only.
    # Then the payload-version skew gate (FR-021) — BEFORE any instance
    # processing or mutation.
    status = client.get_status()
    state = str(status.get("state", "unknown"))
    if state == "mount_configured":
        raise MountConfiguredError(_MOUNT_CONFIGURED_MSG)
    _check_payload_version_supported(status)
    print_info(
        f"Service state: {state} "
        f"({status.get('registry_instances', 0)} instances currently registered)"
    )

    # Step 2: service identity + the push cache entry for this deployment.
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

    deployment_cache = load_push_cache().get(deployment_id)
    cached_instances = deployment_cache.instances if deployment_cache else {}
    cached_generation = deployment_cache.mirror_generation if deployment_cache else 0

    # Flap detection (FR-024): the mirror advanced under us since our last push.
    flap = _flap_warning(status, cached_generation)
    if flap is not None:
        print_warning(flap)
        if interactive and not confirm(
            "Push anyway, overwriting the other workstation's mirror?"
        ):
            raise AdoptError(
                "push aborted: the deployment's mirror was updated by another "
                "workstation. Re-run `remo web status`/`push` once you have "
                "reconciled, or pass --yes to overwrite."
            )

    # Step 3: build the mirror from the local registry (FR-008/FR-016).
    hosts = get_known_hosts()
    if not hosts and not allow_empty:
        raise EmptyRegistryError(_empty_registry_message())

    # Step 4: per-instance loop with delta detection. An instance whose
    # fingerprint matches the cache skips keyscan/authorize but its cached
    # host-key lines are REUSED in the payload: PUT /setup/registry replaces the
    # service's known_hosts wholesale, so every mirrored direct-access instance
    # must contribute lines on every push. ``force`` bypasses the fast-path so
    # every direct-access instance is re-scanned and re-authorized (FR-019).
    outcomes: list[InstanceOutcome] = []
    host_keys: dict[str, list[str]] = {}
    for host in hosts:
        cached = cached_instances.get(host.name)
        if (
            not force
            and is_direct_access(host)
            and cached is not None
            and cached.fingerprint == instance_fingerprint(host)
            and cached.host_keys
        ):
            host_keys[host.name] = list(cached.host_keys)
            outcomes.append(
                InstanceOutcome(
                    host,
                    OUTCOME_UNCHANGED,
                    detail="unchanged since last push; keyscan/authorize skipped",
                )
            )
            continue
        print_info(f"Processing {host.type}/{host.name} ({host.host})...")
        outcomes.append(
            _process_instance(
                host,
                public_key,
                interactive=interactive,
                host_keys=host_keys,
            )
        )

    # Instances the last push knew but the mirror no longer contains. Revocation
    # is deferred until AFTER the PUT below (017 US3): we only de-authorize an
    # instance once the mirror removal is actually committed, so a failed PUT can
    # never leave a de-authorized instance still listed in the service mirror.
    removed = sorted(set(cached_instances) - {h.name for h in hosts})

    # Step 5: always PUT the full mirror (removals propagate). The workstation
    # label is informational display text for the deployment's flap marker (US5).
    payload = build_adoption_payload(hosts, host_keys, allow_empty=True)
    payload["workstation"] = _workstation_label()
    applied = client.put_registry(payload, allow_empty=allow_empty)
    print_success(
        f"Registry pushed: {applied.get('registry_instances', len(hosts))} instances, "
        f"host keys for {applied.get('host_key_instances', len(host_keys))}."
    )

    # Step 5b: now that the mirror no longer lists them, best-effort revoke the
    # service's authorized_keys entry on each removed instance (never fatal).
    revocations = _revoke_removed(removed, cached_instances)

    # Step 6: service-side verification (FR-014).
    print_info("Running service-side verification...")
    verify = client.post_verify()

    # Step 6b (#122): self-heal. Verification is the only observer of whether
    # the service is still authorized on an instance; the push cache only knows
    # what this workstation last *sent*. Where the two disagree, re-authorize
    # the instances the fast path skipped, then re-PUT (a repaired instance may
    # carry rescanned host-key lines) and re-verify so the report reflects the
    # repaired state rather than the state that triggered it.
    #
    # Everything from here on is best-effort: the mirror is already applied, so
    # a failure in the repair round must not abort the run before step 7 writes
    # the cache. Losing that write is what turned #158 into a phantom
    # "another workstation" flap on the following push.
    repaired = _repair_auth_failures(
        outcomes, verify, host_keys, interactive=interactive, public_key=public_key
    )
    repair_put_failed = False
    if repaired:
        payload = build_adoption_payload(hosts, host_keys, allow_empty=True)
        payload["workstation"] = _workstation_label()
        try:
            # `applied` is reassigned ONLY on success: the service bumps the
            # mirror generation on every PUT, so caching a generation from a
            # failed call would mis-arm the next push's flap detection.
            applied = client.put_registry(payload, allow_empty=allow_empty)
        except SetupApiError as e:
            repair_put_failed = True
            print_warning(
                f"Could not re-push the mirror after repair: {e}. The repaired "
                "instances will be re-scanned and re-authorized in full on the "
                "next `remo web push`."
            )
        else:
            print_info("Re-running service-side verification after repair...")
            try:
                verify = client.post_verify()
            except SetupApiError as e:
                print_warning(
                    f"Could not re-run service-side verification after repair: {e}. "
                    "The report below predates the repair."
                )

    # Step 7: only after a successful PUT, rewrite the delta cache for this
    # deployment (removed instances drop out; skipped/flagged instances get no
    # entry so the next push retries them in full). Record the generation the
    # service just returned so the next push can flap-detect (US5).
    #
    # An instance the service still cannot authenticate to is dropped from the
    # cache as well (#122): caching it would make it eligible for the very
    # `unchanged` fast path that let the breakage persist, so the next push
    # would skip it again. A known-failed instance always gets retried in full.
    returned_generation = applied.get("mirror_generation")
    new_generation = (
        returned_generation
        if isinstance(returned_generation, int) and not isinstance(returned_generation, bool)
        else cached_generation
    )
    if deployment_id:
        cache_entries = _cache_from_outcomes(outcomes, host_keys)
        still_failing = auth_failed_labels(verify)
        for outcome in outcomes:
            if outcome.label in still_failing:
                cache_entries.pop(outcome.host.name, None)
            elif repair_put_failed and outcome.outcome == OUTCOME_REPAIRED:
                # The service never received this instance's rescanned host-key
                # lines, so it is not really in sync. Caching it would re-arm
                # the `unchanged` fast path that hid the breakage in the first
                # place. (When only the re-verify failed, the stale report's
                # own auth_failed labels prune these above — conservative on
                # purpose: an instance we cannot confirm is never cached.)
                cache_entries.pop(outcome.host.name, None)
        _update_push_cache(deployment_id, cache_entries, new_generation)

    render_summary(outcomes)
    render_revocations(revocations)
    render_verification(verify, outcomes, service_url=display_url or client.base_url)

    return AdoptResult(
        outcomes=outcomes,
        verify=verify,
        applied=applied,
        deployment_id=deployment_id,
        revocations=revocations,
    )
