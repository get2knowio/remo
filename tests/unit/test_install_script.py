"""Tests for install.sh — the curl|bash installer.

Drives the real script through its `--dry-run` mode rather than re-implementing
its logic, so what is asserted is what a user actually gets.

The bug these exist to prevent is specific and was live: `--pre-release` ran
`uv tool install --prerelease allow`, which resolves against PyPI. remo's
pre-releases never go to PyPI (constitution IX), so the newest one a resolver
could see was 2.2.0rc13 — older than the current stable — and the flag silently
installed the stable release while reporting success.

Nothing here touches the network: every case passes an explicit version. Bare
`--pre-release` hits the GitHub releases API and is therefore not covered.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / "install.sh"
DEV_BUILD_YML = REPO_ROOT / ".github" / "workflows" / "dev-build.yml"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _script() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _run(
    *args: str, path: str | None = None, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run install.sh with a controlled PATH. `path` of None keeps the ambient
    one; anything else is used verbatim so a test can decide which of
    uv/pipx/uname the script gets to find."""
    env = dict(os.environ)
    # The installer reads these, so a developer's own environment must not leak
    # in and decide the outcome of a test.
    for leaky in ("REMO_VERSION", "REMO_PRERELEASE", "REMO_REPO_SLUG"):
        env.pop(leaky, None)
    if path is not None:
        env["PATH"] = path
    if env_extra:
        env.update(env_extra)
    assert BASH is not None
    return subprocess.run(
        [BASH, str(INSTALL_SH), "--dry-run", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
        timeout=60,
    )


@pytest.fixture
def fake_path(tmp_path: Path) -> object:
    """Builds a PATH containing only what a test opts into, plus a real uname
    (the Windows guard calls it) — so `command -v uv` / `command -v pipx`
    answer what the test intends rather than what the runner happens to have."""

    real_uname = shutil.which("uname")
    assert real_uname is not None, "uname is required by install.sh"

    def _build(*, uv: bool = False, pipx: bool = False, uname_out: str | None = None) -> str:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        # Rebuilt on each call so one test can construct several environments.
        uname = bin_dir / "uname"
        uname.unlink(missing_ok=True)
        if uname_out is None:
            uname.symlink_to(real_uname)
        else:
            uname.write_text(f'#!/bin/sh\necho "{uname_out}"\n', encoding="utf-8")
            uname.chmod(0o755)
        if uv:
            stub = bin_dir / "uv"
            stub.write_text('#!/bin/sh\necho "uv 0.9.9"\n', encoding="utf-8")
            stub.chmod(0o755)
        if pipx:
            stub = bin_dir / "pipx"
            stub.write_text('#!/bin/sh\necho "1.7.1"\n', encoding="utf-8")
            stub.chmod(0o755)
        return str(bin_dir)

    return _build


# ---------------------------------------------------------------------------
# Channel routing: PyPI for final versions, GitHub releases for pre-releases
# ---------------------------------------------------------------------------


class TestChannelRouting:
    def test_no_version_installs_the_package_from_pypi(self, fake_path) -> None:
        result = _run(path=fake_path(uv=True))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'uv tool install "remo-cli"'

    def test_no_version_does_not_force(self, fake_path) -> None:
        """A bare re-run stays the no-op it is today (Principle VII)."""
        result = _run(path=fake_path(uv=True))
        assert "--force" not in result.stdout

    def test_final_version_pins_against_pypi(self, fake_path) -> None:
        result = _run("--version", "4.3.6", path=fake_path(uv=True))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'uv tool install --force "remo-cli==4.3.6"'

    @pytest.mark.parametrize("version", ["4.4.0rc3", "4.4.0b2", "4.4.0a1", "4.4.0.dev5"])
    def test_prerelease_versions_route_to_github(self, fake_path, version: str) -> None:
        """Every PEP 440 pre-release/dev form dev-build.yml accepts must resolve
        to a wheel URL, never to a PyPI requirement."""
        result = _run("--version", version, path=fake_path(uv=True))
        assert result.returncode == 0, result.stderr
        assert f"releases/download/rc-{version}/remo_cli-{version}-py3-none-any.whl" in result.stdout
        assert f"remo-cli=={version}" not in result.stdout

    def test_pre_release_flag_with_version_matches_bare_version(self, fake_path) -> None:
        via_flag = _run("--pre-release", "4.4.0rc3", path=fake_path(uv=True)).stdout
        via_version = _run("--version", "4.4.0rc3", path=fake_path(uv=True)).stdout
        assert via_flag == via_version

    def test_prerelease_install_is_forced(self, fake_path) -> None:
        """Switching an existing install onto an RC needs --force; without it uv
        reports the already-installed version and changes nothing."""
        result = _run("--pre-release", "4.4.0rc3", path=fake_path(uv=True))
        assert "--force" in result.stdout

    def test_dry_run_stdout_is_only_the_command(self, fake_path) -> None:
        """So `CMD=$(install.sh --dry-run …)` is usable — the informational
        lines go to stderr."""
        result = _run("--version", "4.3.6", path=fake_path(uv=True))
        assert len(result.stdout.strip().splitlines()) == 1
        assert "Found uv" in result.stderr


class TestPrereleaseAllowRegression:
    @pytest.mark.parametrize(
        "args", [[], ["--version", "4.3.6"], ["--pre-release", "4.4.0rc3"]]
    )
    def test_prerelease_allow_is_never_emitted(self, fake_path, args: list[str]) -> None:
        """The original bug, asserted on behaviour rather than on source text:
        `--prerelease allow` resolves against PyPI, where remo's pre-releases
        are absent by design, so it silently selected the stable release. The
        flag still appears in the help output — explaining why it cannot be
        used — so only the emitted command is meaningful here."""
        result = _run(*args, path=fake_path(uv=True))
        assert result.returncode == 0, result.stderr
        assert "--prerelease" not in result.stdout

    def test_the_old_argument_builder_is_gone(self) -> None:
        """`uv_args` was the array that appended `("--prerelease" "allow")`.
        Its absence is a precise, stable signal that the old path is deleted
        rather than merely bypassed."""
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
        assert (
            "remo_cli-${VERSION}-py3-none-any.whl"
            in DEV_BUILD_YML.read_text(encoding="utf-8")
        )

    def test_url_built_for_a_known_release(self, fake_path) -> None:
        """Pinned against the real, published rc-4.4.0rc3 asset URL."""
        result = _run("--pre-release", "4.4.0rc3", path=fake_path(uv=True))
        assert (
            "https://github.com/get2knowio/remo/releases/download/"
            "rc-4.4.0rc3/remo_cli-4.4.0rc3-py3-none-any.whl" in result.stdout
        )


# ---------------------------------------------------------------------------
# Installer selection: uv -> pipx -> bootstrap uv. Never bare pip.
# ---------------------------------------------------------------------------


class TestInstallerSelection:
    def test_uv_preferred_when_both_present(self, fake_path) -> None:
        result = _run("--version", "4.3.6", path=fake_path(uv=True, pipx=True))
        assert result.stdout.startswith("uv tool install")

    def test_pipx_used_when_only_pipx_present(self, fake_path) -> None:
        """Someone who standardised on pipx does not get uv installed behind
        their back."""
        result = _run("--version", "4.3.6", path=fake_path(pipx=True))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'pipx install --force "remo-cli==4.3.6"'

    def test_neither_present_plans_uv_and_writes_nothing(self, fake_path) -> None:
        result = _run("--version", "4.3.6", path=fake_path())
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

    def test_linux_is_not_refused(self, fake_path) -> None:
        result = _run("--version", "4.3.6", path=fake_path(uv=True, uname_out="Linux"))
        assert result.returncode == 0

    def test_darwin_is_not_refused(self, fake_path) -> None:
        result = _run("--version", "4.3.6", path=fake_path(uv=True, uname_out="Darwin"))
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

    def test_prerelease_is_accepted_as_one_word(self, fake_path) -> None:
        """hola's flag, and uv's own, is `--prerelease`."""
        result = _run("--prerelease", "4.4.0rc3", path=fake_path(uv=True))
        assert result.returncode == 0, result.stderr
        assert "rc-4.4.0rc3" in result.stdout

    def test_both_spellings_are_equivalent(self, fake_path) -> None:
        """`--pre-release` is what this script shipped with and must keep
        working."""
        one_word = _run("--prerelease", "4.4.0rc3", path=fake_path(uv=True))
        hyphenated = _run("--pre-release", "4.4.0rc3", path=fake_path(uv=True))
        assert one_word.stdout == hyphenated.stdout
        assert one_word.returncode == hyphenated.returncode == 0

    def test_remo_version_env_var(self, fake_path) -> None:
        result = _run(path=fake_path(uv=True), env_extra={"REMO_VERSION": "4.4.0rc3"})
        assert "rc-4.4.0rc3" in result.stdout

    def test_remo_prerelease_env_var(self, fake_path) -> None:
        result = _run(
            path=fake_path(uv=True),
            env_extra={"REMO_PRERELEASE": "true", "REMO_VERSION": "4.4.0rc2"},
        )
        assert "rc-4.4.0rc2" in result.stdout

    def test_remo_repo_slug_env_var(self, fake_path) -> None:
        """Lets a fork, or a test, point somewhere other than get2knowio/remo."""
        result = _run(
            "--prerelease", "4.4.0rc3", path=fake_path(uv=True),
            env_extra={"REMO_REPO_SLUG": "someone/fork"},
        )
        assert "github.com/someone/fork/releases/download/" in result.stdout

    def test_an_explicit_flag_beats_the_environment(self, fake_path) -> None:
        result = _run(
            "--version", "4.3.6", path=fake_path(uv=True),
            env_extra={"REMO_VERSION": "4.4.0rc1"},
        )
        assert result.stdout.strip() == 'uv tool install --force "remo-cli==4.3.6"'

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
