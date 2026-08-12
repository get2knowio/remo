"""Black-box tests for the nested-overlayfs devcontainer shim (#160, #171).

``roles/nested_docker/templates/devcontainer-shim.sh.j2`` is installed at
``/usr/local/bin/devcontainer`` on hosts whose kernel refuses nested overlayfs
mounts (OrbStack machines). It picks the Docker build environment per
invocation because the devcontainer CLI's two builders need contradictory
settings there: ``docker compose build`` honours ``DOCKER_BUILDKIT=0`` and
falls back to a classic builder that rejects the ``additional_contexts`` the
CLI injects for Features, while the ``updateUID`` stage's plain ``docker
build`` needs exactly that setting to avoid the daemon's embedded BuildKit.

Every branch is exercised for real: render the template, syntax-check it with
``bash -n``, then run it against a stub CLI that reports the argv and
environment it was handed. That is the only way to test a decision made in
shell — reading the script proves nothing about what bash does with it.

The parse-only Jinja check for this template (and every other) lives in
``test_ansible_templates.py``; this module covers behaviour.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
ROLE_ROOT = REPO_ROOT / "ansible" / "roles" / "nested_docker"
SHIM_TEMPLATE = ROLE_ROOT / "templates" / "devcontainer-shim.sh.j2"
ROLE_DEFAULTS = ROLE_ROOT / "defaults" / "main.yml"
GROUP_VARS = REPO_ROOT / "ansible" / "group_vars" / "all.yml"
CONFIGURE_DEV_TOOLS = REPO_ROOT / "ansible" / "tasks" / "configure_dev_tools.yml"

BASH = shutil.which("bash")
PYTHON3 = shutil.which("python3")

pytestmark = pytest.mark.skipif(
    BASH is None or PYTHON3 is None,
    reason="the shim is a bash script delegating to a python3 stub CLI",
)

BUILDER_NAME = "remo-native"

# A devcontainer.json that pulls in Compose, and one that does not. Both carry
# a JSONC comment, because the real files do and the shim greps rather than
# parses.
COMPOSE_CONFIG = """\
{
    // Compose-based: the CLI will run `docker compose build`.
    "name": "site",
    "dockerComposeFile": "docker-compose.yml",
    "service": "app",
    "workspaceFolder": "/workspaces/site"
}
"""

IMAGE_CONFIG = """\
{
    // Image-based: the CLI will run `docker buildx build`.
    "name": "api",
    "image": "mcr.microsoft.com/devcontainers/base:ubuntu"
}
"""


# ---------------------------------------------------------------------------
# Rendering / running helpers
# ---------------------------------------------------------------------------


def _render_shim(real_cli: Path, pinned_uid: int) -> str:
    source = SHIM_TEMPLATE.read_text()
    template = Environment(autoescape=False).from_string(source)
    return template.render(
        nested_docker_real_cli_path=str(real_cli),
        nested_docker_builder_name=BUILDER_NAME,
        nested_docker_pinned_uid=pinned_uid,
    )


def _write_stub_cli(path: Path) -> Path:
    """A stand-in for @devcontainers/cli that reports how it was invoked.

    Exits 0 unless handed a magic ``--stub-exit N`` argument, which the
    exit-code propagation test uses.
    """
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "argv = sys.argv[1:]\n"
        "print(json.dumps({\n"
        '    "args": argv,\n'
        '    "env": {k: os.environ.get(k) for k in\n'
        '            ("DOCKER_BUILDKIT", "COMPOSE_BAKE", "BUILDX_BUILDER")},\n'
        "}))\n"
        'if "--stub-exit" in argv:\n'
        '    sys.exit(int(argv[argv.index("--stub-exit") + 1]))\n'
    )
    path.chmod(0o755)
    return path


@pytest.fixture
def shim_env(tmp_path: Path) -> dict[str, Path]:
    """A rendered shim, a stub CLI, and two fixture projects.

    Layout:
      bin/devcontainer          - the shim (named exactly as installed)
      real/devcontainer         - the stub the shim must delegate to
      projects/compose/         - .devcontainer/devcontainer.json, Compose-based
      projects/image/           - .devcontainer/devcontainer.json, image-based
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    real_cli = _write_stub_cli(real_dir / "devcontainer")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "devcontainer"
    shim.write_text(_render_shim(real_cli, os.getuid()))
    shim.chmod(0o755)

    for name, body in (("compose", COMPOSE_CONFIG), ("image", IMAGE_CONFIG)):
        cfg_dir = tmp_path / "projects" / name / ".devcontainer"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "devcontainer.json").write_text(body)

    return {
        "shim": shim,
        "real": real_cli,
        "bin": bin_dir,
        "projects": tmp_path / "projects",
        "tmp": tmp_path,
    }


def _run(shim: Path, *args: str, cwd: Path | None = None, path_prefix: Path | None = None):
    env = dict(os.environ)
    # Never let a value inherited from the developer's own shell masquerade as
    # something the shim set.
    for key in ("DOCKER_BUILDKIT", "COMPOSE_BAKE", "BUILDX_BUILDER"):
        env.pop(key, None)
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [str(shim), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=30,
    )


def _invocation(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# The script itself
# ---------------------------------------------------------------------------


def test_shim_is_syntactically_valid_bash(shim_env: dict[str, Path]) -> None:
    result = subprocess.run(
        [BASH, "-n", str(shim_env["shim"])], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# The four rows of #171's verification table
# ---------------------------------------------------------------------------


def test_compose_project_up_gets_buildkit_bake_and_skips_update_uid(
    shim_env: dict[str, Path],
) -> None:
    result = _run(
        shim_env["shim"],
        "up",
        "--workspace-folder",
        str(shim_env["projects"] / "compose"),
    )
    call = _invocation(result)
    assert call["env"]["DOCKER_BUILDKIT"] == "1"
    assert call["env"]["COMPOSE_BAKE"] == "1"
    assert call["env"]["BUILDX_BUILDER"] == BUILDER_NAME
    assert call["args"][-2:] == ["--update-remote-user-uid-default", "never"]


def test_image_based_project_up_keeps_classic_builder_and_untouched_args(
    shim_env: dict[str, Path],
) -> None:
    workspace = str(shim_env["projects"] / "image")
    result = _run(shim_env["shim"], "up", "--workspace-folder", workspace)
    call = _invocation(result)
    assert call["env"]["DOCKER_BUILDKIT"] == "0"
    # COMPOSE_BAKE must stay unset: it is meaningless without BUILDKIT=1 and
    # setting it here would be a claim this path does not make.
    assert call["env"]["COMPOSE_BAKE"] is None
    assert call["env"]["BUILDX_BUILDER"] == BUILDER_NAME
    assert call["args"] == ["up", "--workspace-folder", workspace]


def test_compose_project_exec_sets_env_but_never_the_uid_flag(
    shim_env: dict[str, Path],
) -> None:
    """`--update-remote-user-uid-default` is only valid on `up` and `build`.

    Appending it to `exec` makes the CLI reject the whole command — and
    project-launch drives `exec`, so getting this wrong would break every
    session launch rather than just a build.
    """
    workspace = str(shim_env["projects"] / "compose")
    result = _run(
        shim_env["shim"], "exec", "--workspace-folder", workspace, "bash", "-lc", "true"
    )
    call = _invocation(result)
    assert call["env"]["DOCKER_BUILDKIT"] == "1"
    assert call["env"]["COMPOSE_BAKE"] == "1"
    assert "--update-remote-user-uid-default" not in call["args"]
    assert call["args"] == ["exec", "--workspace-folder", workspace, "bash", "-lc", "true"]


@pytest.mark.parametrize(
    "user_args",
    [
        ["--update-remote-user-uid-default", "on"],
        ["--update-remote-user-uid-default=on"],
    ],
    ids=["space-separated", "equals-form"],
)
def test_explicit_uid_default_is_respected_not_duplicated(
    shim_env: dict[str, Path], user_args: list[str]
) -> None:
    result = _run(
        shim_env["shim"],
        "up",
        "--workspace-folder",
        str(shim_env["projects"] / "compose"),
        *user_args,
    )
    call = _invocation(result)
    # Count both spellings: the `=`-form is a single argv token, so an exact
    # match on the flag name alone would report zero and pass vacuously.
    occurrences = [
        a for a in call["args"] if a.startswith("--update-remote-user-uid-default")
    ]
    assert occurrences == user_args[:1]
    assert "never" not in call["args"]
    assert call["args"][-len(user_args):] == user_args


# ---------------------------------------------------------------------------
# Locating the project's configuration
# ---------------------------------------------------------------------------


def test_equals_form_workspace_folder_is_parsed(shim_env: dict[str, Path]) -> None:
    result = _run(
        shim_env["shim"],
        "up",
        f"--workspace-folder={shim_env['projects'] / 'compose'}",
    )
    assert _invocation(result)["env"]["DOCKER_BUILDKIT"] == "1"


def test_workspace_folder_defaults_to_the_working_directory(
    shim_env: dict[str, Path],
) -> None:
    result = _run(shim_env["shim"], "up", cwd=shim_env["projects"] / "compose")
    assert _invocation(result)["env"]["DOCKER_BUILDKIT"] == "1"


def test_explicit_config_flag_wins_over_the_workspace(
    shim_env: dict[str, Path],
) -> None:
    """`--config` names the file directly, so the workspace's own
    devcontainer.json must not be consulted."""
    result = _run(
        shim_env["shim"],
        "up",
        "--workspace-folder",
        str(shim_env["projects"] / "image"),
        "--config",
        str(shim_env["projects"] / "compose" / ".devcontainer" / "devcontainer.json"),
    )
    assert _invocation(result)["env"]["DOCKER_BUILDKIT"] == "1"


def test_root_level_devcontainer_json_is_found(shim_env: dict[str, Path]) -> None:
    project = shim_env["tmp"] / "rootlevel"
    project.mkdir()
    (project / ".devcontainer.json").write_text(COMPOSE_CONFIG)
    result = _run(shim_env["shim"], "up", "--workspace-folder", str(project))
    assert _invocation(result)["env"]["DOCKER_BUILDKIT"] == "1"


def test_per_configuration_subfolder_is_found(shim_env: dict[str, Path]) -> None:
    """A project may hold several configurations under
    ``.devcontainer/<name>/devcontainer.json``; one being Compose-based is
    enough to need the Compose environment."""
    project = shim_env["tmp"] / "multi"
    (project / ".devcontainer" / "full").mkdir(parents=True)
    (project / ".devcontainer" / "full" / "devcontainer.json").write_text(COMPOSE_CONFIG)
    result = _run(shim_env["shim"], "up", "--workspace-folder", str(project))
    assert _invocation(result)["env"]["DOCKER_BUILDKIT"] == "1"


def test_project_without_any_devcontainer_json_is_treated_as_image_based(
    shim_env: dict[str, Path],
) -> None:
    project = shim_env["tmp"] / "bare"
    project.mkdir()
    result = _run(shim_env["shim"], "up", "--workspace-folder", str(project))
    call = _invocation(result)
    assert call["env"]["DOCKER_BUILDKIT"] == "0"
    assert call["args"] == ["up", "--workspace-folder", str(project)]


# ---------------------------------------------------------------------------
# The UID guard
# ---------------------------------------------------------------------------


def test_uid_mismatch_declines_to_skip_update_uid_and_says_why(
    shim_env: dict[str, Path], tmp_path: Path
) -> None:
    """Skipping updateUID is only safe when the host UID already matches the
    image user's. At any other UID the stage may genuinely have to remap the
    container user, and skipping it would leave bind-mounted files unwritable —
    so the shim declines and explains, rather than silently trading one broken
    build for silently broken permissions.
    """
    other_uid = os.getuid() + 1
    shim = tmp_path / "shim-other-uid"
    shim.write_text(_render_shim(shim_env["real"], other_uid))
    shim.chmod(0o755)

    result = _run(
        shim, "up", "--workspace-folder", str(shim_env["projects"] / "compose")
    )
    call = _invocation(result)
    assert "--update-remote-user-uid-default" not in call["args"]
    # The Compose environment is still applied — the guard is about updateUID,
    # not about whether Compose can build at all.
    assert call["env"]["DOCKER_BUILDKIT"] == "1"
    assert call["env"]["COMPOSE_BAKE"] == "1"
    assert "updateUID" in result.stderr
    assert "--update-remote-user-uid-default never" in result.stderr


def test_pinned_uid_default_matches_the_user_setup_pin() -> None:
    """The default is not arbitrary: user_setup pins remo_user to UID 1000 so
    it matches the devcontainer `vscode`/`node` user, and that match is the
    entire reason skipping updateUID is safe."""
    defaults = yaml.safe_load(ROLE_DEFAULTS.read_text())
    assert defaults["nested_docker_pinned_uid"] == 1000

    user_setup = (
        REPO_ROOT / "ansible" / "roles" / "user_setup" / "tasks" / "main.yml"
    ).read_text()
    assert "uid: 1000" in user_setup


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


def test_shim_does_not_recurse_when_it_shadows_the_real_cli_on_path(
    shim_env: dict[str, Path],
) -> None:
    """The installed shim is *named* devcontainer and sits ahead of the real
    binary on PATH. Delegating via `command devcontainer` would find the shim
    again and fork-bomb, so it must call the real CLI by absolute path.
    """
    result = _run(
        shim_env["shim"],
        "up",
        "--workspace-folder",
        str(shim_env["projects"] / "compose"),
        path_prefix=shim_env["bin"],
    )
    call = _invocation(result)
    assert call["args"][0] == "up"


def test_exit_code_from_the_real_cli_is_propagated(shim_env: dict[str, Path]) -> None:
    result = _run(
        shim_env["shim"],
        "up",
        "--workspace-folder",
        str(shim_env["projects"] / "image"),
        "--stub-exit",
        "7",
    )
    assert result.returncode == 7


def test_leading_flag_is_not_mistaken_for_a_subcommand(
    shim_env: dict[str, Path],
) -> None:
    result = _run(shim_env["shim"], "--version")
    call = _invocation(result)
    assert call["args"] == ["--version"]
    assert "--update-remote-user-uid-default" not in call["args"]


def test_missing_real_cli_fails_loudly_with_a_remedy(tmp_path: Path) -> None:
    shim = tmp_path / "orphan-shim"
    shim.write_text(_render_shim(tmp_path / "nowhere" / "devcontainer", os.getuid()))
    shim.chmod(0o755)
    result = _run(shim, "up")
    assert result.returncode == 127
    assert "npm install -g @devcontainers/cli" in result.stderr


# ---------------------------------------------------------------------------
# Wiring: detection, placement, inclusion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kernel", "expected"),
    [
        ("7.0.11-orbstack-00360-gc9bc4d96ac70", "True"),
        ("7.0.2-2-pve", "False"),
        ("6.8.0-51-generic", "False"),
        ("", "False"),
    ],
)
def test_nested_overlayfs_detection_expression(kernel: str, expected: str) -> None:
    """`-orbstack-` in the kernel release is the discriminator. A Proxmox
    guest's `-pve` must not trip it, and a host with no fact gathered must fall
    through to False rather than raising."""
    raw = yaml.safe_load(GROUP_VARS.read_text())["docker_nested_overlayfs"]
    rendered = Environment(autoescape=False).from_string(raw).render(ansible_kernel=kernel)
    assert rendered.strip() == expected


def test_detection_expression_survives_an_undefined_kernel_fact() -> None:
    """Constitution principle V: registered/gathered values are accessed
    through `| default()`, so a playbook that skipped fact gathering degrades
    to "not affected" instead of failing the whole configure."""
    raw = yaml.safe_load(GROUP_VARS.read_text())["docker_nested_overlayfs"]
    rendered = Environment(autoescape=False).from_string(raw).render()
    assert rendered.strip() == "False"


def test_shim_is_installed_on_the_system_path_not_under_local_bin() -> None:
    """~/.local/bin is added to PATH inside ~/.bashrc, which returns early for
    non-interactive shells — so a shim there would miss
    `ssh host 'devcontainer up …'`, the exact case it exists to fix, while
    passing every interactive test. /usr/local/bin is on the default PATH and
    precedes /usr/bin.
    """
    defaults = yaml.safe_load(ROLE_DEFAULTS.read_text())
    assert defaults["nested_docker_shim_path"] == "/usr/local/bin/devcontainer"
    assert ".local/bin" not in defaults["nested_docker_shim_path"]


def test_configure_dev_tools_includes_the_role_behind_the_detection_gate() -> None:
    tasks = yaml.safe_load(CONFIGURE_DEV_TOOLS.read_text())
    matching = [
        t
        for t in tasks
        if (t.get("ansible.builtin.include_role") or {}).get("name") == "nested_docker"
    ]
    assert len(matching) == 1, "nested_docker must be included exactly once"
    assert "docker_nested_overlayfs" in matching[0]["when"]

    # It has to run after user_setup: the buildx builder is created as
    # remo_user, and that account (and its docker group membership) does not
    # exist until user_setup has run.
    names = [t.get("name", "") for t in tasks]
    assert names.index("Configure user environment") < names.index(matching[0]["name"])
