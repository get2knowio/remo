"""Business logic for ``remo local`` — this machine as a remo host (spec 029).

No Click, no ``sys.exit``: failures raise the ``core/errors.py`` taxonomy and
the CLI layer's one boundary maps them to exit codes.

Setup reuses the very Ansible tasks that provision a remote host's scripts
(``ansible/local_setup.yml`` -> ``roles/user_setup/tasks/host_scripts.yml``),
run with a local connection and no privilege escalation. It installs nothing
system-wide, creates no users and never edits a shell rc file (FR-012);
prerequisites are *detected*, never installed (FR-011).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from remo_cli.core import registry
from remo_cli.core.ansible_runner import run_playbook
from remo_cli.core.config import DEFAULT_LOCAL_HOST_NAME, LOCAL_HOST_TYPE
from remo_cli.core.errors import (
    MissingDependencyError,
    OperationFailedError,
    PreconditionError,
)
from remo_cli.core.host_access import (
    get_host_access,
    local_bin_dir,
    local_env_path,
)
from remo_cli.core.known_hosts import find_remo_host_by_name, get_known_hosts
from remo_cli.core.output import print_info, print_success, print_warning
from remo_cli.core.remo_host_client import (
    RemoHostClientError,
    RemoHostCommandError,
    run_remo_host_json,
)
from remo_cli.core.version import get_current_version
from remo_cli.models.host import KnownHost

Status = Literal["present", "missing", "not_running"]

#: Where ``/etc/os-release`` lives; a module attribute so tests can point it
#: at a fixture for the per-distro install hints.
OS_RELEASE_PATH = Path("/etc/os-release")

DOCKER_INFO_TIMEOUT_S = 10
BASH_PROBE_TIMEOUT_S = 5
MIN_BASH_MAJOR = 4

ZELLIJ_URL = "https://zellij.dev/documentation/installation"
DEVCONTAINER_URL = "https://containers.dev/supporting#devcontainer-cli"
DEACON_URL = "https://github.com/get2knowio/deacon"
ENGINE_START_HINT = (
    "start Docker Desktop / OrbStack / `colima start` / `systemctl start docker`"
)

#: Name -> (brew, apt, dnf, pacman); ``None`` means "no package: use the URL".
_PACKAGES: dict[str, tuple[str | None, str | None, str | None, str | None]] = {
    "bash": ("bash", "bash", "bash", "bash"),
    "git": ("git", "git", "git", "git"),
    "fzf": ("fzf", "fzf", "fzf", "fzf"),
    "zellij": ("zellij", None, "zellij", "zellij"),
    "gh": ("gh", "gh", "gh", "github-cli"),
}


@dataclass(frozen=True)
class Prerequisite:
    name: str
    required: bool
    status: Status
    detail: str
    install_hint: str


@dataclass(frozen=True)
class SetupResult:
    name: str
    changed: bool


# ---------------------------------------------------------------------------
# Prerequisite detection
# ---------------------------------------------------------------------------


def _linux_family() -> str:
    """``apt`` | ``dnf`` | ``pacman`` | ``""`` from /etc/os-release ID/ID_LIKE."""
    try:
        text = OS_RELEASE_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    ids: list[str] = []
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids += value.strip().strip('"').split()
    for ident in ids:
        if ident in ("debian", "ubuntu"):
            return "apt"
        if ident in ("fedora", "rhel", "centos"):
            return "dnf"
        if ident in ("arch", "manjaro"):
            return "pacman"
    return ""


def _install_hint(tool: str, url: str) -> str:
    packages = _PACKAGES.get(tool)
    if platform.system() == "Darwin":
        return f"brew install {packages[0]}" if packages and packages[0] else url
    family = _linux_family()
    if packages and family:
        index = {"apt": 1, "dnf": 2, "pacman": 3}[family]
        package = packages[index]
        if package:
            tool_cmd = {"apt": "sudo apt install", "dnf": "sudo dnf install", "pacman": "sudo pacman -S"}[family]
            return f"{tool_cmd} {package}"
    return url


def _bash_candidates() -> list[str]:
    candidates: list[str] = []
    prefix = os.environ.get("HOMEBREW_PREFIX")
    if prefix:
        candidates.append(f"{prefix}/bin/bash")
    candidates += ["/opt/homebrew/bin/bash", "/usr/local/bin/bash"]
    path_hit = shutil.which("bash")
    if path_hit:
        candidates.append(path_hit)
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if directory:
            candidates.append(os.path.join(directory, "bash"))
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _probe_bash(path: str) -> tuple[int, str] | None:
    """``(major, full version)`` of the bash at *path*, or ``None`` if it is not one."""
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        return None
    try:
        out = subprocess.run(
            [path, "-c", 'printf "%s %s" "${BASH_VERSINFO[0]}" "${BASH_VERSION%%[^0-9.]*}"'],
            capture_output=True,
            text=True,
            timeout=BASH_PROBE_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    major, _, version = out.stdout.strip().partition(" ")
    if out.returncode != 0 or not major.isdigit():
        return None
    return int(major), version


def find_bash() -> tuple[str, str] | None:
    """The first bash >= 4 among the candidates: ``(path, version)``."""
    for candidate in _bash_candidates():
        probed = _probe_bash(candidate)
        if probed is not None and probed[0] >= MIN_BASH_MAJOR:
            return candidate, probed[1]
    return None


def _which_prereq(tool: str, *, required: bool, url: str) -> Prerequisite:
    found = shutil.which(tool)
    if found:
        return Prerequisite(tool, required, "present", found, "")
    return Prerequisite(tool, required, "missing", "not found on PATH", _install_hint(tool, url))


def detect_devcontainer_runtime() -> tuple[str, str] | None:
    """``(runtime, path)`` with the same precedence as
    ``ansible/tasks/resolve_devcontainer_runtime.yml``: deacon, then the
    reference ``devcontainer`` CLI."""
    for runtime in ("deacon", "devcontainer"):
        found = shutil.which(runtime)
        if found:
            return runtime, found
    return None


def _container_engine() -> Prerequisite:
    name = "container engine"
    docker = shutil.which("docker")
    if not docker:
        if platform.system() == "Darwin":
            hint = "brew install --cask docker   (or install OrbStack / colima)"
        else:
            hint = "install Docker Engine: https://docs.docker.com/engine/install/"
        return Prerequisite(name, True, "missing", "docker not found on PATH", hint)
    try:
        info = subprocess.run(
            [docker, "info"],
            capture_output=True,
            timeout=DOCKER_INFO_TIMEOUT_S,
        )
        running = info.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        running = False
    if running:
        return Prerequisite(name, True, "present", f"docker ({docker})", "")
    return Prerequisite(
        name,
        True,
        "not_running",
        f"installed (docker) but not running — {ENGINE_START_HINT}",
        "",
    )


def check_prerequisites() -> list[Prerequisite]:
    """Detect (never install) what local setup needs; pure and PATH-driven."""
    results: list[Prerequisite] = []

    bash = find_bash()
    if bash:
        results.append(Prerequisite("bash >= 4", True, "present", f"{bash[0]} ({bash[1]})", ""))
    else:
        results.append(
            Prerequisite(
                "bash >= 4", True, "missing",
                "no bash >= 4 found (macOS ships 3.2)", _install_hint("bash", "https://www.gnu.org/software/bash/"),
            )
        )

    results.append(_which_prereq("git", required=True, url="https://git-scm.com/downloads"))
    results.append(_which_prereq("zellij", required=True, url=ZELLIJ_URL))
    results.append(_which_prereq("fzf", required=True, url="https://github.com/junegunn/fzf#installation"))

    runtime = detect_devcontainer_runtime()
    if runtime:
        results.append(
            Prerequisite("devcontainer runtime", True, "present", f"{runtime[0]} ({runtime[1]})", "")
        )
    else:
        hint = "brew install deacon  (or: npm install -g @devcontainers/cli)" if platform.system() == "Darwin" else f"{DEACON_URL}  (or: npm install -g @devcontainers/cli)"
        results.append(
            Prerequisite("devcontainer runtime", True, "missing", "neither deacon nor devcontainer on PATH", hint)
        )

    results.append(_container_engine())
    results.append(_which_prereq("gh", required=False, url="https://cli.github.com"))
    return results


def missing_required(results: list[Prerequisite]) -> list[Prerequisite]:
    return [r for r in results if r.required and r.status == "missing"]


def render_prerequisites(results: list[Prerequisite]) -> str:
    """The ``remo local check`` table (contracts/cli-local.md)."""
    rows = []
    for r in results:
        label = r.name if r.required else f"{r.name} (optional)"
        status = r.status.replace("_", " ")
        detail = r.install_hint if r.status == "missing" and r.install_hint else r.detail
        rows.append((label, status, detail))
    w1 = max(len("TOOL"), *(len(r[0]) for r in rows))
    w2 = max(len("STATUS"), *(len(r[1]) for r in rows))
    lines = [f"{'TOOL':<{w1}}  {'STATUS':<{w2}}  DETAIL / INSTALL"]
    lines += [f"{a:<{w1}}  {b:<{w2}}  {c}" for a, b, c in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def get_local_host() -> KnownHost | None:
    """The one ``local`` registry entry, if setup has registered it."""
    return next((h for h in get_known_hosts() if h.type == LOCAL_HOST_TYPE), None)


def _require_local_host() -> KnownHost:
    host = get_local_host()
    if host is None:
        raise PreconditionError(
            "remo local setup has not completed on this machine — run: remo local setup"
        )
    return host


def _normalize_projects_dir(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = Path(os.path.normpath(path))
    if not path.is_dir():
        raise PreconditionError(f"--projects-dir {value!r}: {path} is not a directory")
    return str(path)


def _register(name: str, projects_dir: str) -> bool:
    """Create or update the single local entry. Returns whether it changed."""
    desired = KnownHost(
        type=LOCAL_HOST_TYPE,
        name=name,
        host="localhost",
        user=getpass.getuser(),
        access_mode="direct",
        region=projects_dir,
    )
    current = get_local_host()
    if current == desired:
        return False

    def _mutate(hosts: list[KnownHost]) -> list[KnownHost]:
        # Replace the FIRST local entry (the one get_local_host() returns); a
        # hand-edited extra one is preserved, never silently deleted
        # (contracts/registry-local-entry.md).
        out: list[KnownHost] = []
        replaced = False
        for h in hosts:
            if h.type == LOCAL_HOST_TYPE and not replaced:
                out.append(desired)
                replaced = True
            else:
                out.append(h)
        if not replaced:
            out.append(desired)
        return out

    registry.mutate_registry(_mutate)
    return True


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def _state_signature() -> str:
    """A digest of everything local setup installs, to report a no-op run."""
    digest = hashlib.sha256()
    bin_dir = local_bin_dir()
    paths: list[Path] = []
    if bin_dir.is_dir():
        paths += sorted(
            p
            for p in bin_dir.iterdir()
            if p.is_file()
            and (p.name.startswith("remo-") or p.name in ("project-menu", "project-launch", "devshell"))
        )
    paths += [local_env_path(), Path.home() / ".remo-version"]
    for path in paths:
        digest.update(str(path).encode())
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"<absent>")
    return digest.hexdigest()


def setup(
    *,
    name: str | None = None,
    projects_dir: str | None = None,
    assume_yes: bool = False,
    verbose: bool = False,
) -> SetupResult:
    """Install the host scripts for this machine and register the local host.

    *projects_dir*: ``None`` keeps the registered value, ``""`` clears it, any
    other value sets it. *assume_yes* is accepted for symmetry with the other
    upgrade paths; setup asks nothing today (it never escalates privileges).
    """
    del assume_yes  # nothing to confirm: no sudo, no system-wide change
    results = check_prerequisites()
    absent = missing_required(results)
    if absent:
        print(render_prerequisites(results))
        raise PreconditionError(
            "required tools are missing: "
            + ", ".join(r.name for r in absent)
            + ". Install them (see the table above), then re-run: remo local setup"
        )
    for r in results:
        if r.status == "not_running":
            print_warning(f"{r.name}: {r.detail}")
        elif r.status == "missing":
            print_warning(f"{r.name} not found — optional; {r.install_hint}")

    bash = find_bash()
    assert bash is not None  # guaranteed by the required-tools gate above

    existing = get_local_host()
    effective_name = name or (existing.name if existing else DEFAULT_LOCAL_HOST_NAME)
    clash = find_remo_host_by_name(effective_name)
    if clash is not None and clash.type != LOCAL_HOST_TYPE:
        raise PreconditionError(
            f"'{effective_name}' is already registered (type: {clash.type}). "
            f"Choose a different name: remo local setup --name <name>"
        )
    if projects_dir is None:
        effective_projects = existing.projects_dir if existing else ""
    elif projects_dir == "":
        effective_projects = ""
    else:
        effective_projects = _normalize_projects_dir(projects_dir)

    runtime = detect_devcontainer_runtime()
    before = _state_signature()

    # JSON form: a key=value pair splits at the first space, so a projects
    # folder (or interpreter) path containing one would be truncated.
    play_vars: dict[str, str] = {
        "ansible_python_interpreter": sys.executable,
        "remo_bash_path": bash[0],
        "remo_local_projects_dir": effective_projects,
    }
    if runtime:
        play_vars["devcontainer_runtime"] = runtime[0]
    version = get_current_version()
    if version != "unknown":
        play_vars["remo_version"] = version
    extra_vars = ["-e", json.dumps(play_vars)]

    print_info("Installing remo's host scripts for this machine...")
    previous_become = os.environ.get("ANSIBLE_BECOME")
    os.environ["ANSIBLE_BECOME"] = "False"  # ansible.cfg defaults become=True
    try:
        rc = run_playbook("local_setup.yml", extra_vars, verbose=verbose)
    except FileNotFoundError as e:
        raise MissingDependencyError(
            "ansible-playbook was not found; 'remo local setup' runs an Ansible "
            "play on this machine. Install it with: uv tool install --with ansible-core remo-cli"
        ) from e
    finally:
        if previous_become is None:
            os.environ.pop("ANSIBLE_BECOME", None)
        else:
            os.environ["ANSIBLE_BECOME"] = previous_become
    if rc != 0:
        raise OperationFailedError(
            f"Local setup failed (playbook rc={rc}). Re-run with -v for the full output."
        )

    scripts_changed = _state_signature() != before
    entry_changed = _register(effective_name, effective_projects)
    # A rename leaves no second entry behind: _register replaces the one.
    if scripts_changed or entry_changed:
        print_success(f"Local host '{effective_name}' is set up.")
        print_info(f"Open it with:  remo shell {effective_name}")
    else:
        print_success(f"Already up to date: local host '{effective_name}'.")
    return SetupResult(effective_name, scripts_changed or entry_changed)


# ---------------------------------------------------------------------------
# Project index (the host scripts own the file; we just call remo-host)
# ---------------------------------------------------------------------------


def _run_projects(verb: str, **kwargs: str | None) -> dict:
    host = _require_local_host()
    prefix = get_host_access(host).command_prefix(host, batch=True)
    try:
        return run_remo_host_json(prefix, verb, **kwargs)  # type: ignore[arg-type]
    except RemoHostCommandError as e:
        if e.returncode == 3:
            raise PreconditionError(e.stderr.strip() or str(e)) from e
        raise OperationFailedError(str(e)) from e
    except RemoHostClientError as e:
        raise OperationFailedError(str(e)) from e


def add_project(path: str, name: str | None = None) -> dict:
    absolute = os.path.abspath(os.path.expanduser(path))
    result = _run_projects("projects add", path=absolute, name=name)
    print_success(f"Added '{result.get('name', name)}' → {result.get('path', absolute)}")
    return result


def remove_project(name: str) -> dict:
    result = _run_projects("projects remove", project=name)
    print_success(f"Removed '{name}' (files untouched)")
    return result


def list_projects() -> list[dict]:
    payload = _run_projects("projects list")
    projects = payload.get("projects", [])
    return projects if isinstance(projects, list) else []


def render_projects(projects: list[dict]) -> str:
    rows = []
    for p in projects:
        source = "added" if p.get("source") == "index" else "folder"
        status = str(p.get("status", "ok"))
        if status == "missing":
            status = (
                f"missing — re-add: remo local add <new-path> --name {p.get('name', '')}"
            )
        rows.append((str(p.get("name", "")), str(p.get("path", "")), source, status))
    if not rows:
        return "No projects yet. Add one with: remo local add PATH"
    w1 = max(len("NAME"), *(len(r[0]) for r in rows))
    w2 = max(len("PATH"), *(len(r[1]) for r in rows))
    w3 = max(len("SOURCE"), *(len(r[2]) for r in rows))
    lines = [f"{'NAME':<{w1}}  {'PATH':<{w2}}  {'SOURCE':<{w3}}  STATUS"]
    lines += [f"{a:<{w1}}  {b:<{w2}}  {c:<{w3}}  {d}" for a, b, c, d in rows]
    return "\n".join(lines)


def local_setup_state() -> str:
    """One line for ``remo local check``: is the local host set up?"""
    host = get_local_host()
    marker = Path.home() / ".remo-version"
    version = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if host is None or not version or not local_env_path().is_file():
        return "Local host: not set up (run: remo local setup)"
    return f"Local host: {host.name} (setup complete, scripts {version})"


__all__ = [
    "Prerequisite",
    "SetupResult",
    "add_project",
    "check_prerequisites",
    "detect_devcontainer_runtime",
    "find_bash",
    "get_local_host",
    "list_projects",
    "local_setup_state",
    "missing_required",
    "remove_project",
    "render_prerequisites",
    "render_projects",
    "setup",
]
