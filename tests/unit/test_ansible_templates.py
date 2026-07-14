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
from pathlib import Path

import pytest
from jinja2 import Environment, TemplateSyntaxError

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "ansible"
REMO_HOST_TEMPLATE = TEMPLATE_ROOT / "roles" / "user_setup" / "templates" / "remo-host.sh.j2"

BASH = shutil.which("bash")


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


def _render_remo_host(projects_dir: str) -> str:
    """Render remo-host.sh.j2 with the minimal context it needs.

    project-launch.sh.j2 only interpolates ``dev_workspace_dir`` (as
    ``PROJECTS_DIR``); remo-host.sh.j2 uses the same single variable.
    """
    source = REMO_HOST_TEMPLATE.read_text()
    template = Environment(autoescape=False).from_string(source)
    return template.render(dev_workspace_dir=projects_dir)


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


def _run_remo_host(script_path: Path, tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(fake_home)}
    return subprocess.run(
        [BASH, str(script_path), *args],
        capture_output=True,
        text=True,
        env=env,
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
    assert payload["operations"] == ["capabilities", "sessions.list", "sessions.attach"]
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
    ],
    ids=["empty", "traversal", "dotdot", "nested-traversal", "absolute", "control-char", "nonexistent"],
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

