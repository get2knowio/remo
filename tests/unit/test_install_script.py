"""Tests for install.sh: the curl|bash installer.

Drives the real script rather than re-implementing its logic, so what is
asserted is what a user actually gets. A fake GitHub (a `curl` shim answering
from a fixture tree built by `install_fixtures.py`) and recording `uv`/`pipx`
shims stand in for the network and the tool managers, so full runs (not just
`--dry-run`) are exercised: manifest fetch, checksum verification, the one
install command, the install record.

The bug the original tests exist to prevent is still pinned: `--pre-release`
once ran `uv tool install --prerelease allow`, which resolves against PyPI,
where remo's pre-releases never go (constitution IX), and silently installed the
stable release while reporting success.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.unit import install_fixtures as fx

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
DEV_BUILD_YML = REPO_ROOT / ".github" / "workflows" / "dev-build.yml"


_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _uses_host(texts, host: str) -> bool:
    """Whether any URL in ``texts`` has exactly ``host`` as its hostname.

    Hosts are parsed and compared for equality, never matched by substring.
    """
    return any(
        urlparse(u).hostname == host for t in texts for u in _URL_RE.findall(t)
    )

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

_LEAKY_ENV = (
    "REMO_VERSION", "REMO_PRERELEASE", "REMO_REPO_SLUG", "REMO_WITH", "REMO_WITHOUT",
    "REMO_HOME", "XDG_CONFIG_HOME", "REMO_TEST_FIXTURE_ROOT", "SHIM_LOG",
)

# Everything install.sh needs besides uv/pipx/curl/sha tools, linked into the
# hermetic PATH so a test decides which of the interesting ones exist.
_BASE_TOOLS = (
    "grep", "sed", "awk", "mktemp", "head", "tail", "tr", "sort", "uniq", "cat",
    "rm", "mv", "mkdir", "date", "basename", "dirname", "readlink", "cut", "wc",
    "chmod", "cp", "touch", "ls", "id", "env", "sleep", "printf", "tee",
)

_CURL_SHIM = """#!{py}
import os, pathlib, sys

args = sys.argv[1:]
fail, out, url, i = False, None, None, 0
while i < len(args):
    a = args[i]
    if a == "-o":
        out = args[i + 1]
        i += 2
        continue
    if a.startswith("-") and not a.startswith("--"):
        fail = fail or "f" in a
    elif a.startswith("http"):
        url = a
    i += 1
with open(os.environ["SHIM_LOG"], "a") as log:
    log.write("curl\\t" + str(url) + "\\n")
root = pathlib.Path(os.environ["REMO_TEST_FIXTURE_ROOT"])
data = None
status = 404
if url and url.startswith("https://api.github.com/"):
    if (root / "api" / "rate_limited").exists():
        data = b'{{"message": "API rate limit exceeded for 1.2.3.4."}}'
        status = 403
    elif (root / "api" / "releases.json").exists():
        data = (root / "api" / "releases.json").read_bytes()
        status = 200
elif url and url.startswith("https://github.com/"):
    rest = url[len("https://github.com/"):].split("/")
    # <owner>/<name>/releases/latest/download/<file> | .../releases/download/<tag>/<file>
    if rest[2:5] == ["releases", "latest", "download"]:
        f = root / "latest" / rest[5]
    elif rest[2:4] == ["releases", "download"]:
        f = root / rest[4] / rest[5]
    else:
        f = root / "nonexistent"
    if f.is_file():
        data, status = f.read_bytes(), 200
if data is None:
    data = b"Not Found"
if status >= 400 and fail:
    sys.exit(22)
if out:
    pathlib.Path(out).write_bytes(data)
else:
    sys.stdout.buffer.write(data)
"""

_SHA_SHIM = """#!{py}
import hashlib, sys
args = sys.argv[1:]
if args[:2] == ["-a", "256"]:
    args = args[2:]
for name in args:
    print(hashlib.sha256(open(name, "rb").read()).hexdigest() + "  " + name)
"""

_TOOL_SHIM = """#!{bash}
# Records every mutating call as one tab-joined line; answers the read-only ones.
name={name}
case "$*" in
    "--version") echo "{version}"; exit 0 ;;
    "tool list"|"list --short")
        [ -n "${{SHIM_{upper}_LIST:-}}" ] && [ -f "${{SHIM_{upper}_LIST}}" ] && cat "${{SHIM_{upper}_LIST}}"
        exit 0 ;;
esac
( IFS=$'\t'; echo "$name	$*" >> "$SHIM_LOG" )
case "$*" in
    *install*) [ -n "${{SHIM_{upper}_FAIL:-}}" ] && exit 1 ;;
esac
exit 0
"""


def _script() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _run(
    *args: str,
    path: str | None = None,
    env_extra: dict[str, str] | None = None,
    dry: bool = True,
    home: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh with a controlled PATH. `path` of None keeps the ambient
    one; anything else is used verbatim so a test can decide which of
    uv/pipx/uname the script gets to find."""
    env = dict(os.environ)
    # The installer reads these, so a developer's own environment must not leak
    # in and decide the outcome of a test.
    for leaky in _LEAKY_ENV:
        env.pop(leaky, None)
    if path is not None:
        env["PATH"] = path
    if home is not None:
        env["HOME"] = str(home)
        env["REMO_HOME"] = str(home / ".config" / "remo")
    if env_extra:
        env.update(env_extra)
    assert BASH is not None
    cmd = [BASH, str(INSTALL_SH)]
    if dry:
        cmd.append("--dry-run")
    return subprocess.run(
        [*cmd, *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )


@pytest.fixture
def fake_path(tmp_path: Path) -> object:
    """Builds a PATH containing only what a test opts into, plus a real uname
    (the Windows guard calls it) and the coreutils the script needs, so
    `command -v uv` / `command -v pipx` answer what the test intends rather than
    what the runner happens to have."""

    real_uname = shutil.which("uname")
    assert real_uname is not None, "uname is required by install.sh"

    def _build(
        *,
        uv: bool = False,
        pipx: bool = False,
        uname_out: str | None = None,
        sha: str | None = "sha256sum",
        curl: bool = True,
    ) -> str:
        bin_dir = tmp_path / "bin"
        if bin_dir.exists():
            shutil.rmtree(bin_dir)
        bin_dir.mkdir()
        # Rebuilt on each call so one test can construct several environments.
        uname = bin_dir / "uname"
        if uname_out is None:
            uname.symlink_to(real_uname)
        else:
            uname.write_text(f'#!/bin/sh\necho "{uname_out}"\n', encoding="utf-8")
            uname.chmod(0o755)
        for tool in _BASE_TOOLS:
            real = shutil.which(tool)
            if real and not (bin_dir / tool).exists():
                (bin_dir / tool).symlink_to(real)

        def _write(name: str, body: str) -> None:
            f = bin_dir / name
            f.write_text(body, encoding="utf-8")
            f.chmod(0o755)

        if curl:
            _write("curl", _CURL_SHIM.format(py=sys.executable))
        if sha is not None:
            _write(sha, _SHA_SHIM.format(py=sys.executable))
        assert BASH is not None
        if uv:
            _write("uv", _TOOL_SHIM.format(bash=BASH, name="uv", version="uv 0.9.9", upper="UV"))
        if pipx:
            _write("pipx", _TOOL_SHIM.format(bash=BASH, name="pipx", version="1.7.1", upper="PIPX"))
        return str(bin_dir)

    return _build


@dataclass
class Sandbox:
    """A fake GitHub plus a sandbox HOME, so full runs touch nothing real."""

    root: Path
    home: Path
    log: Path

    @property
    def remo_home(self) -> Path:
        return self.home / ".config" / "remo"

    @property
    def record(self) -> Path:
        return self.remo_home / "install.json"

    def run(
        self, *args: str, path: str, dry: bool = False, env_extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "REMO_TEST_FIXTURE_ROOT": str(self.root),
            "SHIM_LOG": str(self.log),
            **(env_extra or {}),
        }
        return _run(*args, path=path, env_extra=env, dry=dry, home=self.home)

    def calls(self, tool: str) -> list[list[str]]:
        if not self.log.exists():
            return []
        rows = [ln.split("\t") for ln in self.log.read_text(encoding="utf-8").splitlines()]
        return [r[1:] for r in rows if r[0] == tool]

    def urls(self) -> list[str]:
        return [c[0] for c in self.calls("curl")]

    def installs(self, tool: str = "uv") -> list[list[str]]:
        return [c for c in self.calls(tool) if "install" in c[:2]]

    def record_json(self) -> dict:
        return json.loads(self.record.read_text(encoding="utf-8"))


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    root = tmp_path / "gh"
    root.mkdir()
    fx.make_release(root, "rc-9.9.9rc1")
    fx.make_release(root, "v9.9.9")
    fx.publish_latest(root, "v9.9.9")
    fx.write_api_releases(root, ["rc-9.9.9rc1", "rc-9.9.8rc2"])
    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "log").write_text("", encoding="utf-8")
    return Sandbox(root=root, home=home, log=tmp_path / "log")


# ---------------------------------------------------------------------------
# One path for every release: manifest -> verified wheels -> one install command
# ---------------------------------------------------------------------------

WHL_CLI_RC = "remo_cli-9.9.9rc1-py3-none-any.whl"
WHL_EC2_RC = "remo_provider_ec2-0.1.0-py3-none-any.whl"
WHL_HZ_RC = "remo_provider_hetzner-0.1.0-py3-none-any.whl"
RC_URL = "https://github.com/get2knowio/remo/releases/download/rc-9.9.9rc1"


def _local(arg: str) -> str:
    """`NAME[extras] @ file://<tmp>/<wheel>` -> its wheel file name."""
    assert " @ file://" in arg, arg
    return arg.split("/")[-1]


class TestReleaseRouting:
    """US1: an RC installs with --with, in one command."""

    def test_rc_with_ec2_and_web_is_one_install_command(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "ec2,web", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        installs = sandbox.installs()
        assert len(installs) == 1, installs
        args = installs[0]
        assert args[:3] == ["tool", "install", "--force"]
        assert args[3].startswith("remo-cli[web] @ file://")
        assert _local(args[3]) == WHL_CLI_RC
        assert args[4] == "--with"
        assert args[5].startswith("remo-provider-ec2 @ file://")
        assert _local(args[5]) == WHL_EC2_RC
        assert len(args) == 6

    @pytest.mark.parametrize(
        ("with_args", "extras", "plugins"),
        [
            ([], "", []),
            (["--with", "web"], "[web]", []),
            (["--with", "ec2"], "", ["ec2"]),
            (["--with", "hetzner"], "", ["hetzner"]),
            (["--with", "ec2,hetzner"], "", ["ec2", "hetzner"]),
            (["--with", "web,ec2,hetzner"], "[web]", ["ec2", "hetzner"]),
            (["--with", "web", "--with", "ec2"], "[web]", ["ec2"]),
        ],
        ids=["none", "web", "ec2", "hetzner", "ec2+hetzner", "all", "repeated"],
    )
    def test_with_combinations(
        self, sandbox, fake_path, with_args: list[str], extras: str, plugins: list[str]
    ) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", *with_args, path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        args = sandbox.installs()[0]
        assert args[3].startswith(f"remo-cli{extras} @ file://")
        withs = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
        assert [w.split(" @ ")[0] for w in withs] == [f"remo-provider-{p}" for p in plugins]

    def test_remo_with_env_twin_and_flag_wins(self, sandbox, fake_path) -> None:
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", path=fake_path(uv=True),
            env_extra={"REMO_WITH": "ec2"}, dry=True,
        )
        assert r.returncode == 0, r.stderr
        assert "remo-provider-ec2" in r.stdout
        # the flag wins over the env twin
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--with", "hetzner", path=fake_path(uv=True),
            env_extra={"REMO_WITH": "ec2"}, dry=True,
        )
        assert r.returncode == 0, r.stderr
        assert "remo-provider-hetzner" in r.stdout
        assert "remo-provider-ec2" not in r.stdout

    def test_bare_prerelease_resolves_newest_rc_through_the_api(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "--with", "ec2", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert _uses_host(sandbox.urls(), "api.github.com")
        assert f"{RC_URL}/manifest.json" in sandbox.urls()
        assert _local(sandbox.installs()[0][3]) == WHL_CLI_RC

    def test_bare_prerelease_dry_run_still_resolves(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", path=fake_path(uv=True), dry=True)
        assert r.returncode == 0, r.stderr
        assert f"{RC_URL}/{WHL_CLI_RC}" in r.stdout

    def test_unknown_with_name_lists_accepted_names_and_installs_nothing(
        self, sandbox, fake_path
    ) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "bogus", path=fake_path(uv=True))
        assert r.returncode == 1
        for accepted in ("web", "ec2", "hetzner"):
            assert accepted in r.stderr
        assert "bogus" in r.stderr
        assert not any(u.endswith(".whl") for u in sandbox.urls()), "no wheel download"
        assert sandbox.installs() == []

    def test_tampered_wheel_is_refused_naming_the_file(self, sandbox, fake_path) -> None:
        fx.make_release(sandbox.root, "rc-9.9.9rc1", tamper="ec2")
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "ec2", path=fake_path(uv=True))
        assert r.returncode == 1
        assert WHL_EC2_RC in r.stderr
        assert sandbox.installs() == []
        assert not sandbox.record.exists()

    def test_no_checksum_tool_refuses_before_installing(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True, sha=None))
        assert r.returncode == 1
        assert "sha256sum" in r.stderr and "shasum" in r.stderr
        assert sandbox.installs() == []

    def test_shasum_alone_is_enough(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True, sha="shasum"))
        assert r.returncode == 0, r.stderr
        assert len(sandbox.installs()) == 1

    def test_missing_manifest_names_the_tag(self, sandbox, fake_path) -> None:
        (sandbox.root / "rc-9.9.9rc1" / "manifest.json").unlink()
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "rc-9.9.9rc1" in r.stderr
        assert sandbox.installs() == []

    def test_rate_limited_api_is_named(self, sandbox, fake_path) -> None:
        fx.rate_limit_api(sandbox.root)
        r = sandbox.run("--prerelease", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "rate limit" in r.stderr.lower()
        assert sandbox.installs() == []

    def test_version_with_rc_shape_is_the_same_as_prerelease(self, sandbox, fake_path) -> None:
        via_flag = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        via_version = sandbox.run("--version", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        assert via_flag.stdout == via_version.stdout
        assert via_flag.returncode == via_version.returncode == 0

    def test_foreign_url_in_the_manifest_is_refused(self, sandbox, fake_path) -> None:
        fx.make_release(sandbox.root, "rc-9.9.9rc1", foreign_url=True)
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True))
        assert r.returncode == 1
        assert _uses_host([r.stderr], "evil.example.com") or "outside" in r.stderr
        assert sandbox.installs() == []

    def test_manifest_for_another_tag_is_refused(self, sandbox, fake_path) -> None:
        fx.make_release(sandbox.root, "rc-9.9.9rc1", manifest_tag="rc-1.0.0rc1")
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True))
        assert r.returncode == 1
        assert sandbox.installs() == []

    def test_manifest_whose_tag_names_another_release_is_refused(
        self, sandbox, fake_path
    ) -> None:
        # The Tier 2 defect: tag rc-9.9.9rc1 over a 4.4.2 release must not install.
        fx.make_release(sandbox.root, "rc-9.9.9rc1", manifest_release="4.4.2",
                        cli_version="4.4.2")
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "does not name its release '4.4.2'" in r.stderr
        assert sandbox.installs() == []

    def test_latest_manifest_whose_tag_names_another_release_is_refused(
        self, sandbox, fake_path
    ) -> None:
        fx.make_release(sandbox.root, "v9.9.9", manifest_release="1.0.0")
        fx.publish_latest(sandbox.root, "v9.9.9")
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 1
        assert "does not name its release '1.0.0'" in r.stderr
        assert sandbox.installs() == []

    def test_cli_wheel_version_other_than_the_release_is_refused(
        self, sandbox, fake_path
    ) -> None:
        fx.make_release(sandbox.root, "rc-9.9.9rc1", cli_version="4.4.2")
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "remo-cli version '4.4.2' is not its release '9.9.9rc1'" in r.stderr
        assert sandbox.installs() == []

    def test_dry_run_prints_manifest_urls_and_changes_nothing(self, sandbox, fake_path) -> None:
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--with", "ec2,web", path=fake_path(uv=True), dry=True
        )
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == (
            f'uv tool install --force "remo-cli[web] @ {RC_URL}/{WHL_CLI_RC}" '
            f'--with "remo-provider-ec2 @ {RC_URL}/{WHL_EC2_RC}"'
        )
        assert not sandbox.record.exists()
        assert not any(u.endswith(".whl") for u in sandbox.urls())
        assert sandbox.installs() == []

    def test_pipx_installs_then_injects_each_plugin(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "ec2,web", path=fake_path(pipx=True))
        assert r.returncode == 0, r.stderr
        installs = sandbox.installs("pipx")
        assert len(installs) == 1
        assert installs[0][:2] == ["install", "--force"]
        assert installs[0][2].startswith("remo-cli[web] @ file://")
        injects = [c for c in sandbox.calls("pipx") if c[0] == "inject"]
        assert len(injects) == 1
        assert injects[0][1] == "remo-cli"
        assert injects[0][2].startswith("remo-provider-ec2 @ file://")

    def test_force_is_always_passed(self, sandbox, fake_path) -> None:
        """An existing install is never upgraded without --force, and the run
        still reports success (the original silent-no-upgrade bug)."""
        for args in ([], ["--version", "9.9.9"], ["--prerelease", "9.9.9rc1"]):
            r = sandbox.run(*args, path=fake_path(uv=True), dry=True)
            assert r.returncode == 0, r.stderr
            assert "--force" in r.stdout

    def test_reports_what_changed(self) -> None:
        source = _script()
        assert "installed_version()" in source
        assert "nothing changed" in source, "a no-change run must say so"
        assert "Updated" in source, "a version change must be reported"


class TestStableRouting:
    """US2: stable takes the same code path, through the release's manifest."""

    def test_default_reads_the_latest_release_manifest(self, sandbox, fake_path) -> None:
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert (
            "https://github.com/get2knowio/remo/releases/latest/download/manifest.json"
            in sandbox.urls()
        )
        assert _local(sandbox.installs()[0][3]) == "remo_cli-9.9.9-py3-none-any.whl"

    def test_explicit_version_reads_its_tag(self, sandbox, fake_path) -> None:
        r = sandbox.run("--version", "9.9.9", "--with", "ec2", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert (
            "https://github.com/get2knowio/remo/releases/download/v9.9.9/manifest.json"
            in sandbox.urls()
        )
        assert sandbox.record_json()["channel"] == "stable"

    def test_unknown_version_is_a_clear_refusal(self, sandbox, fake_path) -> None:
        r = sandbox.run("--version", "1.2.3", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "v1.2.3" in r.stderr
        assert sandbox.installs() == []

    def test_repo_slug_changes_both_urls(self, sandbox, fake_path) -> None:
        env = {"REMO_REPO_SLUG": "someone/fork"}
        sandbox.run(path=fake_path(uv=True), env_extra=env)
        sandbox.run("--version", "9.9.9", path=fake_path(uv=True), env_extra=env)
        urls = sandbox.urls()
        assert "https://github.com/someone/fork/releases/latest/download/manifest.json" in urls
        assert "https://github.com/someone/fork/releases/download/v9.9.9/manifest.json" in urls

    def test_pypi_is_never_consulted(self, sandbox, fake_path) -> None:
        r = sandbox.run("--with", "ec2", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert not _uses_host(sandbox.urls(), "pypi.org")
        for call in sandbox.calls("uv"):
            assert not any(re.match(r"remo-cli(==|$)", a) for a in call), call

    def test_dry_run_stdout_is_only_the_command(self, sandbox, fake_path) -> None:
        """So `CMD=$(install.sh --dry-run ...)` is usable: informational lines
        go to stderr."""
        r = sandbox.run("--version", "9.9.9", path=fake_path(uv=True), dry=True)
        assert len(r.stdout.strip().splitlines()) == 1
        assert "Found uv" in r.stderr


class TestPrereleaseAllowRegression:
    @pytest.mark.parametrize("args", [[], ["--version", "9.9.9"], ["--pre-release", "9.9.9rc1"]])
    def test_prerelease_allow_is_never_emitted(self, sandbox, fake_path, args: list[str]) -> None:
        """The original bug, asserted on behaviour rather than on source text:
        `--prerelease allow` resolves against PyPI, where remo's pre-releases
        are absent by design, so it silently selected the stable release."""
        r = sandbox.run(*args, path=fake_path(uv=True), dry=True)
        assert r.returncode == 0, r.stderr
        assert "--prerelease" not in r.stdout

    def test_the_old_argument_builder_is_gone(self) -> None:
        assert "uv_args" not in _script()

    def test_script_records_why(self) -> None:
        source = _script()
        assert "constitution" in source.lower() or "principle IX" in source


# ---------------------------------------------------------------------------
# Drift gates against dev-build.yml, which decides what can exist at all
# ---------------------------------------------------------------------------


class TestDevBuildAgreement:
    def test_prerelease_regex_matches_the_workflow(self) -> None:
        """install.sh must accept exactly the versions dev-build.yml can
        publish. If the workflow starts allowing another form, a user passing it
        here would otherwise get a PyPI requirement for a version PyPI has never
        heard of."""
        in_script = re.search(r"PRERELEASE_RE='([^']+)'", _script())
        assert in_script, "PRERELEASE_RE not found in install.sh"

        in_workflow = re.search(
            r"grep -qE '([^']+)'", DEV_BUILD_YML.read_text(encoding="utf-8")
        )
        assert in_workflow, "version-validation regex not found in dev-build.yml"

        assert in_script.group(1) == in_workflow.group(1), (
            "install.sh's PRERELEASE_RE has drifted from dev-build.yml's version "
            "validation. They must accept the same set of versions."
        )

    def test_workflow_still_tags_rc_prefixed(self) -> None:
        """The URL install.sh builds embeds this tag scheme."""
        assert 'TAG="rc-${VERSION}"' in DEV_BUILD_YML.read_text(encoding="utf-8")

    def test_workflow_wheel_filename_matches_the_url(self) -> None:
        """The release assets are the wheels + manifest the installer's
        manifest URLs point at (spec 031): attached by glob, at the built commit."""
        text = DEV_BUILD_YML.read_text(encoding="utf-8")
        assert "dist/*.whl dist/manifest.json" in text
        assert "release_manifest.py generate" in text

    def test_url_built_for_a_known_release(self, sandbox, fake_path) -> None:
        """The manifest URL install.sh reads is the one dev-build.yml publishes
        under (`rc-<ver>` tag, `manifest.json` asset)."""
        sandbox.run("--pre-release", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        assert (
            "https://github.com/get2knowio/remo/releases/download/rc-9.9.9rc1/manifest.json"
            in sandbox.urls()
        )


# ---------------------------------------------------------------------------
# Installer selection: uv -> pipx -> bootstrap uv. Never bare pip.
# ---------------------------------------------------------------------------


class TestInstallerSelection:
    def test_uv_preferred_when_both_present(self, sandbox, fake_path) -> None:
        result = sandbox.run("--version", "9.9.9", path=fake_path(uv=True, pipx=True), dry=True)
        assert result.stdout.startswith("uv tool install")

    def test_pipx_used_when_only_pipx_present(self, sandbox, fake_path) -> None:
        """Someone who standardised on pipx does not get uv installed behind
        their back."""
        result = sandbox.run("--version", "9.9.9", path=fake_path(pipx=True), dry=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == (
            'pipx install --force "remo-cli @ '
            'https://github.com/get2knowio/remo/releases/download/v9.9.9/'
            'remo_cli-9.9.9-py3-none-any.whl"'
        )

    def test_neither_present_plans_uv_and_writes_nothing(self, sandbox, fake_path) -> None:
        result = sandbox.run("--version", "9.9.9", path=fake_path(), dry=True)
        assert result.returncode == 0, result.stderr
        assert "would install uv" in result.stderr
        assert result.stdout.startswith("uv tool install")

    def test_bare_pip_is_never_used(self) -> None:
        """PEP 668 distributions (Ubuntu 24.04, Debian 12, Fedora 38+) refuse
        `pip install` into the system interpreter outright."""
        source = _script()
        assert not re.search(r"^\s*pip install", source, re.MULTILINE)
        assert "externally-managed" in source, "the reason should be recorded in the script"


# ---------------------------------------------------------------------------
# Refusals (Principle VI: the paths that fail are covered too)
# ---------------------------------------------------------------------------


class TestRefusals:
    def test_native_windows_is_refused(self, fake_path) -> None:
        """Git Bash / MSYS2 / Cygwin run this script happily and would install a
        Windows Python, pushing the failure past the install."""
        result = _run("--version", "4.3.6", path=fake_path(uv=True, uname_out="MINGW64_NT-10.0"))
        assert result.returncode == 1
        assert "cannot run natively on Windows" in result.stderr
        assert "WSL2" in result.stderr
        assert result.stdout.strip() == "", "nothing should be proposed"

    @pytest.mark.parametrize("uname_out", ["MINGW64_NT-10.0", "MSYS_NT-10.0", "CYGWIN_NT-10.0"])
    def test_every_windows_shell_flavour(self, fake_path, uname_out: str) -> None:
        result = _run("--version", "4.3.6", path=fake_path(uv=True, uname_out=uname_out))
        assert result.returncode == 1

    def test_linux_is_not_refused(self, sandbox, fake_path) -> None:
        result = sandbox.run(
            "--version", "9.9.9", path=fake_path(uv=True, uname_out="Linux"), dry=True
        )
        assert result.returncode == 0

    def test_darwin_is_not_refused(self, sandbox, fake_path) -> None:
        result = sandbox.run(
            "--version", "9.9.9", path=fake_path(uv=True, uname_out="Darwin"), dry=True
        )
        assert result.returncode == 0

    def test_pre_release_with_a_final_version_is_refused(self, fake_path) -> None:
        """Rather than quietly installing the stable release — the exact failure
        mode this rewrite removes."""
        result = _run("--pre-release", "4.3.6", path=fake_path(uv=True))
        assert result.returncode == 1
        assert "final version" in result.stderr
        assert "--version 4.3.6" in result.stderr, "should name the working alternative"

    def test_unknown_option_is_refused(self, fake_path) -> None:
        result = _run("--nope", path=fake_path(uv=True))
        assert result.returncode == 1
        assert "Unknown option" in result.stderr

    def test_version_without_a_value_is_refused(self, fake_path) -> None:
        result = _run("--version", path=fake_path(uv=True))
        assert result.returncode == 1
        assert "needs a value" in result.stderr


class TestHolaSurfaceParity:
    """Surface-area parity with try-hola/hola's cli-install.sh, so the two
    installers in the portfolio answer to the same shapes."""

    def test_prerelease_is_accepted_as_one_word(self, sandbox, fake_path) -> None:
        """hola's flag, and uv's own, is `--prerelease`."""
        result = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        assert result.returncode == 0, result.stderr
        assert "rc-9.9.9rc1" in result.stdout

    def test_both_spellings_are_equivalent(self, sandbox, fake_path) -> None:
        """`--pre-release` is what this script shipped with and must keep
        working."""
        one_word = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        hyphenated = sandbox.run("--pre-release", "9.9.9rc1", path=fake_path(uv=True), dry=True)
        assert one_word.stdout == hyphenated.stdout
        assert one_word.returncode == hyphenated.returncode == 0

    def test_remo_version_env_var(self, sandbox, fake_path) -> None:
        result = sandbox.run(
            path=fake_path(uv=True), env_extra={"REMO_VERSION": "9.9.9rc1"}, dry=True
        )
        assert "rc-9.9.9rc1" in result.stdout

    def test_remo_prerelease_env_var(self, sandbox, fake_path) -> None:
        result = sandbox.run(
            path=fake_path(uv=True),
            env_extra={"REMO_PRERELEASE": "true", "REMO_VERSION": "9.9.9rc1"},
            dry=True,
        )
        assert "rc-9.9.9rc1" in result.stdout

    def test_remo_repo_slug_env_var(self, sandbox, fake_path) -> None:
        """Lets a fork, or a test, point somewhere other than get2knowio/remo."""
        sandbox.run(
            "--prerelease", "9.9.9rc1", path=fake_path(uv=True),
            env_extra={"REMO_REPO_SLUG": "someone/fork"}, dry=True,
        )
        assert "github.com/someone/fork/releases/download/" in "\n".join(sandbox.urls())

    def test_an_explicit_flag_beats_the_environment(self, sandbox, fake_path) -> None:
        result = sandbox.run(
            "--version", "9.9.9", path=fake_path(uv=True),
            env_extra={"REMO_VERSION": "9.9.9rc1"}, dry=True,
        )
        assert "remo_cli-9.9.9-py3-none-any.whl" in result.stdout
        assert "rc-9.9.9rc1" not in result.stdout

    def test_resolver_needs_no_json_parser(self) -> None:
        """An `rc-` tag is only ever created by dev-build.yml's prerelease job,
        so the newest one IS the newest pre-release and the `prerelease` boolean
        is redundant. That keeps the resolver to grep/sed — nothing that can be
        missing on a fresh machine, matching hola's approach."""
        source = _script()
        assert "jq" not in source
        assert "python3" not in source

    def test_help_documents_the_environment_variables(self) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(INSTALL_SH), "--help"], capture_output=True, text=True, timeout=30
        )
        assert "REMO_VERSION" in result.stdout
        assert "REMO_PRERELEASE" in result.stdout
        assert "REMO_REPO_SLUG" in result.stdout
        assert "REMO_WITH" in result.stdout
        assert "REMO_WITHOUT" in result.stdout

    def test_help_documents_the_new_flags_with_full_urls(self) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(INSTALL_SH), "--help"], capture_output=True, text=True, timeout=30
        )
        for flag in ("--with ", "--without", "--with-pkg", "--with-url"):
            assert flag in result.stdout
        assert "from PyPI" not in result.stdout
        assert "/install.sh" in result.stdout
        assert ".../install.sh" not in result.stdout


class TestScriptHygiene:
    def test_script_parses(self) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, "-n", str(INSTALL_SH)], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, result.stderr

    def test_help_documents_the_two_channels(self) -> None:
        assert BASH is not None
        result = subprocess.run(
            [BASH, str(INSTALL_SH), "--help"], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0
        assert "--pre-release" in result.stdout
        assert "--dry-run" in result.stdout
        assert "never published to PyPI" in result.stdout


# ---------------------------------------------------------------------------
# US3: additive, remembered installs
# ---------------------------------------------------------------------------

_TS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"


class TestInstallRecord:
    def _first(self, sandbox, fake_path, *extra: str, uv: bool = True, pipx: bool = False):
        r = sandbox.run("--prerelease", "9.9.9rc1", *extra, path=fake_path(uv=uv, pipx=pipx))
        assert r.returncode == 0, r.stderr
        return r

    def test_record_layout_is_exact(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2,web")
        text = sandbox.record.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[:9] == [
            '{"format":1,',
            '"repo":"get2knowio/remo",',
            '"channel":"prerelease",',
            '"version":"9.9.9rc1",',
            '"installer":"uv",',
            '"extras":["web"],',
            '"plugins":["ec2"],',
            '"with_pkg":[],',
            '"with_url":[],',
        ]
        assert re.fullmatch(rf'"updated_at":"{_TS}"\}}', lines[9])
        assert len(lines) == 10
        data = json.loads(text)
        assert data["plugins"] == ["ec2"]
        assert oct(sandbox.record.stat().st_mode & 0o777) == "0o644"
        assert oct(sandbox.remo_home.stat().st_mode & 0o777) == "0o700"

    def test_a_later_run_adds_to_what_is_recorded(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2,web")
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "hetzner", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        args = sandbox.installs()[-1]
        assert args[3].startswith("remo-cli[web] @")
        assert [a.split(" @ ")[0] for a in args if a.startswith("remo-provider-")] == [
            "remo-provider-ec2", "remo-provider-hetzner",
        ]
        data = sandbox.record_json()
        assert data["extras"] == ["web"]
        assert data["plugins"] == ["ec2", "hetzner"]

    def test_without_removes_an_entry(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2,web")
        r = sandbox.run("--prerelease", "9.9.9rc1", "--without", "web", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert sandbox.installs()[-1][3].startswith("remo-cli @")
        assert sandbox.record_json()["extras"] == []
        assert sandbox.record_json()["plugins"] == ["ec2"]

    def test_without_something_not_installed_is_info_not_error(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2")
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--without", "hetzner", path=fake_path(uv=True)
        )
        assert r.returncode == 0, r.stderr
        assert "hetzner" in r.stdout and "not" in r.stdout.lower()

    def test_rerun_is_a_no_op_but_for_the_timestamp(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2,web")
        before = sandbox.record.read_text(encoding="utf-8").splitlines()
        self._first(sandbox, fake_path, "--with", "ec2,web")
        after = sandbox.record.read_text(encoding="utf-8").splitlines()
        assert before[:-1] == after[:-1]

    def test_third_party_additions_pass_through_and_are_kept(self, sandbox, fake_path) -> None:
        url = "https://example.com/x-1.0-py3-none-any.whl"
        self._first(sandbox, fake_path, "--with-pkg", "foo==1.0", "--with-url", url)
        args = sandbox.installs()[-1]
        withs = [args[i + 1] for i, a in enumerate(args) if a == "--with"]
        assert withs == ["foo==1.0", url]
        data = sandbox.record_json()
        assert data["with_pkg"] == ["foo==1.0"]
        assert data["with_url"] == [url]
        # a later run that never mentions them keeps them
        self._first(sandbox, fake_path, "--with", "ec2")
        withs = [a for a in sandbox.installs()[-1] if a in ("foo==1.0", url)]
        assert withs == ["foo==1.0", url]
        assert sandbox.record_json()["with_pkg"] == ["foo==1.0"]

    def test_third_party_on_pipx_is_injected(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with-pkg", "foo==1.0", uv=False, pipx=True)
        injects = [c for c in sandbox.calls("pipx") if c[0] == "inject"]
        assert injects == [["inject", "remo-cli", "foo==1.0"]]

    def test_bad_pkg_name_is_refused(self, sandbox, fake_path) -> None:
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with-pkg", 'bad"name', path=fake_path(uv=True))
        assert r.returncode == 1
        assert sandbox.installs() == []

    def test_non_https_url_is_refused(self, sandbox, fake_path) -> None:
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--with-url", "http://example.com/x.whl",
            path=fake_path(uv=True),
        )
        assert r.returncode == 1
        assert "https" in r.stderr
        assert sandbox.installs() == []

    def test_corrupt_record_warns_and_is_ignored(self, sandbox, fake_path) -> None:
        sandbox.remo_home.mkdir(parents=True)
        sandbox.record.write_text("{not json", encoding="utf-8")
        r = self._first(sandbox, fake_path, "--with", "ec2")
        assert "install.json" in r.stderr
        args = sandbox.installs()[0]
        assert args[3].startswith("remo-cli @")
        assert sandbox.record_json()["plugins"] == ["ec2"]

    def test_unknown_record_format_is_ignored(self, sandbox, fake_path) -> None:
        sandbox.remo_home.mkdir(parents=True)
        sandbox.record.write_text(
            '{"format":2,\n"repo":"get2knowio/remo",\n"plugins":["hetzner"]}\n', encoding="utf-8"
        )
        r = self._first(sandbox, fake_path)
        assert "install.json" in r.stderr
        assert not [a for a in sandbox.installs()[0] if a.startswith("remo-provider-hetzner")]

    def test_failed_install_keeps_the_previous_record(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2")
        before = sandbox.record.read_text(encoding="utf-8")
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--with", "hetzner", path=fake_path(uv=True),
            env_extra={"SHIM_UV_FAIL": "1"},
        )
        assert r.returncode == 1
        assert sandbox.record.read_text(encoding="utf-8") == before

    def test_dry_run_never_writes_the_record(self, sandbox, fake_path) -> None:
        sandbox.run("--prerelease", "9.9.9rc1", "--with", "ec2", path=fake_path(uv=True), dry=True)
        assert not sandbox.record.exists()

    def test_other_manager_copy_is_removed_after_success_only(
        self, sandbox, fake_path, tmp_path: Path
    ) -> None:
        listing = tmp_path / "pipx-list"
        listing.write_text("remo-cli 4.4.0\n", encoding="utf-8")
        env = {"SHIM_PIPX_LIST": str(listing)}
        # failure: nothing is removed
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", path=fake_path(uv=True, pipx=True),
            env_extra={**env, "SHIM_UV_FAIL": "1"},
        )
        assert r.returncode == 1
        assert [c for c in sandbox.calls("pipx") if c[0] == "uninstall"] == []
        # success: removed, and after the new install
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True, pipx=True), env_extra=env)
        assert r.returncode == 0, r.stderr
        order = [(t, c[0]) for t, c in
                 [(ln.split("\t")[0], ln.split("\t")[1:]) for ln in
                  sandbox.log.read_text(encoding="utf-8").splitlines()] if t in ("uv", "pipx")]
        assert ("pipx", "uninstall") in order
        assert order.index(("uv", "tool")) < order.index(("pipx", "uninstall"))

    def test_channel_switch_is_recorded(self, sandbox, fake_path) -> None:
        r = sandbox.run("--version", "9.9.9", "--with", "ec2", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert sandbox.record_json()["channel"] == "stable"
        self._first(sandbox, fake_path)
        data = sandbox.record_json()
        assert data["channel"] == "prerelease"
        assert data["version"] == "9.9.9rc1"
        assert data["plugins"] == ["ec2"]

    def test_the_record_follows_the_chosen_installer(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, uv=False, pipx=True)
        assert sandbox.record_json()["installer"] == "pipx"


# ---------------------------------------------------------------------------
# Review follow-ups: self-update re-passing a dropped plugin, pipx leftovers,
# the rate-limit sniff, the "existing config" line, the repo slug.
# ---------------------------------------------------------------------------


class TestReviewFollowUps:
    def _first(self, sandbox, fake_path, *extra: str, uv: bool = True, pipx: bool = False):
        r = sandbox.run("--prerelease", "9.9.9rc1", *extra, path=fake_path(uv=uv, pipx=pipx))
        assert r.returncode == 0, r.stderr
        return r

    def test_recorded_plugin_passed_again_as_with_is_dropped_not_refused(
        self, sandbox, fake_path
    ) -> None:
        """`remo self-update` re-passes the recorded set as --with; a release that
        no longer ships one of those plugins must drop it, not fail every update."""
        self._first(sandbox, fake_path, "--with", "ec2,hetzner")
        fx.make_release(sandbox.root, "rc-9.9.9rc2", plugins=("hetzner",))
        r = sandbox.run(
            "--prerelease", "9.9.9rc2", "--with", "ec2,hetzner", path=fake_path(uv=True)
        )
        assert r.returncode == 0, r.stderr
        assert "ec2" in r.stderr and "dropping" in r.stderr
        args = sandbox.installs()[-1]
        assert not [a for a in args if a.startswith("remo-provider-ec2")]
        assert sandbox.record_json()["plugins"] == ["hetzner"]

    def test_unrecorded_unknown_with_name_is_still_refused(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "hetzner")
        r = sandbox.run("--prerelease", "9.9.9rc1", "--with", "bogus", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "bogus" in r.stderr

    def test_pipx_without_uninjects_the_removed_plugin(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2,hetzner", uv=False, pipx=True)
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--without", "ec2", path=fake_path(pipx=True)
        )
        assert r.returncode == 0, r.stderr
        calls = sandbox.calls("pipx")
        last_install = max(i for i, c in enumerate(calls) if c[0] == "install")
        after = calls[last_install + 1:]
        assert ["uninject", "remo-cli", "remo-provider-ec2"] in after
        # hetzner is kept: uninjected (deps left) and then injected again, so a
        # same-version wheel with new code is actually installed
        uninj = after.index(["uninject", "--leave-deps", "remo-cli", "remo-provider-hetzner"])
        inj = next(i for i, c in enumerate(after) if c[0] == "inject")
        assert uninj < inj
        assert after[inj][2].startswith("remo-provider-hetzner @ file://")
        assert not [c for c in after if c[0] == "inject" and "remo-provider-ec2" in c[2]]

    def test_pipx_without_a_package_name_uninjects_it(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with-pkg", "foo==1.0", uv=False, pipx=True)
        r = sandbox.run("--prerelease", "9.9.9rc1", "--without", "foo", path=fake_path(pipx=True))
        assert r.returncode == 0, r.stderr
        assert sandbox.record_json()["with_pkg"] == []
        assert ["uninject", "remo-cli", "foo"] in sandbox.calls("pipx")

    def test_uv_never_uninjects(self, sandbox, fake_path) -> None:
        self._first(sandbox, fake_path, "--with", "ec2")
        self._first(sandbox, fake_path, "--without", "ec2")
        assert sandbox.calls("pipx") == []

    def test_release_notes_mentioning_rate_limits_are_not_a_refusal(
        self, sandbox, fake_path
    ) -> None:
        api = sandbox.root / "api" / "releases.json"
        api.write_text(
            '[\n  {\n    "tag_name": "rc-9.9.9rc1",\n    "prerelease": true,\n'
            '    "assets": [\n      {\n        "browser_download_url": '
            f'"{RC_URL}/manifest.json"\n      }}\n    ],\n'
            '    "body": "Names the GitHub rate limit when it is hit."\n  }\n]\n',
            encoding="utf-8",
        )
        r = sandbox.run("--prerelease", path=fake_path(uv=True), dry=True)
        assert r.returncode == 0, r.stderr
        assert f"{RC_URL}/{WHL_CLI_RC}" in r.stdout

    def test_fresh_install_does_not_claim_existing_config(self, sandbox, fake_path) -> None:
        r = self._first(sandbox, fake_path)
        assert "was preserved" not in r.stdout
        r = self._first(sandbox, fake_path)
        assert "was preserved" in r.stdout

    def test_malformed_repo_slug_is_refused(self, sandbox, fake_path) -> None:
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", path=fake_path(uv=True),
            env_extra={"REMO_REPO_SLUG": 'o/r"x'},
        )
        assert r.returncode == 1
        assert "owner/name" in r.stderr
        assert sandbox.installs() == [] and not sandbox.record.exists()

    def test_same_version_with_a_new_plugin_is_reported_as_a_change(
        self, sandbox, fake_path, tmp_path: Path
    ) -> None:
        listing = tmp_path / "uv-list"
        listing.write_text("remo-cli v9.9.9rc1\n", encoding="utf-8")
        env = {"SHIM_UV_LIST": str(listing)}
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True), env_extra=env)
        assert r.returncode == 0, r.stderr
        r = sandbox.run(
            "--prerelease", "9.9.9rc1", "--with", "ec2", path=fake_path(uv=True), env_extra=env
        )
        assert r.returncode == 0, r.stderr
        assert "Updated what is installed" in r.stdout
        assert "nothing changed" not in r.stdout
        r = sandbox.run("--prerelease", "9.9.9rc1", path=fake_path(uv=True), env_extra=env)
        assert r.returncode == 0, r.stderr
        assert "nothing changed" in r.stdout


# ---------------------------------------------------------------------------
# #290: a release exists before its assets do. release-please publishes vX.Y.Z
# at once, release.yml attaches the wheels and manifest.json minutes later (or
# never, if it fails), so `releases/latest/download/manifest.json` 404s.
# ---------------------------------------------------------------------------

LATEST_URL = "https://github.com/get2knowio/remo/releases/latest/download/manifest.json"
API_URL = "https://api.github.com/repos/get2knowio/remo/releases?per_page=20"


class TestReleaseStillBeingPublished:
    @staticmethod
    def _publishing(sandbox: Sandbox, **kw) -> None:
        """v9.9.10 is out but asset-less, so `latest/download` serves nothing."""
        shutil.rmtree(sandbox.root / "latest")
        fx.write_api_releases(
            sandbox.root,
            ["v9.9.10", "rc-9.9.9rc1", "v9.9.9"],
            without_manifest=("v9.9.10",),
            **kw,
        )

    def test_default_falls_back_to_the_newest_release_with_a_manifest(
        self, sandbox, fake_path
    ) -> None:
        self._publishing(sandbox)
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        urls = sandbox.urls()
        assert urls[:3] == [
            LATEST_URL,
            API_URL,
            "https://github.com/get2knowio/remo/releases/download/v9.9.9/manifest.json",
        ]
        assert _local(sandbox.installs()[0][3]) == "remo_cli-9.9.9-py3-none-any.whl"
        assert "v9.9.10 is the newest release but is still being published" in r.stderr
        assert "Installing v9.9.9 instead" in r.stderr
        assert sandbox.record_json()["channel"] == "stable"

    def test_fallback_reads_githubs_compact_layout_too(self, sandbox, fake_path) -> None:
        self._publishing(sandbox, compact=True)
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert _local(sandbox.installs()[0][3]) == "remo_cli-9.9.9-py3-none-any.whl"

    def test_fallback_dry_run_keeps_stdout_to_the_command(self, sandbox, fake_path) -> None:
        self._publishing(sandbox)
        r = sandbox.run(path=fake_path(uv=True), dry=True)
        assert r.returncode == 0, r.stderr
        assert "still being published" in r.stderr
        assert "still being published" not in r.stdout
        assert "releases/download/v9.9.9/remo_cli-9.9.9-py3-none-any.whl" in r.stdout

    def test_drafts_and_prereleases_are_never_the_fallback(self, sandbox, fake_path) -> None:
        shutil.rmtree(sandbox.root / "latest")
        fx.make_release(sandbox.root, "v9.9.11")
        fx.write_api_releases(
            sandbox.root, ["v9.9.11", "rc-9.9.9rc1", "v9.9.9"], drafts=("v9.9.11",)
        )
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert _local(sandbox.installs()[0][3]) == "remo_cli-9.9.9-py3-none-any.whl"
        assert "still being published" not in r.stderr, "a draft is not a newer release"

    def test_latest_404_with_the_newest_release_already_complete_needs_no_note(
        self, sandbox, fake_path
    ) -> None:
        """The race in reverse: the assets landed between the two requests."""
        shutil.rmtree(sandbox.root / "latest")
        fx.write_api_releases(sandbox.root, ["rc-9.9.9rc1", "v9.9.9"])
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert "still being published" not in r.stderr
        assert _local(sandbox.installs()[0][3]) == "remo_cli-9.9.9-py3-none-any.whl"

    def test_a_working_latest_needs_no_api_call(self, sandbox, fake_path) -> None:
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert not _uses_host(sandbox.urls(), "api.github.com")

    def test_no_stable_release_with_a_manifest_fails_clearly(self, sandbox, fake_path) -> None:
        shutil.rmtree(sandbox.root / "latest")
        fx.write_api_releases(
            sandbox.root, ["v9.9.10", "rc-9.9.9rc1", "v9.9.9"],
            without_manifest=("v9.9.10", "v9.9.9"),
        )
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 1
        assert LATEST_URL in r.stderr
        assert "No recent stable release carries a manifest.json yet" in r.stderr
        assert "v9.9.10 is still being published" in r.stderr
        assert sandbox.installs() == [] and not sandbox.record.exists()

    def test_no_stable_release_at_all_fails_clearly(self, sandbox, fake_path) -> None:
        shutil.rmtree(sandbox.root / "latest")
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 1
        assert "No stable release with a manifest.json was found" in r.stderr
        assert sandbox.installs() == []

    def test_fallback_names_an_exhausted_rate_limit(self, sandbox, fake_path) -> None:
        shutil.rmtree(sandbox.root / "latest")
        fx.rate_limit_api(sandbox.root)
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 1
        assert "rate limit" in r.stderr.lower()
        assert "--version" in r.stderr
        assert sandbox.installs() == []

    @pytest.mark.parametrize(
        ("args", "tag"),
        [(["--version", "9.9.10"], "v9.9.10"), (["--prerelease", "9.9.10rc1"], "rc-9.9.10rc1")],
    )
    def test_a_named_version_is_never_substituted(
        self, sandbox, fake_path, args: list[str], tag: str
    ) -> None:
        self._publishing(sandbox)
        fx.write_api_releases(
            sandbox.root, ["rc-9.9.10rc1", "v9.9.10", "rc-9.9.9rc1", "v9.9.9"],
            without_manifest=("rc-9.9.10rc1", "v9.9.10"),
        )
        r = sandbox.run(*args, path=fake_path(uv=True))
        assert r.returncode == 1
        assert tag in r.stderr
        assert not _uses_host(sandbox.urls(), "api.github.com")
        assert sandbox.installs() == []

    def test_bare_prerelease_skips_one_still_being_published(self, sandbox, fake_path) -> None:
        fx.write_api_releases(
            sandbox.root, ["rc-9.9.10rc1", "v9.9.9", "rc-9.9.9rc1"],
            without_manifest=("rc-9.9.10rc1",),
        )
        r = sandbox.run("--prerelease", path=fake_path(uv=True))
        assert r.returncode == 0, r.stderr
        assert f"{RC_URL}/manifest.json" in sandbox.urls()
        assert _local(sandbox.installs()[0][3]) == WHL_CLI_RC
        assert "rc-9.9.10rc1 is the newest release but is still being published" in r.stderr
        assert sandbox.record_json()["channel"] == "prerelease"

    def test_bare_prerelease_with_no_manifest_anywhere_fails_clearly(
        self, sandbox, fake_path
    ) -> None:
        fx.write_api_releases(
            sandbox.root, ["rc-9.9.10rc1", "v9.9.9"], without_manifest=("rc-9.9.10rc1",)
        )
        r = sandbox.run("--prerelease", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "No recent pre-release carries a manifest.json yet" in r.stderr
        assert "rc-9.9.10rc1 is still being published" in r.stderr
        assert "v9.9.9" not in r.stderr, "never offers stable for a pre-release request"
        assert sandbox.installs() == []

    def test_bare_prerelease_with_no_prerelease_at_all_fails_clearly(
        self, sandbox, fake_path
    ) -> None:
        fx.write_api_releases(sandbox.root, ["v9.9.9"])
        r = sandbox.run("--prerelease", path=fake_path(uv=True))
        assert r.returncode == 1
        assert "Could not determine the newest pre-release" in r.stderr
        assert sandbox.installs() == []

    def test_text_in_release_notes_is_never_read_as_a_release(self, sandbox, fake_path) -> None:
        """Every fixture release's notes contain a quoted tag_name for v0.0.1
        and a manifest url under it; none of that may surface as a release."""
        shutil.rmtree(sandbox.root / "latest")
        fx.write_api_releases(sandbox.root, ["rc-9.9.9rc1"])
        r = sandbox.run(path=fake_path(uv=True))
        assert r.returncode == 1
        assert "v0.0.1" not in r.stderr + r.stdout
        assert not any("v0.0.1" in u for u in sandbox.urls())
