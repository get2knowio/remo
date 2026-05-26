"""Hetzner Cloud provider business logic for remo.

Manages the lifecycle of Hetzner Cloud VMs: create, destroy, and update
(re-configure dev tools).  All functions are pure business logic with no
Click imports; CLI argument handling lives in the ``cli`` layer.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import subprocess

from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.known_hosts import (
    clear_known_hosts_by_type,
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import confirm, print_error, print_info, print_success, print_warning
from remo_cli.core.snapshot import (
    handle_destroy_snapshot_cleanup,
    validate_name as validate_snapshot_name,
)
from remo_cli.providers import broker as broker_helpers
from remo_cli.core.ssh import detect_timezone
from remo_cli.core.validation import build_tool_args, parse_volume_size, validate_name
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost
from remo_cli.models.snapshot import Snapshot, SnapshotStatus


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_hetzner_api_token() -> str:
    """Fetch the Hetzner API token from laptop fnox (FR-006).

    Falls back to ``HETZNER_API_TOKEN`` env var for backward compatibility
    until the next major release; emits a deprecation note when used.
    Returns empty string if neither source has a token.
    """
    try:
        from remo_cli.core import fnox as _fnox_mod  # noqa: PLC0415
        if _fnox_mod.is_installed():
            try:
                return _fnox_mod.get("hetzner_api_token")
            except _fnox_mod.FnoxError:
                pass
    except Exception:  # noqa: BLE001
        pass
    # Backward-compatible fallback (will be removed in 3.0).
    return os.environ.get("HETZNER_API_TOKEN", "")


def _query_hetzner_server_ip(server_name: str) -> str:
    """Query the Hetzner API for the IPv4 address of *server_name*.

    Token from laptop fnox via :func:`_get_hetzner_api_token`. Returns an
    empty string when the token is missing, the API call fails, or no
    matching server is found.
    """
    token = _get_hetzner_api_token()
    if not token:
        return ""

    url = f"https://api.hetzner.cloud/v1/servers?name={server_name}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        servers = data.get("servers", [])
        if servers:
            return (
                servers[0]
                .get("public_net", {})
                .get("ipv4", {})
                .get("ip", "")
            )
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError):
        pass

    return ""


def _lookup_hetzner_host(server_name: str) -> str:
    """Return the registered host (IP) for *server_name*, or empty string."""
    for entry in get_known_hosts(type_filter="hetzner"):
        if entry.name == server_name:
            return entry.host
    return ""


# ---------------------------------------------------------------------------
# Bootstrap-token delivery (Phase 3, US1)
# ---------------------------------------------------------------------------


def _fetch_hetzner_host_keys(server_id: int) -> list[tuple[str, str]]:
    """Return SSH host keys for *server_id* as ``[(algo, base64_key), ...]``.

    Best-effort: returns an empty list when the API token is missing, the
    request fails, or the server resource doesn't expose host keys. Hetzner
    Cloud's v1 API exposes host fingerprints under ``server.host_keys`` on
    the detail endpoint; we also try a couple of nested fallbacks for
    robustness against minor field-name drift across API revisions.
    """
    token = _get_hetzner_api_token()
    if not token:
        return []
    url = f"https://api.hetzner.cloud/v1/servers/{server_id}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, ValueError):
        return []

    server = payload.get("server") or {}
    candidates = (
        server.get("host_keys")
        or (server.get("public_net") or {}).get("host_keys")
        or []
    )
    out: list[tuple[str, str]] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        algo = entry.get("type") or entry.get("algorithm") or ""
        key = entry.get("key") or entry.get("public_key") or ""
        if algo and key:
            out.append((str(algo), str(key)))
    return out


def _verify_ssh_host_key(
    server_ip: str, expected: list[tuple[str, str]]
) -> bool:
    """Verify the live SSH host key on *server_ip* matches one of *expected*.

    Runs ``ssh-keyscan -T 10 -t rsa,ed25519,ecdsa <server_ip>`` and parses
    each ``<host> <algo> <base64-key>`` line. Returns True iff at least one
    scanned ``(algo, key)`` is in *expected*. Returns False on any
    transport error.
    """
    if not expected:
        return False
    try:
        proc = subprocess.run(
            [
                "ssh-keyscan",
                "-T", "10",
                "-t", "rsa,ed25519,ecdsa",
                server_ip,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0 and not proc.stdout:
        return False

    expected_set = {(algo, key) for algo, key in expected}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        algo, key = parts[1], parts[2]
        if (algo, key) in expected_set:
            return True
    return False


def _push_bootstrap_token(
    server_ip: str,
    token: str,
    ssh_user: str = "root",
    server_id: int | None = None,
) -> None:
    """SSH-push the bootstrap token to /etc/remo-broker/bootstrap-token (mode 0400 root).

    Token bytes are piped on stdin so they never appear in argv / ps output.
    Per research R2 + contracts/bootstrap-delivery.md.

    When *server_id* is supplied, fetches the Hetzner-reported SSH host keys
    and verifies the live key matches before pushing — closing the MITM
    window on freshly-allocated public IPs. Falls back to
    ``StrictHostKeyChecking=accept-new`` (with a warning) when the API
    surface doesn't expose host keys.

    Raises :class:`RuntimeError` on SSH failure or when Hetzner-reported
    host keys disagree with the live server.
    """
    if not server_ip:
        raise ValueError("server_ip must be non-empty")
    if not token:
        raise ValueError("bootstrap token must be non-empty")

    remote_cmd = (
        "install -D -m 0400 -o root -g root /dev/stdin "
        "/etc/remo-broker/bootstrap-token"
    )

    expected_keys: list[tuple[str, str]] = []
    if server_id is not None:
        expected_keys = _fetch_hetzner_host_keys(server_id)

    known_hosts_file: str | None = None
    if expected_keys:
        if not _verify_ssh_host_key(server_ip, expected_keys):
            raise RuntimeError(
                "Hetzner-reported host keys do not match live server; "
                "refusing to push bootstrap token. Possible MITM."
            )
        # Materialise the verified keys into a temp known_hosts file so the
        # actual ssh push can run with StrictHostKeyChecking=yes.
        fd, known_hosts_file = tempfile.mkstemp(prefix="remo-hetzner-kh-")
        try:
            with os.fdopen(fd, "w") as fp:
                for algo, key in expected_keys:
                    fp.write(f"{server_ip} {algo} {key}\n")
        except OSError:
            # If we can't write the temp file, fall back to accept-new path.
            try:
                os.unlink(known_hosts_file)
            except OSError:
                pass
            known_hosts_file = None
    else:
        print_warning(
            "Hetzner did not return SSH host keys for this server; falling "
            "back to StrictHostKeyChecking=accept-new for the bootstrap-token "
            "push. Residual MITM risk on a freshly-allocated public IP — "
            "see docs/credential-broker.md threat model."
        )

    if known_hosts_file is not None:
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts_file}",
            "-o", "BatchMode=yes",
            f"{ssh_user}@{server_ip}",
            remote_cmd,
        ]
    else:
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "BatchMode=yes",
            f"{ssh_user}@{server_ip}",
            remote_cmd,
        ]
    try:
        proc = subprocess.run(
            ssh_cmd,
            input=token,
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        if known_hosts_file is not None:
            try:
                os.unlink(known_hosts_file)
            except OSError:
                pass
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(
            f"failed to push bootstrap token to {server_ip}: {stderr}"
        )


def _set_server_label(server_id: int, key: str, value: str) -> None:
    """Set a Hetzner server label for later revocation lookup."""
    token = _get_hetzner_api_token()
    if not token:
        return
    url = f"https://api.hetzner.cloud/v1/servers/{server_id}"
    body = json.dumps({"labels": {key: value}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.URLError:
        # Best-effort. Tag missing → rotation/destroy will fail with a clear message later.
        pass


def _hetzner_server_id(server_name: str) -> int | None:
    token = _get_hetzner_api_token()
    if not token:
        return None
    url = f"https://api.hetzner.cloud/v1/servers?name={server_name}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        servers = data.get("servers", [])
        if servers:
            return int(servers[0].get("id", 0)) or None
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, ValueError):
        return None
    return None


def _deliver_bootstrap_token(
    server_name: str,
    server_ip: str,
    backend: str,
    dev_id: str,
) -> None:
    """Mint a sub-token at the backend, push it to the instance, record the token-id label.

    Best-effort: if the backend isn't configured / minting isn't implemented yet,
    surfaces a warning and returns (the broker simply won't start until the token
    arrives — the configure playbook's `bootstrap_token_file` role will fail loudly).
    """
    try:
        minted = broker_helpers.mint_bootstrap_token(
            backend, instance_id=server_name, dev_id=dev_id
        )
    except NotImplementedError as exc:
        print_warning(
            f"Skipping bootstrap delivery: {exc}. "
            "Broker service will not start until a token is provisioned."
        )
        return
    except broker_helpers.BackendError as exc:
        print_error(f"Bootstrap minting failed: {exc}")
        raise

    token = minted.get("token", "")
    token_id = minted.get("token_id", "")
    if not token:
        raise RuntimeError("mint_bootstrap_token returned no token")

    # Resolve the server id up-front so `_push_bootstrap_token` can fetch
    # Hetzner-reported SSH host keys for verification (Finding 15).
    server_id = _hetzner_server_id(server_name)
    _push_bootstrap_token(server_ip, token, server_id=server_id)

    if token_id and server_id:
        # Hetzner Cloud label keys disallow `:` (validation regex returns
        # HTTP 400). Use underscore form on writes; readers fall back to
        # the legacy colon-delimited key for pre-fix instances.
        _set_server_label(server_id, "remo_bootstrap_token_id", token_id)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create(
    name: str = "",
    server_type: str = "",
    location: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Create a new Hetzner Cloud VM and configure it with dev tools.

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "server name")
    volume_size = parse_volume_size(volume_size)

    print_info("Creating Hetzner VM...")

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"hetzner_server_name={name}"])
    if server_type:
        extra_vars.extend(["-e", f"hetzner_server_type={server_type}"])
    if location:
        extra_vars.extend(["-e", f"hetzner_location={location}"])
    if volume_size:
        extra_vars.extend(["-e", f"hetzner_volume_size={volume_size}"])

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    rc = run_playbook("hetzner_site.yml", extra_vars, verbose=verbose)

    if rc != 0:
        return rc

    # Save to known_hosts on success.
    server_name = name or "remo"
    server_ip = _query_hetzner_server_ip(server_name)

    if server_ip:
        save_known_host(
            KnownHost(
                type="hetzner",
                name=server_name,
                host=server_ip,
                user="remo",
            )
        )

        # Phase 3, US1: deliver bootstrap token to the broker via SSH push.
        # Backend + dev_id selection lands in Phase 5 (`remo init`). Until then,
        # fall back to environment hints / no-op.
        from remo_cli.core.broker_config import get_backend  # noqa: PLC0415
        backend = get_backend()
        dev_id = os.environ.get("REMO_DEV_ID", "") or os.environ.get("USER", "")
        if backend:
            try:
                _deliver_bootstrap_token(server_name, server_ip, backend, dev_id)
            except Exception as exc:  # noqa: BLE001
                print_error(f"bootstrap-token delivery failed: {exc}")
                return 1

    # Print post-create summary.
    print("")
    print_success("==================================================")
    print_success("  Hetzner server created successfully!")
    print_success("==================================================")
    print("")
    print(f"  Name:      {server_name}")
    print(f"  Type:      {server_type or 'cx22'}")
    print(f"  Location:  {location or 'hel1'}")
    print(f"  IP:        {server_ip or 'N/A'}")
    print(f"  Storage:   {volume_size or '10'} GB persistent volume")
    print("")
    print("  Connect:  remo shell")
    print_success("==================================================")
    print("")

    return rc


def destroy(
    name: str = "",
    auto_confirm: bool = False,
    remove_volume: bool = False,
    verbose: bool = False,
    force_broker: bool = False,
) -> int:
    """Destroy a Hetzner Cloud VM.

    Returns the ansible-playbook exit code (0 on success). Exit code 5 if
    broker revocation fails and force_broker is False (FR-020).
    """
    if name:
        validate_name(name, "server name")

    server_name = name or "remo"

    # FR-020: revoke bootstrap token at the backend BEFORE deleting the instance.
    from remo_cli.core import broker_revoke as _broker_revoke  # noqa: PLC0415
    candidate = KnownHost(type="hetzner", name=server_name, host="", user="")
    if not _broker_revoke.revoke_before_destroy(candidate, force=force_broker):
        return 5

    if remove_volume:
        print_warning(
            "WARNING: --remove-volume will destroy all data on the persistent volume!"
        )

    # FR-020 through FR-023: surface remo-managed snapshot images before destroy.
    try:
        _pre = snapshot_list(server_name=server_name)
    except Exception as e:  # noqa: BLE001
        print_warning(
            f"Could not list snapshots before destroy ({e}); "
            f"proceeding without snapshot cleanup."
        )
        _pre = []
    handle_destroy_snapshot_cleanup(
        provider_label="Hetzner",
        instance=server_name,
        snapshots=_pre,
        delete_one=lambda snap: snapshot_delete(
            server_name=server_name,
            snap_name=snap.name,
            auto_confirm=True,
        ),
        auto_confirm=auto_confirm,
        show_status=True,
    )

    if not auto_confirm:
        prompt = f"Destroy Hetzner Cloud server '{server_name}'? This cannot be undone."
        if not confirm(prompt):
            print_info("Aborted.")
            return 0

    print_info(f"Destroying Hetzner VM '{server_name}'...")

    extra_vars: list[str] = []

    if name:
        extra_vars.extend(["-e", f"hetzner_server_name={name}"])

    extra_vars.extend(["-e", f"remove_volume={'true' if remove_volume else 'false'}"])

    rc = run_playbook("hetzner_teardown.yml", extra_vars, verbose=verbose)

    # Remove from known_hosts.
    remove_known_host("hetzner", server_name)

    return rc


def update(
    name: str = "",
    volume_size: str = "",
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    verbose: bool = False,
) -> int:
    """Re-configure dev tools on an existing Hetzner VM.

    When *volume_size* is provided, grow the persistent volume and the
    filesystem first (idempotent — no-op when sizes match).

    Returns the ansible-playbook exit code (0 on success).
    """
    if name:
        validate_name(name, "server name")
    volume_size = parse_volume_size(volume_size)

    server_name = name or "remo"

    # Get server address from known_hosts.
    server_host = _lookup_hetzner_host(server_name)
    if not server_host:
        print_error(f"Server '{server_name}' not found in known_hosts.")
        print("Run 'remo hetzner sync' or 'remo hetzner create' first.")
        sys.exit(1)

    if volume_size:
        print_info(f"Resizing Hetzner volume for '{server_name}' to {volume_size}GB...")
        resize_vars: list[str] = [
            "-e", f"hetzner_server_name={server_name}",
            "-e", f"volume_size={volume_size}",
        ]
        rc = run_playbook("hetzner_resize.yml", resize_vars, verbose=verbose)
        if rc != 0:
            return rc

    print_info(f"Updating Hetzner VM '{server_name}' at {server_host}...")

    extra_vars: list[str] = [
        "-i", f"{server_host},",
        "-e", "ansible_user=remo",
    ]

    extra_vars.extend(build_tool_args(tools_only, tools_skip))

    tz = detect_timezone()
    if tz:
        extra_vars.extend(["-e", f"timezone={tz}"])

    current = get_current_version()
    if current != "unknown":
        extra_vars.extend(["-e", f"remo_version={current}"])

    return run_playbook(
        "hetzner_configure.yml",
        extra_vars,
        verbose=verbose,
    )


def list_hosts() -> None:
    """Print a formatted table of all registered Hetzner VMs."""
    entries = get_known_hosts(type_filter="hetzner")

    print(f"{'NAME':<25} {'HOST':<25} {'SSH COMMAND'}")
    print(f"{'----':<25} {'----':<25} {'-----------'}")

    for entry in entries:
        ssh_cmd = f"ssh {entry.user}@{entry.host}"
        print(f"{entry.name:<25} {entry.host:<25} {ssh_cmd}")

    if not entries:
        print("No Hetzner VMs registered.")
        print("Create one with: remo hetzner create")


def info(name: str = "") -> int:
    """Print detailed information about a Hetzner Cloud server.

    Queries the Hetzner API for the server (type, status, IP) and its
    paired ``<name>-home`` volume (size). Requires ``HETZNER_API_TOKEN``.
    Returns 0 on success or 1 on failure.
    """
    token = _get_hetzner_api_token()
    if not token:
        print_error(
            "Hetzner API token unavailable. Store it via `fnox set hetzner_api_token`."
        )
        return 1

    server_name = name or "remo"

    server_url = f"https://api.hetzner.cloud/v1/servers?name={server_name}"
    server_req = urllib.request.Request(
        server_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(server_req, timeout=15) as resp:
            server_data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print_error(f"Hetzner API request failed: {e}")
        return 1

    servers = server_data.get("servers", [])
    if not servers:
        print_error(f"No Hetzner server found with name '{server_name}'.")
        return 1

    server = servers[0]
    server_type = server.get("server_type") or {}
    public_net = server.get("public_net") or {}
    ipv4 = (public_net.get("ipv4") or {}).get("ip", "")
    location = (server.get("datacenter") or {}).get("location", {}).get("name", "")

    volume_name = f"{server_name}-home"
    volume_url = f"https://api.hetzner.cloud/v1/volumes?name={volume_name}"
    volume_req = urllib.request.Request(
        volume_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    volume_size = ""
    try:
        with urllib.request.urlopen(volume_req, timeout=15) as resp:
            volume_data = json.loads(resp.read().decode())
        volumes = volume_data.get("volumes", [])
        if volumes:
            volume_size = f"{volumes[0].get('size', '?')} GB"
    except urllib.error.URLError:
        # Volume lookup is best-effort; don't fail the whole info call.
        pass

    print("")
    print(f"  Name:          {server.get('name', server_name)}")
    print(f"  Server ID:     {server.get('id', '?')}")
    print(f"  State:         {server.get('status', 'unknown')}")
    print(f"  Type:          {server_type.get('name', '?')}")
    print(f"  Location:      {location or '?'}")
    print(f"  Public IPv4:   {ipv4 or '(unavailable)'}")
    print(f"  Cores:         {server_type.get('cores', '?')}")
    print(f"  Memory:        {server_type.get('memory', '?')} GB")
    print(f"  Server disk:   {server_type.get('disk', '?')} GB (ephemeral; tied to instance)")
    print(f"  Volume:        {volume_size or '(none attached)'} ({volume_name})")
    print("")

    return 0


def sync() -> None:
    """Discover Hetzner VMs with the ``remo`` label and update the registry.

    Requires the ``HETZNER_API_TOKEN`` environment variable.  Queries the
    Hetzner Cloud API for all servers carrying the ``remo`` label, clears
    existing hetzner entries from the known-hosts registry, and re-registers
    each discovered server.
    """
    token = _get_hetzner_api_token()
    if not token:
        print_error(
            "Hetzner API token unavailable. Store it via `fnox set hetzner_api_token`."
        )
        sys.exit(1)

    url = "https://api.hetzner.cloud/v1/servers?label_selector=remo"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        print_error(f"Failed to query Hetzner API: {exc}")
        sys.exit(1)

    servers = data.get("servers", [])

    clear_known_hosts_by_type("hetzner")

    for server in servers:
        name = server.get("name", "")
        ip = (
            server.get("public_net", {})
            .get("ipv4", {})
            .get("ip", "")
        )
        if name and ip:
            save_known_host(
                KnownHost(
                    type="hetzner",
                    name=name,
                    host=ip,
                    user="remo",
                )
            )
            print_info(f"Registered: {name} ({ip})")

    count = len(servers)
    if count == 0:
        print_warning("No Hetzner VMs with 'remo' label found.")
    else:
        print_success(f"Synced {count} Hetzner VM(s).")


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


_HETZNER_API = "https://api.hetzner.cloud/v1"


def _hetzner_api(
    method: str, path: str, body: dict | None = None, timeout: int = 30
) -> dict:
    """Call the Hetzner Cloud REST API and return the decoded JSON body.

    Raises :class:`RuntimeError` on non-2xx responses or transport errors so
    callers can surface them.
    """
    token = _get_hetzner_api_token()
    if not token:
        raise RuntimeError(
            "Hetzner API token unavailable. Store it via `fnox set hetzner_api_token`."
        )

    url = f"{_HETZNER_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json" if data else "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
            err_msg = err_body.get("error", {}).get("message", str(e))
        except (ValueError, OSError):
            err_msg = str(e)
        raise RuntimeError(
            f"Hetzner API {method} {path} failed: {e.code} {err_msg}"
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hetzner API {method} {path} failed: {e}") from None


def _get_server_by_name(server_name: str) -> dict:
    """Return the Hetzner server record for *server_name*.

    Raises :class:`RuntimeError` if no matching server exists.
    """
    qs = urllib.parse.urlencode({"name": server_name})
    payload = _hetzner_api("GET", f"/servers?{qs}")
    servers = payload.get("servers", [])
    if not servers:
        raise RuntimeError(f"No Hetzner server found named '{server_name}'.")
    return servers[0]


def _parse_hetzner_timestamp(s: str) -> datetime:
    if not s:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    cleaned = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _hetzner_state_to_status(state: str) -> SnapshotStatus:
    if state in {"creating"}:
        return SnapshotStatus.PENDING
    if state == "available":
        return SnapshotStatus.AVAILABLE
    return SnapshotStatus.FAILED


def _list_snapshots_for_server(
    server_id: int, server_name: str
) -> list[Snapshot]:
    """Return remo-managed snapshot images created from *server_id*.

    Scoping by ``remo-source-server-id`` satisfies FR-027; the additional
    ``remo=true`` label satisfies FR-026.
    """
    selector = f"remo=true,remo-source-server-id={server_id}"
    qs = urllib.parse.urlencode(
        {"type": "snapshot", "label_selector": selector}
    )
    payload = _hetzner_api("GET", f"/images?{qs}")
    snapshots: list[Snapshot] = []
    for img in payload.get("images", []):
        labels = img.get("labels", {}) or {}
        user_name = labels.get("remo-snapshot-name") or img.get("description", "")
        size_gb = img.get("image_size") or img.get("disk_size") or 0
        size_bytes = int(size_gb * (1024**3)) if size_gb else None
        snapshots.append(
            Snapshot(
                provider="hetzner",
                instance_name=server_name,
                name=user_name,
                backend_id=str(img.get("id", "")),
                created_at=_parse_hetzner_timestamp(img.get("created", "")),
                size_bytes=size_bytes,
                description=img.get("description", "") or "",
                status=_hetzner_state_to_status(img.get("status", "")),
            )
        )
    return snapshots


def snapshot_create(
    server_name: str, snap_name: str, description: str = ""
) -> int:
    """Create a Hetzner Cloud snapshot of *server_name*.

    Returns 0 once the provider accepts the request (no polling — per FR-004).
    """
    validate_snapshot_name(snap_name)

    try:
        server = _get_server_by_name(server_name)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    if any(s.name == snap_name for s in existing):
        print_error(
            f"Snapshot '{snap_name}' already exists for hetzner instance "
            f"'{server_name}'."
        )
        return 1

    body = {
        "type": "snapshot",
        "description": description or f"remo snapshot of {server_name}",
        "labels": {
            "remo": "true",
            "remo-snapshot-name": snap_name,
            "remo-source-server-id": str(server_id),
        },
    }
    try:
        _hetzner_api("POST", f"/servers/{server_id}/actions/create_image", body)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    print_info(
        f"Snapshot '{snap_name}' creation started for {server_name}. "
        f"This will take several minutes. "
        f"Run `remo hetzner snapshot list {server_name}` to check status."
    )
    return 0


def _wait_for_action(action_id: int, timeout: int = 600) -> bool:
    """Poll a Hetzner action until ``status`` is ``success``.

    Returns True on success, False on timeout/error. Sleeps 5s between polls.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            payload = _hetzner_api("GET", f"/actions/{action_id}")
        except RuntimeError:
            return False
        status = payload.get("action", {}).get("status", "")
        if status == "success":
            return True
        if status in {"error"}:
            return False
        time.sleep(5)
    return False


def snapshot_restore(
    server_name: str, snap_name: str, auto_confirm: bool = False
) -> int:
    """Rebuild *server_name* from snapshot *snap_name*.

    Hetzner's rebuild is atomic from the user's perspective: server ID and
    IP are preserved (FR-013). We poll the rebuild action until success.
    Returns 0 on success, 1 on any failure.
    """
    try:
        server = _get_server_by_name(server_name)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for hetzner instance '{server_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo hetzner snapshot list {server_name}` for status."
        )
        return 1
    if target.status is not SnapshotStatus.AVAILABLE:
        print_error(
            f"Snapshot '{snap_name}' is {target.status.value}; cannot restore."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Restore '{snap_name}' to {server_name}? "
            f"Server will be rebuilt from the snapshot image — "
            f"typically 1-2 minutes of downtime.",
            default=False,
        ):
            print_info("Aborted.")
            return 1

    try:
        payload = _hetzner_api(
            "POST",
            f"/servers/{server_id}/actions/rebuild",
            {"image": int(target.backend_id)},
        )
    except RuntimeError as e:
        print_error(str(e))
        return 1

    action_id = payload.get("action", {}).get("id", 0)
    if not _wait_for_action(action_id):
        print_error(
            f"Rebuild action {action_id} did not complete successfully; "
            f"check the Hetzner Cloud console for details."
        )
        return 1

    print_info(
        f"Restored '{snap_name}' to {server_name}. "
        f"You can reconnect with: remo shell {server_name}"
    )
    return 0


def snapshot_list(server_name: str) -> list[Snapshot]:
    """Return remo-managed snapshots for *server_name*.

    Raises :class:`RuntimeError` if the server cannot be found.
    """
    server = _get_server_by_name(server_name)
    return _list_snapshots_for_server(server.get("id", 0), server_name)


def snapshot_delete(
    server_name: str, snap_name: str, auto_confirm: bool = False
) -> int:
    """Delete the remo-managed Hetzner snapshot image *snap_name*."""
    try:
        server = _get_server_by_name(server_name)
    except RuntimeError as e:
        print_error(str(e))
        return 1

    server_id = server.get("id", 0)
    existing = _list_snapshots_for_server(server_id, server_name)
    target = next((s for s in existing if s.name == snap_name), None)
    if target is None:
        print_error(
            f"Snapshot '{snap_name}' not found for hetzner instance '{server_name}'."
        )
        return 1
    if target.status is SnapshotStatus.PENDING:
        print_error(
            f"Snapshot '{snap_name}' is still pending; "
            f"check `remo hetzner snapshot list {server_name}` for status."
        )
        return 1

    if not auto_confirm:
        if not confirm(
            f"Delete snapshot '{snap_name}' of {server_name}?",
            default=False,
        ):
            print_info("Aborted.")
            return 1

    try:
        _hetzner_api("DELETE", f"/images/{target.backend_id}")
    except RuntimeError as e:
        print_error(str(e))
        return 1

    print_info(f"Deleted snapshot '{snap_name}' of {server_name}.")
    return 0
