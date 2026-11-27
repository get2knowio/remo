"""``remo self-update`` business logic (spec 031, contracts/self-update-cli.md).

Runs the target release's own ``install.sh`` rather than reimplementing the
install: one implementation, and the installer always matches the manifest
format of the release it installs. Typed errors only; the CLI layer owns exit
codes.
"""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from importlib import metadata

from remo_cli.core.errors import OperationFailedError, PreconditionError, UserAbortedError
from remo_cli.core.install_record import (
    InstallOrigin,
    InstallRecord,
    detect_origin,
    load_record,
)
from remo_cli.core.output import confirm as _confirm
from remo_cli.core.output import print_info
from remo_cli.core.version import (
    ReleaseLookupError,
    get_current_version,
    get_latest_github_release,
    version_is_newer,
)

SWITCH_URL = "https://raw.githubusercontent.com/get2knowio/remo/main/install.sh"
_SOURCE_GUIDANCE = "Running from a source checkout: update it with git pull && uv sync"
_PLUGIN_PREFIX = "remo-provider-"
_INSTALLER_ENV_TWINS = frozenset({"REMO_VERSION", "REMO_PRERELEASE", "REMO_WITH", "REMO_WITHOUT"})


def _switch_command(with_names: list[str]) -> str:
    base = f"curl -fsSL {SWITCH_URL} | bash"
    if with_names:
        base += f" -s -- --with {','.join(with_names)}"
    return base


def detect_with_names() -> list[str]:
    """``--with`` names for a PyPI install: installed plugin dists plus ``web``
    when the web extra's FastAPI is importable."""
    names = []
    for dist in metadata.distributions():
        name = str(dist.metadata["Name"] or "").lower().replace("_", "-")
        if name.startswith(_PLUGIN_PREFIX):
            names.append(name[len(_PLUGIN_PREFIX):])
    names = sorted(set(names))
    if importlib.util.find_spec("fastapi") is not None:
        names.append("web")
    return names


def _fetch_installer(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
            return str(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OperationFailedError(f"could not download the installer from {url}: {exc}") from exc


def _installer_args(record: InstallRecord, newest: str) -> list[str]:
    args = ["--prerelease", newest] if record.channel == "prerelease" else ["--version", newest]
    with_names = [*record.extras, *record.plugins]
    if with_names:
        args += ["--with", ",".join(with_names)]
    for pkg in record.with_pkg:
        args += ["--with-pkg", pkg]
    for url in record.with_url:
        args += ["--with-url", url]
    return args


def run_self_update(
    check: bool,
    assume_yes: bool,
    *,
    confirm: Callable[..., bool] = _confirm,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    fetch: Callable[[str], str] = _fetch_installer,
    lookup: Callable[[str, str], str] = get_latest_github_release,
) -> None:
    origin = detect_origin()
    current = get_current_version()

    if origin is InstallOrigin.SOURCE:
        print_info(_SOURCE_GUIDANCE)
        return

    if origin is InstallOrigin.PYPI:
        print_info(
            "This remo was installed from PyPI, which is not where updates are published. "
            "Switch to the installer once:"
        )
        print_info(f"    {_switch_command(detect_with_names())}")
        return

    record, reason = load_record()
    if origin is not InstallOrigin.RECORDED or record is None:
        why = reason or "remo cannot tell how it was installed"
        raise PreconditionError(
            f"{why}.\n"
            f"  Reinstall with the installer: {_switch_command([])}\n"
            f"  Or, from a source checkout: git pull && uv sync"
        )

    try:
        newest = lookup(record.repo, record.channel)
    except ReleaseLookupError as exc:
        raise OperationFailedError(f"Could not look up the newest release: {exc}") from exc

    if not version_is_newer(newest, current):
        print_info(f"remo {current} is the newest {record.channel} release")
        return

    print_info(f"remo {current} → {newest} ({record.channel})")
    if check:
        return

    tag = f"rc-{newest}" if record.channel == "prerelease" else f"v{newest}"
    url = f"https://raw.githubusercontent.com/{record.repo}/{tag}/install.sh"
    args = _installer_args(record, newest)
    shown = "bash install.sh " + " ".join(shlex.quote(a) for a in args)
    if not assume_yes and not confirm(f"Run `{shown}` (from {url})?", default=True):
        raise UserAbortedError("Self-update cancelled.")

    script = fetch(url)
    # The installer's env twins would otherwise override or contradict the
    # flags built from the record: an exported REMO_PRERELEASE=true makes a
    # stable `--version` a refusal, and REMO_WITHOUT strips plugins.
    env = {k: v for k, v in os.environ.items() if k not in _INSTALLER_ENV_TWINS}
    env["REMO_REPO_SLUG"] = record.repo
    path = ""
    try:
        fd, path = tempfile.mkstemp(prefix="remo-install-", suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write(script)
        result = runner(["bash", path, *args], env=env, check=False)
    except OSError as exc:
        # An unwritable TMPDIR or a missing bash: a typed error, not a traceback.
        raise OperationFailedError(f"Could not run the installer: {exc}") from exc
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    if result.returncode != 0:
        raise OperationFailedError(
            f"The installer exited with status {result.returncode}; remo was not updated."
        )
