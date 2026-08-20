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

import os
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


#: Jinja delimiters. Registry fields become ``-e name=value`` extra-vars on the
#: ``remo configure`` path, and Ansible templates extra-var values on the
#: CONTROL NODE — so a stored ``{{ lookup('pipe', '…') }}`` would execute
#: locally. Rejecting them at the boundary keeps the registry inert data.
_JINJA_DELIMITERS = ("{{", "}}", "{%", "%}")


def _reject_unsafe_field(label: str, value: str) -> None:
    """Reject a control character or Jinja delimiter in a registry field value.

    The registry (registry.json, format v2) rejects control characters and
    newlines in any string field (data-model.md V2); checking here gives a
    friendlier, ``add``-specific error message before the value is parsed any
    further. Colons are unrestricted in the JSON registry — the previous
    colon-delimited format's positional-overloading problem no longer exists.

    Jinja delimiters are rejected because these fields are passed to
    ``ansible-playbook`` as extra-vars by ``remo configure``; see
    :data:`_JINJA_DELIMITERS`.
    """
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError(f"{label} contains control characters")
    for delimiter in _JINJA_DELIMITERS:
        if delimiter in value:
            raise ValueError(f"{label} contains a Jinja delimiter ({delimiter})")


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

    IPv6 literals are accepted two ways (US2, T020): the OpenSSH-style
    bracketed form ``[v6][:port]`` (e.g. ``[2001:db8::7]:2222``), and a bare
    bracket-less TARGET containing more than one colon, which is treated as
    a host with no port suffix (a legal ``host:port`` has exactly one colon,
    so 2+ colons unambiguously means an un-bracketed IPv6 literal).

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

    if rest.startswith("["):
        closing = rest.find("]")
        if closing == -1:
            raise ValueError(f"'{target}': unmatched '[' in IPv6 literal")
        host_part = rest[1:closing]
        remainder = rest[closing + 1 :]
        if remainder and not remainder.startswith(":"):
            raise ValueError(
                f"'{target}': unexpected characters after ']': {remainder!r}"
            )
        port_str = remainder[1:] if remainder else ""
        if port_str:
            try:
                embedded_port: int | None = int(port_str)
            except ValueError:
                raise ValueError(
                    f"'{target}': port '{port_str}' is not a number"
                ) from None
        else:
            embedded_port = None
    elif rest.count(":") > 1:
        # Bare (bracket-less) IPv6 literal — no port suffix is representable
        # without brackets, so the whole remainder is the host.
        host_part = rest
        embedded_port = None
    elif rest.count(":") == 1:
        host_part, _, port_str = rest.partition(":")
        try:
            embedded_port = int(port_str)
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
    Contrast ``core/web_adopt.py``'s fingerprint-confirmation branch, which
    *does* write known_hosts — there the operator explicitly approved the key.
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
                "Invalid --identity: the path must not contain control "
                "characters. Use an '~/.ssh/config' IdentityFile entry for "
                "such a key."
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
# configure
# ---------------------------------------------------------------------------


def configure(
    *,
    name: str,
    tools_only: tuple[str, ...] = (),
    tools_skip: tuple[str, ...] = (),
    assume_yes: bool = False,
    verbose: bool = False,
) -> None:
    """Install or refresh remo's dev tools on a manually-added SSH host.

    Runs the generic ``ansible/ssh_configure.yml`` play — the same shared
    ``tasks/configure_dev_tools.yml`` role list every provider applies — against
    a registry ``type="ssh"`` entry. This is what installs ``remo-host``, and so
    what makes the host yield session targets in ``remo web`` instead of sitting
    permanently badged ``no_remo_host``.

    Only ``remo_ssh_*`` names are passed as extra-vars, never an ``ansible_*``
    one: extra-vars are the highest-precedence source, so an ``ansible_port``
    emitted here would apply to every host in the run and be unoverridable. The
    playbook owns the mapping onto ``ansible_port`` /
    ``ansible_ssh_private_key_file``, where "no identity" can mean ``omit``
    rather than the wrong key.

    Raises the :mod:`remo_cli.core.errors` taxonomy; never exits.
    """
    from remo_cli.core.ansible_runner import build_configure_extra_vars, run_playbook
    from remo_cli.core.errors import (
        MissingDependencyError,
        OperationFailedError,
        PreconditionError,
        UserAbortedError,
    )
    from remo_cli.core.known_hosts import guard_added_ssh_host_only

    entry = guard_added_ssh_host_only(name)

    if entry.user == "root":
        raise PreconditionError(
            f"'{name}' is registered as root@{entry.host}. remo configures the "
            f"registered account as the workspace user and pins it to UID 1000, "
            f"which would break root. Re-register with a normal user: "
            f"remo add {name} {entry.host} --user <user>"
        )

    # Registry values become extra-vars below, and Ansible templates those on
    # the control node. Entries written before _reject_unsafe_field learned
    # about Jinja are covered by re-checking on this read path.
    for label, value in (
        ("host", entry.host),
        ("user", entry.user),
        ("identity path", entry.ssh_identity or ""),
    ):
        try:
            _reject_unsafe_field(label, value)
        except ValueError as e:
            raise PreconditionError(
                f"Registry entry '{name}' has an unusable {label}: {e}. "
                f"Re-register it with 'remo add'."
            ) from None

    target = f"{entry.user}@{entry.host}:{entry.ssh_port}"

    if not assume_yes and not confirm(
        f"Configure {target}? remo will apt-upgrade the system, install Docker, "
        f"Node.js, zellij and its host tools, and give '{entry.user}' "
        f"passwordless sudo.",
        default=False,
    ):
        raise UserAbortedError("Aborted; nothing was configured.")

    # Same argv builder as `remo shell` (port + identity + IdentitiesOnly), so a
    # reachability or auth problem surfaces as ssh's own stderr rather than an
    # opaque Ansible UNREACHABLE dump — and the pre-flight can never disagree
    # with the run that follows it.
    print_info(f"Checking SSH reachability of {target}...")
    ok, err = verify_reachable(entry)
    if not ok:
        raise PreconditionError(
            f"Cannot reach {target}:\n"
            f"  {err}\n"
            f"  Nothing was configured."
        )

    print_info(f"Configuring {target}...")

    extra_vars: list[str] = [
        "-e",
        f"remo_ssh_host={entry.host}",
        "-e",
        f"remo_ssh_user={entry.user}",
        "-e",
        f"remo_ssh_port={entry.ssh_port}",
    ]
    if entry.ssh_identity:
        extra_vars.extend(["-e", f"remo_ssh_identity={entry.ssh_identity}"])
    else:
        # Deployment-level identity fallback (023): the web service's job
        # runner exports $REMO_SSH_IDENTITY_FILE so an in-container configure
        # authenticates with the service key when the entry stores no
        # identity. Never stored in the registry (a container path would sync
        # to workstations and, under IdentitiesOnly, guarantee auth failure).
        env_identity = os.environ.get("REMO_SSH_IDENTITY_FILE")
        if env_identity:
            try:
                _reject_unsafe_field("identity path", env_identity)
            except ValueError as e:
                raise PreconditionError(
                    f"$REMO_SSH_IDENTITY_FILE has an unusable value: {e}."
                ) from None
            extra_vars.extend(["-e", f"remo_ssh_identity={env_identity}"])
    extra_vars.extend(build_configure_extra_vars(tools_only, tools_skip))

    try:
        rc = run_playbook("ssh_configure.yml", extra_vars, verbose=verbose)
    except FileNotFoundError as e:
        raise MissingDependencyError(
            "ansible-playbook was not found; 'remo configure' runs an Ansible "
            "play against the host. Install it with: "
            "uv tool install --with ansible-core remo"
        ) from e

    if rc != 0:
        raise OperationFailedError(
            f"Failed to configure '{name}' (playbook rc={rc})."
        )

    print_success(f"Configured '{name}'.")
    print_info(f"Connect with:  remo shell {name}")


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
