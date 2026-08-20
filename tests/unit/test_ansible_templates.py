"""Jinja2 parse check for every Ansible template in the repo.

Catches syntax errors before they hit a real Ansible run — most notably the
``${#var}`` bash array-length idiom, which Jinja2 reads as a ``{#`` comment
opener and consumes the rest of the file looking for ``#}``. That class of
bug otherwise only surfaces on a live host during a smoke test.

We use ``Environment.parse`` rather than ``render`` so the test doesn't need
to know what variables each template expects.

This module also contains more targeted, black-box tests for
``remo-host.sh.j2`` (see ``contracts/remo-host-protocol.md`` under
specs/010-web-session-interface/): render it, syntax-check it with ``bash
-n``, and — when ``bash`` is available in the sandbox — actually execute it
against a temporary ``PROJECTS_DIR`` fixture to verify the JSON verbs emit
only JSON on stdout and that project-name validation on ``sessions attach``
rejects bad input before ever reaching ``exec``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from jinja2 import Environment, TemplateSyntaxError

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "ansible"
REMO_HOST_TEMPLATE = TEMPLATE_ROOT / "roles" / "user_setup" / "templates" / "remo-host.sh.j2"

BASH = shutil.which("bash")
GIT = shutil.which("git")
SETSID = shutil.which("setsid")


def _all_templates() -> list[Path]:
    return sorted(TEMPLATE_ROOT.rglob("*.j2"))


@pytest.mark.parametrize("template_path", _all_templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_template_parses(template_path: Path) -> None:
    env = Environment(autoescape=False)
    source = template_path.read_text()
    try:
        env.parse(source)
    except TemplateSyntaxError as exc:
        pytest.fail(
            f"{template_path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.message}"
        )


# ---------------------------------------------------------------------------
# remo-host.sh.j2 — targeted tests (T009)
# ---------------------------------------------------------------------------


def _render_remo_host(projects_dir: str, devcontainer_cli_bin: str = "devcontainer") -> str:
    """Render remo-host.sh.j2 with the minimal context it needs.

    The template interpolates ``dev_workspace_dir`` (as ``PROJECTS_DIR``) and
    ``devcontainer_cli_bin`` (gates ``projects rebuild``, which needs the
    reference CLI's ``--remove-existing-container``).
    """
    source = REMO_HOST_TEMPLATE.read_text()
    template = Environment(autoescape=False).from_string(source)
    return template.render(
        dev_workspace_dir=projects_dir,
        devcontainer_cli_bin=devcontainer_cli_bin,
    )


@pytest.fixture
def rendered_script(tmp_path: Path) -> Path:
    """Render remo-host.sh.j2 to an executable file with a fake PROJECTS_DIR.

    Fixture layout:
      alpha/                 - plain project, no devcontainer
      beta/.devcontainer/    - project with a devcontainer
    """
    projects_dir = tmp_path / "projects"
    (projects_dir / "alpha").mkdir(parents=True)
    (projects_dir / "beta" / ".devcontainer").mkdir(parents=True)

    rendered = _render_remo_host(str(projects_dir))
    script_path = tmp_path / "remo-host"
    script_path.write_text(rendered)
    script_path.chmod(0o755)
    return script_path


def test_remo_host_renders_nonempty_bash(rendered_script: Path) -> None:
    content = rendered_script.read_text()
    assert content.startswith("#!/bin/bash")
    assert "sessions attach" in content
    assert "sessions list" in content
    assert "capabilities" in content


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_bash_syntax_ok(rendered_script: Path) -> None:
    result = subprocess.run(
        [BASH, "-n", str(rendered_script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"bash -n failed: {result.stderr}"


def _run_remo_host(
    script_path: Path,
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
    path_prepend: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    path = "/usr/bin:/bin:/usr/local/bin"
    if path_prepend is not None:
        path = f"{path_prepend}:{path}"
    full_env = {"PATH": path, "HOME": str(fake_home)}
    if env:
        full_env.update(env)
    return subprocess.run(
        [BASH, str(script_path), *args],
        capture_output=True,
        text=True,
        env=full_env,
    )


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_capabilities_json_stdout_only(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_remo_host(rendered_script, tmp_path, "capabilities", "--json")
    assert result.returncode == 0, result.stderr

    # stdout must be nothing but the JSON object.
    payload = json.loads(result.stdout)

    assert payload["protocol_version"] == 1
    assert isinstance(payload["host_tools_version"], str)
    assert payload["projects_root"].endswith("/projects")
    assert payload["operations"] == [
        "capabilities",
        "sessions.list",
        "sessions.attach",
        "host.stats",
        "projects.clone",
        "projects.delete",
        "projects.rebuild",
        "jobs.status",
    ]
    assert isinstance(payload["zellij"], bool)
    assert isinstance(payload["docker"], bool)
    # Degrades gracefully instead of crashing regardless of what's installed.
    assert payload["zellij"] == (shutil.which("zellij") is not None)
    assert payload["docker"] == (shutil.which("docker") is not None)


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_capabilities_no_host_tools_version(rendered_script: Path, tmp_path: Path) -> None:
    # No ~/.remo-version marker file was written in the fake HOME.
    result = _run_remo_host(rendered_script, tmp_path, "capabilities", "--json")
    payload = json.loads(result.stdout)
    assert payload["host_tools_version"] == ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_capabilities_reads_version_marker(rendered_script: Path, tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    (fake_home / ".remo-version").write_text("2.1.0")

    result = _run_remo_host(rendered_script, tmp_path, "capabilities", "--json")
    payload = json.loads(result.stdout)
    assert payload["host_tools_version"] == "2.1.0"


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_sessions_list_json_stdout_only(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_remo_host(rendered_script, tmp_path, "sessions", "list", "--json")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["protocol_version"] == 1
    assert payload["projects_root"].endswith("/projects")

    projects = {p["name"]: p for p in payload["projects"]}
    assert set(projects) == {"alpha", "beta"}

    assert projects["alpha"]["has_devcontainer"] is False
    assert projects["beta"]["has_devcontainer"] is True

    # No sessions exist for these throwaway fixture names, regardless of
    # whether zellij itself is installed on this machine.
    assert projects["alpha"]["zellij_state"] == "absent"
    assert projects["beta"]["zellij_state"] == "absent"

    # devcontainer_running must degrade to "unknown" without a devcontainer,
    # and (docker absent) -> "unknown" / (docker present, no matching
    # container) -> "stopped" when a devcontainer is present.
    assert projects["alpha"]["devcontainer_running"] == "unknown"
    if shutil.which("docker") is None:
        assert projects["beta"]["devcontainer_running"] == "unknown"
    else:
        assert projects["beta"]["devcontainer_running"] in ("stopped", "running")

    # Git status fields are always present; the throwaway fixtures are not git
    # repos, so they default to "not tracked / clean / no ahead-behind".
    for name in ("alpha", "beta"):
        assert projects[name]["git_tracked"] is False
        assert projects[name]["git_dirty"] is False
        assert projects[name]["git_ahead"] == 0
        assert projects[name]["git_behind"] == 0


@pytest.mark.skipif(BASH is None or GIT is None, reason="bash/git not available in this sandbox")
def test_remo_host_sessions_list_reports_git_status(tmp_path: Path) -> None:
    """A project that IS a git work tree reports tracked + dirty read-only."""
    projects_dir = tmp_path / "projects"
    repo = projects_dir / "gitproj"
    repo.mkdir(parents=True)
    # A fresh repo with an untracked file is "dirty" per `git status --porcelain`
    # without needing a commit or a configured identity.
    subprocess.run([GIT, "init", "-q", str(repo)], check=True)
    (repo / "file.txt").write_text("hello\n")

    rendered = _render_remo_host(str(projects_dir))
    script_path = tmp_path / "remo-host"
    script_path.write_text(rendered)
    script_path.chmod(0o755)

    result = _run_remo_host(script_path, tmp_path, "sessions", "list", "--json")
    assert result.returncode == 0, result.stderr
    project = {p["name"]: p for p in json.loads(result.stdout)["projects"]}["gitproj"]
    assert project["git_tracked"] is True
    assert project["git_dirty"] is True
    # No upstream configured, so ahead/behind stay 0 (discovery never fetches).
    assert project["git_ahead"] == 0
    assert project["git_behind"] == 0


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_sessions_list_empty_projects_dir(tmp_path: Path) -> None:
    projects_dir = tmp_path / "empty-projects"
    projects_dir.mkdir()
    rendered = _render_remo_host(str(projects_dir))
    script_path = tmp_path / "remo-host"
    script_path.write_text(rendered)
    script_path.chmod(0o755)

    result = _run_remo_host(script_path, tmp_path, "sessions", "list", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["projects"] == []


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "../etc",
        "..",
        "foo/../../etc",
        "/etc/passwd",
        "foo\nbar",
        "does-not-exist",
        ".hidden",
    ],
    ids=[
        "empty",
        "traversal",
        "dotdot",
        "nested-traversal",
        "absolute",
        "control-char",
        "nonexistent",
        "hidden",
    ],
)
def test_remo_host_attach_rejects_bad_project_names(
    rendered_script: Path, tmp_path: Path, bad_name: str
) -> None:
    result = _run_remo_host(rendered_script, tmp_path, "sessions", "attach", "--project", bad_name)
    assert result.returncode == 3, (
        f"expected exit 3 for {bad_name!r}, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    # Diagnostics go to stderr, never stdout, and validation happens before
    # any launch attempt (no partial JSON or terminal output leaks out).
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_attach_valid_name_passes_validation_before_exec(
    rendered_script: Path, tmp_path: Path
) -> None:
    # "alpha" exists under the fixture PROJECTS_DIR, so validation must pass
    # and the script must reach `exec ~/.local/bin/project-launch`. In this
    # sandbox project-launch isn't installed, so exec fails with 127 (command
    # not found) rather than exit 3 — proving validation ran first and
    # succeeded rather than rejecting the name.
    result = _run_remo_host(rendered_script, tmp_path, "sessions", "attach", "--project", "alpha")
    assert result.returncode != 3
    assert result.returncode != 2


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_unknown_subcommand_usage_error(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_remo_host(rendered_script, tmp_path, "bogus")
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_no_subcommand_usage_error(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_remo_host(rendered_script, tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_unsupported_sessions_verb(rendered_script: Path, tmp_path: Path) -> None:
    # "sessions stop" is a reserved future verb (contracts/remo-host-protocol.md
    # "Forward compatibility"): recognized shape, not yet implemented -> 4.
    result = _run_remo_host(rendered_script, tmp_path, "sessions", "stop", "--project", "alpha")
    assert result.returncode == 4
    assert result.stdout == ""


def test_remo_host_source_contains_no_naive_json_concat() -> None:
    """Guard against regressing to raw string concatenation for JSON.

    The script must build JSON strings through the json_escape() helper
    rather than splicing raw variables directly between quotes, which would
    break on project names containing a `"` or `\\`.
    """
    source = REMO_HOST_TEMPLATE.read_text()
    assert "json_escape" in source
    assert 'name":"$name"' not in source



# ---------------------------------------------------------------------------
# remo-host.sh.j2 — host stats / projects clone|delete|rebuild / jobs status
# (see contracts/remo-host-protocol.md; feature: web console host detail page)
#
# These execute the rendered script against fixture /proc and /sys trees via
# the REMO_HOST_PROC_ROOT / REMO_HOST_SYS_ROOT / REMO_HOST_JOBS_DIR test-env
# overrides, and against PATH-shim binaries (git/gh/docker/zellij/
# devcontainer/df/nproc) that record their argv instead of doing real work.
# ---------------------------------------------------------------------------


def _write_shim(shim_dir: Path, name: str, body: str) -> None:
    """Install an argv-recording stub binary onto the shim PATH dir."""
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / name
    shim.write_text("#!/bin/bash\n" + body + "\n")
    shim.chmod(0o755)


def _wait_for_file(path: Path, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {path}")


def _make_proc_tree(root: Path) -> Path:
    proc = root / "proc"
    proc.mkdir(parents=True, exist_ok=True)
    (proc / "uptime").write_text("12345.67 23456.78\n")
    (proc / "loadavg").write_text("0.52 0.58 0.59 1/234 5678\n")
    (proc / "stat").write_text(
        "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 100 0 100 800 0 0 0 0 0 0\n"
    )
    (proc / "meminfo").write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         4096000 kB\n"
        "MemAvailable:    8192000 kB\n"
        "SwapTotal:       2048000 kB\n"
        "SwapFree:        1024000 kB\n"
    )
    return proc


def _make_hwmon_tree(root: Path) -> Path:
    sys_root = root / "sys"
    hwmon = sys_root / "class" / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True, exist_ok=True)
    (hwmon / "name").write_text("k10temp\n")
    (hwmon / "temp1_input").write_text("45500\n")
    (hwmon / "temp1_label").write_text("Tctl\n")
    (hwmon / "temp2_input").write_text("38200\n")  # no matching _label file
    return sys_root


def _make_empty_sys_tree(root: Path) -> Path:
    sys_root = root / "sys"
    (sys_root / "class").mkdir(parents=True, exist_ok=True)
    return sys_root


def _stats_shims(tmp_path: Path, df_lines: list[str] | None = None) -> Path:
    """df + nproc shims giving stats deterministic disk/cpu-count output."""
    shim_dir = tmp_path / "shims"
    if df_lines is None:
        # The same filesystem twice (projects_root and / on one mount):
        # the payload must dedupe it to a single entry.
        df_lines = [
            "/dev/sda1 1000 400 600 40% /",
            "/dev/sda1 1000 400 600 40% /",
        ]
    body_lines = ['echo "Filesystem 1-blocks Used Available Capacity Mounted on"']
    body_lines += [f'echo "{line}"' for line in df_lines]
    _write_shim(shim_dir, "df", "\n".join(body_lines))
    _write_shim(shim_dir, "nproc", "echo 8")
    return shim_dir


def _run_stats(
    rendered_script: Path, tmp_path: Path, sys_root: Path, shim_dir: Path
) -> subprocess.CompletedProcess[str]:
    return _run_remo_host(
        rendered_script,
        tmp_path,
        "host",
        "stats",
        "--json",
        env={
            "REMO_HOST_PROC_ROOT": str(_make_proc_tree(tmp_path)),
            "REMO_HOST_SYS_ROOT": str(sys_root),
        },
        path_prepend=shim_dir,
    )


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_full_payload(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_stats(
        rendered_script, tmp_path, _make_hwmon_tree(tmp_path), _stats_shims(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["protocol_version"] == 1
    assert payload["uptime_s"] == 12345
    assert payload["load_1"] == 0.52
    assert payload["load_5"] == 0.58
    assert payload["load_15"] == 0.59
    assert payload["cpu_count"] == 8
    # The fixture /proc/stat is static, so the two samples are identical and
    # the delta math must degrade to 0.0 rather than divide by zero.
    assert payload["cpu_used_pct"] == 0.0

    kb = 1024
    assert payload["mem_total"] == 16384000 * kb
    assert payload["mem_available"] == 8192000 * kb
    assert payload["mem_used"] == (16384000 - 8192000) * kb
    assert payload["swap_total"] == 2048000 * kb
    assert payload["swap_used"] == (2048000 - 1024000) * kb

    # Duplicate df rows for the same mount point collapse to one entry.
    assert payload["disks"] == [
        {"mount": "/", "size_bytes": 1000, "used_bytes": 400, "avail_bytes": 600}
    ]

    # hwmon millidegrees -> degrees C with one decimal; label "" when the
    # sensor has no tempN_label file.
    assert payload["temps"] == [
        {"name": "k10temp", "label": "Tctl", "temp_c": 45.5},
        {"name": "k10temp", "label": "", "temp_c": 38.2},
    ]


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_two_distinct_mounts(rendered_script: Path, tmp_path: Path) -> None:
    shim_dir = _stats_shims(
        tmp_path,
        df_lines=[
            "/dev/sdb1 2000 100 1900 5% /projects",
            "/dev/sda1 1000 400 600 40% /",
        ],
    )
    result = _run_stats(rendered_script, tmp_path, _make_empty_sys_tree(tmp_path), shim_dir)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [d["mount"] for d in payload["disks"]] == ["/projects", "/"]


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_thermal_zone_fallback(rendered_script: Path, tmp_path: Path) -> None:
    sys_root = tmp_path / "sys"
    tz = sys_root / "class" / "thermal" / "thermal_zone0"
    tz.mkdir(parents=True)
    (tz / "temp").write_text("42000\n")
    (tz / "type").write_text("cpu-thermal\n")

    result = _run_stats(rendered_script, tmp_path, sys_root, _stats_shims(tmp_path))
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["temps"] == [{"name": "cpu-thermal", "label": "", "temp_c": 42.0}]


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_no_sensors_empty_temps(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_stats(
        rendered_script, tmp_path, _make_empty_sys_tree(tmp_path), _stats_shims(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["temps"] == []


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_caps_temps_at_16(rendered_script: Path, tmp_path: Path) -> None:
    sys_root = tmp_path / "sys"
    hwmon = sys_root / "class" / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (hwmon / "name").write_text("many\n")
    for i in range(1, 21):  # 20 sensors on one chip
        (hwmon / f"temp{i:02d}_input").write_text(f"{i * 1000}\n")

    result = _run_stats(rendered_script, tmp_path, sys_root, _stats_shims(tmp_path))
    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["temps"]) == 16


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_stats_requires_json_flag(rendered_script: Path, tmp_path: Path) -> None:
    result = _run_remo_host(rendered_script, tmp_path, "host", "stats")
    assert result.returncode == 2
    assert result.stdout == ""


# --- projects clone ---------------------------------------------------------


def _clone_shims(tmp_path: Path, gh_authed: bool = False) -> tuple[Path, Path, Path]:
    """git + gh shims for clone jobs.

    Returns (shim_dir, git_argv_log, gh_argv_log). The shims record each
    invocation as one space-joined line and create the destination directory
    (their last argument) so the worker's `mv` into projects_root succeeds.
    A gh shim always exists so a real, authenticated gh on the host running
    the tests can never intercept the clone and hit the network.
    """
    shim_dir = tmp_path / "shims"
    git_log = tmp_path / "git-argv.log"
    gh_log = tmp_path / "gh-argv.log"
    _write_shim(
        shim_dir,
        "git",
        f'printf \'%s\\n\' "$*" >> "{git_log}"\n'
        'for a in "$@"; do last="$a"; done\n'
        'mkdir -p "$last"',
    )
    if gh_authed:
        _write_shim(
            shim_dir,
            "gh",
            f'printf \'%s\\n\' "$*" >> "{gh_log}"\n'
            'if [ "$1" = auth ]; then exit 0; fi\n'
            'if [ "$1" = repo ] && [ "$2" = clone ]; then mkdir -p "$4"; fi',
        )
    else:
        _write_shim(shim_dir, "gh", "exit 1")
    return shim_dir, git_log, gh_log


def _jobs_env(tmp_path: Path) -> dict[str, str]:
    return {"REMO_HOST_JOBS_DIR": str(tmp_path / "jobs")}


def _wait_for_job(tmp_path: Path, job_id: str) -> None:
    _wait_for_file(tmp_path / "jobs" / f"{job_id}.exit")


@pytest.mark.skipif(
    BASH is None or SETSID is None, reason="bash/setsid not available in this sandbox"
)
def test_remo_host_clone_uses_git_with_separator(rendered_script: Path, tmp_path: Path) -> None:
    shim_dir, git_log, _ = _clone_shims(tmp_path)
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        "acme/widget",
        "--json",
        env=_jobs_env(tmp_path),
        path_prepend=shim_dir,
    )
    assert result.returncode == 0, result.stderr
    ref = json.loads(result.stdout)
    assert ref["protocol_version"] == 1
    assert ref["kind"] == "clone"
    assert ref["project"] == "widget"
    assert ref["job_id"].startswith("clone-")

    _wait_for_job(tmp_path, ref["job_id"])

    # gh is unauthenticated, so git ran — with `--` before every positional.
    argv = git_log.read_text().strip().split()
    assert argv[0] == "clone"
    assert argv[1] == "--"
    assert argv[2] == "https://github.com/acme/widget"

    # The finished clone was moved into projects_root (never cloned in place).
    assert (tmp_path / "projects" / "widget").is_dir()

    status = _run_remo_host(
        rendered_script,
        tmp_path,
        "jobs",
        "status",
        "--job",
        ref["job_id"],
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert status.returncode == 0, status.stderr
    payload = json.loads(status.stdout)
    assert payload["state"] == "succeeded"
    assert payload["exit_code"] == 0
    assert payload["started_at"] != ""
    assert payload["finished_at"] is not None


@pytest.mark.skipif(
    BASH is None or SETSID is None, reason="bash/setsid not available in this sandbox"
)
def test_remo_host_clone_prefers_authenticated_gh(rendered_script: Path, tmp_path: Path) -> None:
    shim_dir, git_log, gh_log = _clone_shims(tmp_path, gh_authed=True)
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        "https://github.com/acme/widget.git",
        "--json",
        env=_jobs_env(tmp_path),
        path_prepend=shim_dir,
    )
    assert result.returncode == 0, result.stderr
    ref = json.loads(result.stdout)
    assert ref["protocol_version"] == 1
    # Name defaults to the repo basename minus .git, for URLs too.
    assert ref["project"] == "widget"
    _wait_for_job(tmp_path, ref["job_id"])

    gh_lines = gh_log.read_text().strip().splitlines()
    assert any(line.startswith("repo clone https://github.com/acme/widget.git ") for line in gh_lines)
    assert not git_log.exists()
    assert (tmp_path / "projects" / "widget").is_dir()


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize(
    "bad_repo",
    [
        "../etc",
        "..",
        "a/..",
        "-x/y",
        "a/-y",
        "git@github.com:acme/widget.git",
        "ssh://git@github.com/acme/widget",
        "http://github.com/acme/widget",
        "https://gitlab.com/acme/widget",
        "https://github.com/acme/widget/../../evil",
        "acme/widget;rm -rf /",
        "acme widget/x",
        "--upload-pack=evil",
    ],
    ids=[
        "traversal",
        "dotdot",
        "dotdot-repo",
        "leading-dash-owner",
        "leading-dash-repo",
        "ssh-scp-url",
        "ssh-url",
        "plain-http",
        "non-github-host",
        "url-traversal",
        "shell-metachars",
        "space",
        "option-injection",
    ],
)
def test_remo_host_clone_rejects_bad_repos(
    rendered_script: Path, tmp_path: Path, bad_repo: str
) -> None:
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        bad_repo,
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert result.returncode == 3, (
        f"expected exit 3 for {bad_repo!r}, got {result.returncode}; "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert result.stdout == ""
    assert result.stderr != ""
    # Nothing was started: no job files, no new project dirs.
    assert not (tmp_path / "jobs").exists()


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize(
    "bad_name",
    ["../evil", "-evil", "evil name", ".github", "alpha"],
    ids=["traversal", "leading-dash", "space", "leading-dot", "already-exists"],
)
def test_remo_host_clone_rejects_bad_names(
    rendered_script: Path, tmp_path: Path, bad_name: str
) -> None:
    # "alpha" exists in the fixture projects dir; the rest are unsafe shapes.
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        "acme/widget",
        "--name",
        bad_name,
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert result.returncode == 3, result.stderr
    assert result.stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_clone_rejects_existing_default_name(
    rendered_script: Path, tmp_path: Path
) -> None:
    # Default name (repo basename) collides with the existing "alpha" project.
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        "acme/alpha",
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert result.returncode == 3
    assert result.stdout == ""


@pytest.mark.skipif(
    BASH is None or SETSID is None, reason="bash/setsid not available in this sandbox"
)
def test_remo_host_clone_stages_inside_projects_root(
    rendered_script: Path, tmp_path: Path
) -> None:
    # Staging must live INSIDE the projects root (same filesystem, so the
    # final rename is atomic), never under ${TMPDIR}: a tmpfs /tmp can be too
    # small for the repo and a cross-device mv degrades to a non-atomic copy
    # that exposes a partial project. Point TMPDIR at a nonexistent path — a
    # worker still staging there would die at mktemp.
    shim_dir, _, _ = _clone_shims(tmp_path)
    env = _jobs_env(tmp_path)
    env["TMPDIR"] = str(tmp_path / "no-such-tmp")
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "clone",
        "--repo",
        "acme/widget",
        "--json",
        env=env,
        path_prepend=shim_dir,
    )
    assert result.returncode == 0, result.stderr
    ref = json.loads(result.stdout)
    _wait_for_job(tmp_path, ref["job_id"])

    status = _run_remo_host(
        rendered_script,
        tmp_path,
        "jobs",
        "status",
        "--job",
        ref["job_id"],
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert json.loads(status.stdout)["state"] == "succeeded"
    projects = tmp_path / "projects"
    assert (projects / "widget").is_dir()
    # The hidden staging dir was cleaned up on exit.
    assert not list(projects.glob(".remo-clone.*"))


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_sessions_list_excludes_hidden_dirs(
    rendered_script: Path, tmp_path: Path
) -> None:
    # A half-finished clone stages in a hidden dir inside the projects root;
    # sessions list must never surface it (the "never visible" invariant).
    (tmp_path / "projects" / ".remo-clone.abc123" / "repo").mkdir(parents=True)
    result = _run_remo_host(rendered_script, tmp_path, "sessions", "list", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [p["name"] for p in payload["projects"]] == ["alpha", "beta"]


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_delete_rejects_hidden_name_even_when_dir_exists(
    rendered_script: Path, tmp_path: Path
) -> None:
    # Hidden dirs are the host's own staging area — never addressable as
    # projects even though the directory exists on disk.
    staging = tmp_path / "projects" / ".remo-clone.abc123"
    staging.mkdir(parents=True)
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "delete",
        "--project",
        ".remo-clone.abc123",
        "--json",
    )
    assert result.returncode == 3
    assert "hidden" in result.stderr
    assert staging.is_dir()


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_clone_missing_repo_is_usage_error(
    rendered_script: Path, tmp_path: Path
) -> None:
    result = _run_remo_host(
        rendered_script, tmp_path, "projects", "clone", "--json", env=_jobs_env(tmp_path)
    )
    assert result.returncode == 2


# --- projects delete --------------------------------------------------------


def _delete_shims(tmp_path: Path) -> tuple[Path, Path, Path]:
    """zellij + docker shims recording argv; docker ps reports one container."""
    shim_dir = tmp_path / "shims"
    zellij_log = tmp_path / "zellij-argv.log"
    docker_log = tmp_path / "docker-argv.log"
    _write_shim(shim_dir, "zellij", f'printf \'%s\\n\' "$*" >> "{zellij_log}"')
    _write_shim(
        shim_dir,
        "docker",
        f'printf \'%s\\n\' "$*" >> "{docker_log}"\n'
        'if [ "$1" = ps ]; then echo cid123; fi',
    )
    return shim_dir, zellij_log, docker_log


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_delete_removes_session_containers_and_dir(
    rendered_script: Path, tmp_path: Path
) -> None:
    shim_dir, zellij_log, docker_log = _delete_shims(tmp_path)
    project_dir = tmp_path / "projects" / "alpha"
    assert project_dir.is_dir()

    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "delete",
        "--project",
        "alpha",
        "--json",
        path_prepend=shim_dir,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"protocol_version": 1, "deleted": "alpha"}
    assert not project_dir.exists()

    zellij_lines = zellij_log.read_text().strip().splitlines()
    assert "kill-session alpha" in zellij_lines
    assert "delete-session --force alpha" in zellij_lines

    docker_lines = docker_log.read_text().strip().splitlines()
    assert docker_lines[0] == (
        f"ps -aq --filter label=devcontainer.local_folder={project_dir}"
    )
    # Containers are force-removed; images are deliberately left as cache.
    assert docker_lines[1] == "rm -f cid123"


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize(
    "bad_name",
    ["does-not-exist", "../etc", "/etc/passwd"],
    ids=["unknown", "traversal", "absolute"],
)
def test_remo_host_delete_rejects_bad_projects(
    rendered_script: Path, tmp_path: Path, bad_name: str
) -> None:
    result = _run_remo_host(
        rendered_script, tmp_path, "projects", "delete", "--project", bad_name, "--json"
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr != ""


# --- projects rebuild -------------------------------------------------------


def _rebuild_shims(tmp_path: Path) -> tuple[Path, Path, Path]:
    shim_dir = tmp_path / "shims"
    dc_log = tmp_path / "devcontainer-argv.log"
    zellij_log = tmp_path / "zellij-argv.log"
    _write_shim(shim_dir, "devcontainer", f'printf \'%s\\n\' "$*" >> "{dc_log}"')
    _write_shim(shim_dir, "zellij", f'printf \'%s\\n\' "$*" >> "{zellij_log}"')
    return shim_dir, dc_log, zellij_log


@pytest.mark.skipif(
    BASH is None or SETSID is None, reason="bash/setsid not available in this sandbox"
)
@pytest.mark.parametrize("no_cache", [False, True], ids=["default", "no-cache"])
def test_remo_host_rebuild_runs_devcontainer_up(
    rendered_script: Path, tmp_path: Path, no_cache: bool
) -> None:
    shim_dir, dc_log, zellij_log = _rebuild_shims(tmp_path)
    args = ["projects", "rebuild", "--project", "beta"]
    if no_cache:
        args.append("--no-cache")
    args.append("--json")

    result = _run_remo_host(
        rendered_script, tmp_path, *args, env=_jobs_env(tmp_path), path_prepend=shim_dir
    )
    assert result.returncode == 0, result.stderr
    ref = json.loads(result.stdout)
    assert ref["protocol_version"] == 1
    assert ref["kind"] == "rebuild"
    assert ref["project"] == "beta"

    _wait_for_job(tmp_path, ref["job_id"])

    project_dir = tmp_path / "projects" / "beta"
    expected = f"up --workspace-folder {project_dir} --remove-existing-container"
    if no_cache:
        expected += " --build-no-cache"
    assert dc_log.read_text().strip() == expected

    # The job kills the project's zellij session before rebuilding.
    zellij_lines = zellij_log.read_text().strip().splitlines()
    assert "kill-session beta" in zellij_lines
    assert "delete-session --force beta" in zellij_lines


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_rebuild_requires_devcontainer_config(
    rendered_script: Path, tmp_path: Path
) -> None:
    # "alpha" has no .devcontainer directory/file.
    result = _run_remo_host(
        rendered_script,
        tmp_path,
        "projects",
        "rebuild",
        "--project",
        "alpha",
        "--json",
        env=_jobs_env(tmp_path),
    )
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_rebuild_unsupported_with_deacon(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "beta" / ".devcontainer").mkdir(parents=True)
    rendered = _render_remo_host(str(projects_dir), devcontainer_cli_bin="deacon")
    script_path = tmp_path / "remo-host"
    script_path.write_text(rendered)
    script_path.chmod(0o755)

    result = _run_remo_host(
        script_path, tmp_path, "projects", "rebuild", "--project", "beta", "--json"
    )
    assert result.returncode == 4
    assert result.stdout == ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_capabilities_deacon_omits_rebuild_operation(tmp_path: Path) -> None:
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    rendered = _render_remo_host(str(projects_dir), devcontainer_cli_bin="deacon")
    script_path = tmp_path / "remo-host"
    script_path.write_text(rendered)
    script_path.chmod(0o755)

    result = _run_remo_host(script_path, tmp_path, "capabilities", "--json")
    assert result.returncode == 0, result.stderr
    operations = json.loads(result.stdout)["operations"]
    assert "projects.rebuild" not in operations
    # Every other new operation is unconditional.
    for op in ("host.stats", "projects.clone", "projects.delete", "jobs.status"):
        assert op in operations


# --- jobs status ------------------------------------------------------------


def _write_job_files(
    tmp_path: Path,
    job_id: str,
    *,
    pid: int,
    log: str = "",
    exit_code: int | None = None,
) -> None:
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir(exist_ok=True)
    meta = {
        "job_id": job_id,
        "kind": job_id.split("-")[0],
        "project": "proj",
        "started_at": "2026-08-20T00:00:00+00:00",
        "pid": pid,
    }
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(meta))
    (jobs_dir / f"{job_id}.log").write_text(log)
    if exit_code is not None:
        (jobs_dir / f"{job_id}.exit").write_text(f"{exit_code}\n")


def _job_status(rendered_script: Path, tmp_path: Path, job_id: str) -> subprocess.CompletedProcess[str]:
    return _run_remo_host(
        rendered_script,
        tmp_path,
        "jobs",
        "status",
        "--job",
        job_id,
        "--json",
        env=_jobs_env(tmp_path),
    )


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_jobs_status_running(rendered_script: Path, tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "30"])
    try:
        _write_job_files(tmp_path, "clone-x-live", pid=proc.pid, log="working...\n")
        result = _job_status(rendered_script, tmp_path, "clone-x-live")
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["protocol_version"] == 1
        assert payload["state"] == "running"
        assert payload["exit_code"] is None
        assert payload["finished_at"] is None
        assert payload["started_at"] == "2026-08-20T00:00:00+00:00"
        assert payload["log_tail"] == "working...\n"
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_jobs_status_succeeded(rendered_script: Path, tmp_path: Path) -> None:
    _write_job_files(tmp_path, "clone-x-ok", pid=1, log="done\n", exit_code=0)
    result = _job_status(rendered_script, tmp_path, "clone-x-ok")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "succeeded"
    assert payload["exit_code"] == 0
    assert payload["finished_at"] is not None


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_jobs_status_failed(rendered_script: Path, tmp_path: Path) -> None:
    _write_job_files(tmp_path, "rebuild-x-bad", pid=1, log="boom\n", exit_code=17)
    result = _job_status(rendered_script, tmp_path, "rebuild-x-bad")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "failed"
    assert payload["exit_code"] == 17


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_jobs_status_interrupted(rendered_script: Path, tmp_path: Path) -> None:
    # A dead pid with no .exit file means the job was interrupted (reboot,
    # kill): reported failed with no exit code.
    proc = subprocess.Popen(["true"])
    proc.wait()
    _write_job_files(tmp_path, "clone-x-dead", pid=proc.pid, log="partial\n")
    result = _job_status(rendered_script, tmp_path, "clone-x-dead")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "failed"
    assert payload["exit_code"] is None
    assert payload["finished_at"] is None
    assert payload["log_tail"] == "partial\n"


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize(
    "job_id",
    ["nope", "../../etc/passwd", "-x"],
    ids=["unknown", "traversal", "leading-dash"],
)
def test_remo_host_jobs_status_unknown_job(
    rendered_script: Path, tmp_path: Path, job_id: str
) -> None:
    result = _job_status(rendered_script, tmp_path, job_id)
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
def test_remo_host_jobs_status_log_tail_capped_at_8192(
    rendered_script: Path, tmp_path: Path
) -> None:
    log = "x" * 20000 + "END"
    _write_job_files(tmp_path, "clone-x-long", pid=1, log=log, exit_code=0)
    result = _job_status(rendered_script, tmp_path, "clone-x-long")
    assert result.returncode == 0, result.stderr
    tail = json.loads(result.stdout)["log_tail"]
    assert len(tail) == 8192
    assert tail.endswith("END")


# --- dispatch ---------------------------------------------------------------


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize("group", ["host", "projects", "jobs"])
def test_remo_host_unsupported_sub_verb_exits_4(
    rendered_script: Path, tmp_path: Path, group: str
) -> None:
    # Mirrors `sessions`: a recognized group with an unknown sub-verb is 4
    # (top-level unknown verbs stay 2 — covered above — which is how the web
    # layer distinguishes "old host tools" from a usage bug).
    result = _run_remo_host(rendered_script, tmp_path, group, "bogus")
    assert result.returncode == 4
    assert result.stdout == ""
    assert result.stderr != ""


@pytest.mark.skipif(BASH is None, reason="bash not available in this sandbox")
@pytest.mark.parametrize("group", ["host", "projects", "jobs"])
def test_remo_host_bare_group_usage_error(
    rendered_script: Path, tmp_path: Path, group: str
) -> None:
    result = _run_remo_host(rendered_script, tmp_path, group)
    assert result.returncode == 2
    assert result.stdout == ""
