"""Business logic for the provider-neutral ``remo add`` / ``remo remove`` commands.

Feature 014-register-ssh-host. Registers a single SSH-reachable environment as a
new registry ``type = "ssh"`` (``access_mode = "direct"``), requiring only SSH
reachability — no hypervisor host access, cloud credentials, or API token. The
SSH port and optional identity are stored in the existing ``KnownHost``
positional fields (``instance_id`` = port, ``region`` = identity), so the
registry format is unchanged.

No Click imports (three-layer architecture): the CLI layer validates the name /
port and forwards clean inputs; this module parses the target, enforces the
collision/update policy, optionally verifies reachability, and writes the entry.
"""

from __future__ import annotations

import subprocess

from remo_cli.core.config import (
    ADDED_HOST_TYPE,
    DEFAULT_ADDED_HOST_USER,
    DEFAULT_SSH_PORT,
)
from remo_cli.core.known_hosts import (
    get_known_hosts,
    remove_known_host,
    save_known_host,
)
from remo_cli.core.output import (
    confirm,
    print_error,
    print_info,
    print_success,
)
from remo_cli.models.host import KnownHost


def _reject_unsafe_field(label: str, value: str) -> None:
    """Reject a registry field that would corrupt the colon-delimited line.

    The known-hosts store is both ``:``-delimited and newline-delimited, so a
    field containing ``:`` shifts every later field on reload and a control
    character (newline/tab/…) can inject or truncate a line. Neither must ever
    be persisted (FR-013).
    """
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError(f"{label} contains control characters")
    if ":" in value:
        raise ValueError(f"{label} must not contain ':'")


def _find_name_conflict(name: str) -> KnownHost | None:
    """Return a registry entry that *name* would collide with, or ``None``.

    Mirrors :func:`~remo_cli.core.known_hosts.resolve_remo_host_by_name`: an
    exact match on any type, or the container part of an incus/proxmox
    ``node/container`` name. This is what makes the FR-010 shadow check as broad
    as the resolver — an added host named ``devbox`` must be refused when an
    incus container ``node/devbox`` already exists.
    """
    hosts = get_known_hosts()
    exact = next((h for h in hosts if h.name == name), None)
    if exact is not None:
        return exact
    for h in hosts:
        if h.type in {"incus", "proxmox"} and "/" in h.name:
            if h.name.split("/", maxsplit=1)[1] == name:
                return h
    return None


# ---------------------------------------------------------------------------
# Target parsing
# ---------------------------------------------------------------------------


def parse_ssh_target(
    target: str,
    user_override: str | None = None,
    port_override: int | None = None,
) -> tuple[str, str, int]:
    """Parse ``[user@]host[:port]`` into ``(user, host, port)``.

    ``user_override`` / ``port_override`` (from ``--user`` / ``--port``) win over
    values embedded in *target*. When no user is present, the documented default
    (:data:`DEFAULT_ADDED_HOST_USER`) is applied; when no port is present,
    :data:`DEFAULT_SSH_PORT` is used.

    Un-bracketed IPv6 literals (and the bracketed ``[::1]:22`` form, which is out
    of scope for this feature) are rejected so a malformed line is never written.

    Raises
    ------
    ValueError
        With a human-readable reason for any malformed target.
    """
    raw = target.strip()
    if not raw:
        raise ValueError("SSH target must not be empty")

    if "@" in raw:
        user_part, _, rest = raw.partition("@")
        if not user_part:
            raise ValueError(f"'{target}': empty user before '@'")
    else:
        user_part = ""
        rest = raw

    # IPv6: a bracketed form, or more than one colon in the host portion, is an
    # IPv6 literal — reject with guidance (a legal host:port has exactly one ':').
    if rest.startswith("["):
        raise ValueError(
            f"'{target}': IPv6 literals are not supported. "
            "Use a hostname or an '~/.ssh/config' alias."
        )
    if rest.count(":") > 1:
        raise ValueError(
            f"'{target}': IPv6 literals are not supported. "
            "Use a hostname or an '~/.ssh/config' alias."
        )

    if rest.count(":") == 1:
        host_part, _, port_str = rest.partition(":")
        try:
            embedded_port: int | None = int(port_str)
        except ValueError:
            raise ValueError(
                f"'{target}': port '{port_str}' is not a number"
            ) from None
    else:
        host_part = rest
        embedded_port = None

    if not host_part:
        raise ValueError(f"'{target}': missing host")

    user = user_override or user_part or DEFAULT_ADDED_HOST_USER
    if port_override is not None:
        port = port_override
    elif embedded_port is not None:
        port = embedded_port
    else:
        port = DEFAULT_SSH_PORT

    if not (1 <= port <= 65535):
        raise ValueError(f"port {port} is out of range (1-65535)")

    # The user (from --user or user@) and host become colon-delimited registry
    # fields; reject anything that would shift fields or inject a line (FR-013).
    _reject_unsafe_field("user", user)
    _reject_unsafe_field("host", host_part)

    return user, host_part, port


# ---------------------------------------------------------------------------
# Reachability check (FR-014)
# ---------------------------------------------------------------------------


def verify_reachable(host: KnownHost, timeout: int = 10) -> tuple[bool, str | None]:
    """Run a lightweight, non-interactive SSH connectivity probe.

    Builds the SSH argv through :func:`~remo_cli.core.ssh.build_ssh_opts` so the
    added host's port and stored identity are honored, then runs ``ssh … true``
    with ``BatchMode=yes`` (no password prompt). Returns ``(True, None)`` on
    success or ``(False, error)`` on any SSH failure.

    Host-key checking is disabled for the probe (``StrictHostKeyChecking=no`` +
    ``UserKnownHostsFile=/dev/null``): this is a *reachability/auth* check, and
    with ``BatchMode=yes`` an unknown host key would otherwise fail with "Host
    key verification failed" — wrongly reporting a reachable, never-before-seen
    host as unreachable. It deliberately does not pre-seed ``~/.ssh/known_hosts``
    (the first real ``remo shell`` still follows normal host-key behavior).
    """
    from remo_cli.core.ssh import build_ssh_opts

    ssh_opts, ssh_target = build_ssh_opts(host)
    cmd = [
        "ssh",
        *ssh_opts,
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        ssh_target,
        "true",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 5
        )
    except subprocess.TimeoutExpired:
        return False, f"SSH timed out after {timeout + 5}s"
    except OSError as e:
        return False, f"SSH failed: {e}"

    if result.returncode == 0:
        return True, None
    stderr = result.stderr.strip()
    return False, stderr or f"SSH connection failed (exit code {result.returncode})"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


def add(
    *,
    name: str,
    target: str,
    user: str | None = None,
    port: int | None = None,
    identity: str | None = None,
    verify: bool = False,
    assume_yes: bool = False,
) -> int:
    """Register (or update in place) an SSH-reachable host. Returns an exit code.

    * Refuses to overwrite a provider-managed entry of the same name (FR-010).
    * Re-adding an existing added host updates it in place, confirming unless
      *assume_yes* (FR-007).
    * With *verify*, runs a fail-closed reachability check BEFORE writing; on
      failure nothing is registered and a non-zero code is returned (FR-014).
    """
    try:
        eff_user, eff_host, eff_port = parse_ssh_target(target, user, port)
    except ValueError as e:
        print_error(f"Invalid target: {e}")
        return 2

    if identity is not None:
        try:
            _reject_unsafe_field("identity path", identity)
        except ValueError:
            print_error(
                "Invalid --identity: the path must not contain ':' or control "
                "characters (they would corrupt the colon-delimited registry "
                "line). Use an '~/.ssh/config' IdentityFile entry for such a key."
            )
            return 2

    # Whole-registry name-collision check: the registry only dedupes within
    # (type, name), so a cross-type collision must be caught here (FR-010).
    # Mirror resolve_remo_host_by_name's matching — including the incus/proxmox
    # "node/container" short-name — so `add` cannot *shadow* a provider entry
    # that `remo shell <name>` would otherwise resolve to.
    existing = _find_name_conflict(name)
    if existing is not None and existing.type != ADDED_HOST_TYPE:
        print_error(
            f"'{name}' is already registered (provider: {existing.type}). "
            f"'remo add' will not overwrite or shadow a provider-managed entry — "
            f"choose a different name."
        )
        return 1

    is_update = existing is not None
    if is_update and not assume_yes:
        if not confirm(
            f"Update existing added host '{name}' to {eff_user}@{eff_host}:{eff_port}?",
            default=True,
        ):
            print_info("Aborted; no changes made.")
            return 1

    entry = KnownHost(
        type=ADDED_HOST_TYPE,
        name=name,
        host=eff_host,
        user=eff_user,
        instance_id=str(eff_port),
        access_mode="direct",
        region=identity or "",
    )

    if verify:
        print_info(f"Verifying SSH reachability of {eff_user}@{eff_host}:{eff_port}...")
        ok, err = verify_reachable(entry)
        if not ok:
            print_error(
                f"SSH reachability check failed for {eff_user}@{eff_host}:{eff_port}:\n"
                f"  {err}\n"
                f"  Nothing was registered (omit --verify to register without checking)."
            )
            return 1
        print_success("SSH reachability check passed.")

    save_known_host(entry)

    verb = "Updated" if is_update else "Registered"
    print_success(f"{verb} '{name}' as {eff_user}@{eff_host}:{eff_port} (SSH user: {eff_user}).")
    print_info(f"Connect with:  remo shell {name}")
    return 0


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


def remove(*, name: str, assume_yes: bool = False) -> int:
    """Deregister a manually-added SSH host — local registry delete only.

    Makes no connection to and no change on the remote environment (FR-008).
    Refuses to act on a provider-managed host (FR-009).
    """
    existing = next((h for h in get_known_hosts() if h.name == name), None)
    if existing is None:
        print_error(
            f"No added host named '{name}' found in the registry. "
            f"(list with 'remo shell' to see registered environments)"
        )
        return 1

    if existing.type != ADDED_HOST_TYPE:
        print_error(
            f"'{name}' is a provider-managed host (provider: {existing.type}), "
            f"not a manually-added SSH host. Use 'remo {existing.type} destroy' "
            f"to tear it down — 'remo remove' only deregisters 'remo add' hosts."
        )
        return 1

    if not assume_yes:
        if not confirm(
            f"Deregister added host '{name}'? The remote environment is not touched.",
            default=False,
        ):
            print_info("Aborted; no changes made.")
            return 1

    remove_known_host(ADDED_HOST_TYPE, name)
    print_success(
        f"Removed '{name}' from the registry. The remote environment was not modified."
    )
    return 0
