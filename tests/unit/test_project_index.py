"""The project index and the `remo-host projects add|remove|list` verbs (spec 029).

The rendered scripts are EXECUTED (temp HOME, PATH shims), not just read:
resolution order, the shadowed/missing states, malformed-file handling, the
exact stderr messages of every rejection, and that a host with no index file
reports exactly what it reported before the index existed (FR-027, SC-006).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from jinja2 import Environment

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = REPO_ROOT / "ansible" / "roles" / "user_setup" / "templates"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _render(name: str, **ctx: str) -> str:
    ctx.setdefault("dev_workspace_dir", "")
    ctx.setdefault("devcontainer_cli_bin", "devcontainer")
    ctx.setdefault("devcontainer_up_extra_args", "")
    return Environment(autoescape=False).from_string((TEMPLATES / name).read_text()).render(**ctx)


class Host:
    """A temp HOME with the rendered scripts installed beside each other."""

    def __init__(self, root: Path, projects_dir: Path | None) -> None:
        self.root = root
        self.home = root / "home"
        self.bin = self.home / ".local" / "bin"
        self.bin.mkdir(parents=True)
        self.shims = root / "shims"
        self.shims.mkdir()
        self.projects_dir = projects_dir
        self.index = self.home / ".local" / "state" / "remo" / "projects.tsv"
        pd = str(projects_dir) if projects_dir is not None else ""
        (self.bin / "remo-lib.sh").write_text(_render("remo-lib.sh.j2"))
        for name, tpl in (("remo-host", "remo-host.sh.j2"),):
            path = self.bin / name
            path.write_text(_render(tpl, dev_workspace_dir=pd))
            path.chmod(0o755)
        self._shim("docker", "exit 0")
        self._shim("gh", "exit 1")

    def _shim(self, name: str, body: str) -> None:
        path = self.shims / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def env(self) -> dict[str, str]:
        return {
            "PATH": f"{self.shims}:/usr/bin:/bin",
            "HOME": str(self.home),
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
        }

    def write_index(self, text: str) -> None:
        self.index.parent.mkdir(parents=True, exist_ok=True)
        self.index.write_text(text)

    def lib(self, snippet: str) -> subprocess.CompletedProcess[str]:
        """Run *snippet* with remo-lib sourced and PROJECTS_DIR set."""
        pd = str(self.projects_dir) if self.projects_dir is not None else ""
        script = f'PROJECTS_DIR="{pd}"\n. "{self.bin / "remo-lib.sh"}"\n{snippet}\n'
        assert BASH is not None
        return subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=self.env())

    def host(self, *args: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, str(self.bin / "remo-host"), *args],
            capture_output=True,
            text=True,
            env=self.env(),
        )


@pytest.fixture
def host(tmp_path: Path) -> Host:
    pd = tmp_path / "projects"
    (pd / "c").mkdir(parents=True)
    (pd / "d").mkdir()
    (pd / ".hidden").mkdir()
    return Host(tmp_path, pd)


def _rows(out: str) -> list[list[str]]:
    return [line.split("\t") for line in out.splitlines()]


# ---------------------------------------------------------------------------
# remo_project_list / remo_project_path
# ---------------------------------------------------------------------------


def test_list_is_explicit_then_implicit(host: Host, tmp_path: Path) -> None:
    (tmp_path / "elsewhere" / "a").mkdir(parents=True)
    host.write_index(f"a\t{tmp_path}/elsewhere/a\n")
    result = host.lib("remo_project_list")
    assert result.returncode == 0, result.stderr
    pd = host.projects_dir
    assert _rows(result.stdout) == [
        ["a", f"{tmp_path}/elsewhere/a", "index", "ok"],
        ["c", f"{pd}/c", "dir", "ok"],
        ["d", f"{pd}/d", "dir", "ok"],
    ]


def test_explicit_wins_a_name_clash_and_the_implicit_one_is_shadowed(host: Host, tmp_path: Path) -> None:
    (tmp_path / "elsewhere" / "c").mkdir(parents=True)
    host.write_index(f"c\t{tmp_path}/elsewhere/c\n")
    rows = _rows(host.lib("remo_project_list").stdout)
    assert ["c", f"{tmp_path}/elsewhere/c", "index", "ok"] in rows
    assert ["c", f"{host.projects_dir}/c", "dir", "shadowed"] in rows
    path = host.lib("remo_project_path c")
    assert path.stdout.strip() == f"{tmp_path}/elsewhere/c"


def test_explicit_record_pointing_into_projects_dir_is_listed_once(host: Host) -> None:
    host.write_index(f"c\t{host.projects_dir}/c\n")
    names = [r[0] for r in _rows(host.lib("remo_project_list").stdout)]
    assert names.count("c") == 1


def test_projects_dir_folder_added_under_another_name_is_listed_once(host: Host) -> None:
    # `remo local add <projects-dir>/c --name web`: one checkout, one project.
    host.write_index(f"web\t{host.projects_dir}/c\n")
    rows = _rows(host.lib("remo_project_list").stdout)
    assert ["web", f"{host.projects_dir}/c", "index", "ok"] in rows
    assert [r for r in rows if r[1] == f"{host.projects_dir}/c"] == [
        ["web", f"{host.projects_dir}/c", "index", "ok"]
    ]


def test_missing_path_is_listed_missing_and_never_removed(host: Host, tmp_path: Path) -> None:
    host.write_index(f"gone\t{tmp_path}/nope\n")
    rows = _rows(host.lib("remo_project_list").stdout)
    assert ["gone", f"{tmp_path}/nope", "index", "missing"] in rows
    assert host.index.read_text() == f"gone\t{tmp_path}/nope\n"


@pytest.mark.parametrize("content", [None, ""])
def test_empty_or_absent_index_lists_only_implicit(host: Host, content: str | None) -> None:
    if content is not None:
        host.write_index(content)
    rows = _rows(host.lib("remo_project_list").stdout)
    assert [r[0] for r in rows] == ["c", "d"]
    assert {r[2] for r in rows} == {"dir"}


def test_no_projects_dir_means_no_implicit_entries(tmp_path: Path) -> None:
    bare = Host(tmp_path, None)
    (tmp_path / "x").mkdir()
    bare.write_index(f"x\t{tmp_path}/x\n")
    rows = _rows(bare.lib("remo_project_list").stdout)
    assert rows == [["x", f"{tmp_path}/x", "index", "ok"]]
    empty = bare.lib("rm -f \"$REMO_PROJECT_INDEX\"; remo_project_list")
    assert empty.stdout == ""


def test_malformed_line_names_file_and_line_and_is_not_rewritten(host: Host) -> None:
    text = "ok\t/tmp\nthis line has no tab\n"
    host.write_index(text)
    result = host.lib("remo_project_list")
    assert result.returncode == 5
    assert str(host.index) in result.stderr and "line 2" in result.stderr
    # a writer refuses too, and leaves the file alone
    added = host.lib(f'remo_project_add "{host.projects_dir}/c" other')
    assert added.returncode == 5
    assert host.index.read_text() == text


def test_path_resolves_explicit_before_implicit_and_unknown_exits_3(host: Host, tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    host.write_index(f"x\t{tmp_path}/x\n")
    assert host.lib("remo_project_path x").stdout.strip() == f"{tmp_path}/x"
    assert host.lib("remo_project_path d").stdout.strip() == f"{host.projects_dir}/d"
    assert host.lib("remo_project_path nope").returncode == 3
    assert host.lib("remo_project_path .hidden").returncode == 3
    assert host.lib("remo_project_path ../x").returncode == 3


# ---------------------------------------------------------------------------
# projects add / remove / list
# ---------------------------------------------------------------------------


def test_add_success_shape_modes_and_default_name(host: Host, tmp_path: Path) -> None:
    target = tmp_path / "code" / "my proj"  # a space: the CLI quotes, the verb must cope
    target.mkdir(parents=True)
    result = host.host("projects", "add", "--path", str(target), "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "protocol_version": 1,
        "name": "my proj",
        "path": str(target),
        "status": "ok",
    }
    assert host.index.read_text() == f"my proj\t{target}\n"
    assert stat.S_IMODE(host.index.stat().st_mode) == 0o600
    assert stat.S_IMODE(host.index.parent.stat().st_mode) == 0o700
    assert list(host.index.parent.glob(".projects.tsv.*")) == []


def test_add_expands_a_leading_tilde_host_side(host: Host) -> None:
    code = host.home / "code" / "tilde proj"
    code.mkdir(parents=True)
    result = host.host("projects", "add", "--path", "~/code/tilde proj", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["path"] == str(code)
    bare = host.host("projects", "add", "--path", "~", "--name", "homedir", "--json")
    assert bare.returncode == 0, bare.stderr
    assert json.loads(bare.stdout)["path"] == str(host.home)


def test_add_does_not_expand_a_tilde_that_is_not_leading(host: Host) -> None:
    result = host.host("projects", "add", "--path", "/nowhere/~/x", "--json")
    assert result.returncode == 3
    assert "no such directory: /nowhere/~/x" in result.stderr


def test_add_with_name_and_trailing_slash(host: Host, tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    result = host.host("projects", "add", "--path", f"{tmp_path}/w/", "--name", "wk", "--json")
    assert json.loads(result.stdout)["path"] == f"{tmp_path}/w"
    assert host.index.read_text() == f"wk\t{tmp_path}/w\n"


@pytest.mark.parametrize(
    ("bad_name", "message"),
    [
        ("a/b", "invalid project name: path separators are not allowed: a/b"),
        (".dot", "invalid project name: hidden project names (leading '.') are not allowed: .dot"),
        ("..", "invalid project name: path traversal is not allowed: .."),
    ],
)
def test_add_rejects_an_invalid_name(host: Host, tmp_path: Path, bad_name: str, message: str) -> None:
    (tmp_path / "w").mkdir()
    result = host.host("projects", "add", "--path", str(tmp_path / "w"), "--name", bad_name, "--json")
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr.strip() == message
    assert not host.index.exists()


def test_add_rejects_a_taken_name(host: Host, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "a").mkdir(parents=True)
    assert host.host("projects", "add", "--path", str(tmp_path / "a"), "--json").returncode == 0
    result = host.host("projects", "add", "--path", str(tmp_path / "b" / "a"), "--json")
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr.strip() == (
        f"project 'a' already exists at {tmp_path}/a — use --name to pick another"
    )


def test_add_rejects_an_already_indexed_path(host: Host, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    host.host("projects", "add", "--path", str(tmp_path / "a"), "--json")
    result = host.host("projects", "add", "--path", str(tmp_path / "a"), "--name", "other", "--json")
    assert result.returncode == 3
    assert result.stdout == ""
    assert result.stderr.strip() == f"{tmp_path}/a is already registered as 'a'"


def test_add_rejects_a_missing_directory(host: Host, tmp_path: Path) -> None:
    (tmp_path / "file").write_text("x")
    for bad in (tmp_path / "nope", tmp_path / "file"):
        result = host.host("projects", "add", "--path", str(bad), "--json")
        assert result.returncode == 3
        assert result.stdout == ""
        assert result.stderr.strip() == f"no such directory: {bad}"


def test_readding_a_moved_project_replaces_the_missing_record(host: Host, tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    host.host("projects", "add", "--path", str(tmp_path / "old"), "--name", "p", "--json")
    (tmp_path / "old").rmdir()
    result = host.host("projects", "add", "--path", str(tmp_path / "new"), "--name", "p", "--json")
    assert result.returncode == 0, result.stderr
    assert host.index.read_text() == f"p\t{tmp_path}/new\n"


def test_add_requires_json_and_path(host: Host) -> None:
    assert host.host("projects", "add", "--path", "/tmp").returncode == 2
    assert host.host("projects", "add", "--json").returncode == 2


def test_remove_drops_the_record_and_keeps_the_files(host: Host, tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    host.host("projects", "add", "--path", str(tmp_path / "a"), "--json")
    result = host.host("projects", "remove", "--project", "a", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"protocol_version": 1, "name": "a", "removed": True}
    assert host.index.read_text() == ""
    assert (tmp_path / "a").is_dir()


def test_remove_refuses_an_implicit_project(host: Host) -> None:
    result = host.host("projects", "remove", "--project", "c", "--json")
    assert result.returncode == 3
    assert result.stdout == ""
    assert "not an indexed project" in result.stderr
    assert "move or delete the folder" in result.stderr
    assert (host.projects_dir / "c").is_dir()  # type: ignore[operator]


def test_remove_unknown_project_exits_3(host: Host) -> None:
    result = host.host("projects", "remove", "--project", "ghost", "--json")
    assert result.returncode == 3
    assert result.stderr.strip() == "no such project: ghost"


def test_list_json_shape(host: Host, tmp_path: Path) -> None:
    (tmp_path / "w").mkdir()
    host.write_index(f"b\t{tmp_path}/gone\nw\t{tmp_path}/w\nc\t{tmp_path}/w\n")
    result = host.host("projects", "list", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["protocol_version"] == 1
    got = {(p["name"], p["source"], p["status"]) for p in payload["projects"]}
    assert got == {
        ("b", "index", "missing"),
        ("w", "index", "ok"),
        ("c", "index", "ok"),
        ("c", "dir", "shadowed"),
        ("d", "dir", "ok"),
    }
    shadowed = [p for p in payload["projects"] if p["status"] == "shadowed"]
    assert shadowed and all(p["shadowed"] is True for p in shadowed)
    assert host.host("projects", "list").returncode == 2


# ---------------------------------------------------------------------------
# existing verbs through the resolver
# ---------------------------------------------------------------------------


def test_sessions_list_reports_index_projects_and_skips_missing(host: Host, tmp_path: Path) -> None:
    (tmp_path / "ext" / ".devcontainer").mkdir(parents=True)
    host.write_index(f"ext\t{tmp_path}/ext\ngone\t{tmp_path}/gone\n")
    payload = json.loads(host.host("sessions", "list", "--json").stdout)
    by_name = {p["name"]: p for p in payload["projects"]}
    assert set(by_name) == {"c", "d", "ext"}
    assert by_name["ext"]["has_devcontainer"] is True


def test_sessions_attach_resolves_an_index_project(host: Host, tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    host.write_index(f"ext\t{tmp_path}/ext\n")
    launch = host.bin / "project-launch"
    launch.write_text('#!/bin/sh\necho "launch $*"\n')
    launch.chmod(0o755)
    result = host.host("sessions", "attach", "--project", "ext")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "launch --project ext"
    assert host.host("sessions", "attach", "--project", "ghost").returncode == 3


def test_capabilities_advertises_the_index_verbs(host: Host) -> None:
    payload = json.loads(host.host("capabilities", "--json").stdout)
    assert payload["protocol_version"] == 1
    for op in ("projects.add", "projects.remove", "projects.list"):
        assert op in payload["operations"]


def _wait_job(host: Host, job_id: str) -> dict[str, object]:
    deadline = time.time() + 20
    while time.time() < deadline:
        status = json.loads(host.host("jobs", "status", "--job", job_id, "--json").stdout)
        if status["state"] != "running":
            return status
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def _git_shim(host: Host) -> None:
    # `git clone -- URL DEST` creates DEST/.git; anything else succeeds quietly.
    host._shim(
        "git",
        'if [ "$1" = clone ]; then shift; [ "$1" = -- ] && shift; mkdir -p "$2/.git"; fi\nexit 0',
    )


def test_clone_into_projects_dir_appends_a_record(host: Host) -> None:
    _git_shim(host)
    result = host.host("projects", "clone", "--repo", "owner/newrepo", "--json")
    assert result.returncode == 0, result.stderr
    status = _wait_job(host, json.loads(result.stdout)["job_id"])
    assert status["state"] == "succeeded", status
    target = host.projects_dir / "newrepo"  # type: ignore[operator]
    assert (target / ".git").is_dir()
    assert host.index.read_text() == f"newrepo\t{target}\n"
    # present in the index AND under PROJECTS_DIR: still one project
    names = [p["name"] for p in json.loads(host.host("projects", "list", "--json").stdout)["projects"]]
    assert names.count("newrepo") == 1


def test_clone_without_projects_dir_targets_home_projects(tmp_path: Path) -> None:
    bare = Host(tmp_path, None)
    _git_shim(bare)
    result = bare.host("projects", "clone", "--repo", "owner/solo", "--json")
    assert result.returncode == 0, result.stderr
    assert _wait_job(bare, json.loads(result.stdout)["job_id"])["state"] == "succeeded"
    target = bare.home / "projects" / "solo"
    assert (target / ".git").is_dir()
    assert bare.index.read_text() == f"solo\t{target}\n"


def test_clone_refuses_a_name_the_index_already_holds(host: Host, tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    host.write_index(f"taken\t{tmp_path}/x\n")
    result = host.host("projects", "clone", "--repo", "owner/taken", "--json")
    assert result.returncode == 3
    assert "project already exists" in result.stderr


def test_delete_removes_the_checkout_and_the_record(host: Host, tmp_path: Path) -> None:
    (tmp_path / "ext").mkdir()
    (tmp_path / "ext" / "f").write_text("x")
    host.write_index(f"ext\t{tmp_path}/ext\nkeep\t{tmp_path}\n")
    result = host.host("projects", "delete", "--project", "ext", "--json")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "ext").exists()
    assert host.index.read_text() == f"keep\t{tmp_path}\n"


def test_rebuild_resolves_an_index_project(host: Host, tmp_path: Path) -> None:
    (tmp_path / "ext" / ".devcontainer").mkdir(parents=True)
    host.write_index(f"ext\t{tmp_path}/ext\n")
    result = host.host("projects", "rebuild", "--project", "ext", "--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["project"] == "ext"


# ---------------------------------------------------------------------------
# T034: a remote-style host is unchanged
# ---------------------------------------------------------------------------

# Captured from the unmodified (pre-029) remo-host.sh.j2 against the same
# fixture: PROJECTS_DIR set, no index file, a fake docker that lists nothing.
_BASELINE = (
    '{"protocol_version":1,"projects_root":"@PD@","projects":['
    '{"name":"alpha","has_devcontainer":false,"zellij_state":"absent","devcontainer_running":"unknown",'
    '"git_tracked":false,"git_dirty":false,"git_ahead":0,"git_behind":0},'
    '{"name":"beta","has_devcontainer":true,"zellij_state":"absent","devcontainer_running":"stopped",'
    '"git_tracked":false,"git_dirty":false,"git_ahead":0,"git_behind":0},'
    '{"name":"delta","has_devcontainer":true,"zellij_state":"absent","devcontainer_running":"stopped",'
    '"git_tracked":false,"git_dirty":false,"git_ahead":0,"git_behind":0},'
    '{"name":"gamma","has_devcontainer":false,"zellij_state":"absent","devcontainer_running":"unknown",'
    '"git_tracked":true,"git_dirty":true,"git_ahead":0,"git_behind":0}]}\n'
)


def test_remote_style_sessions_list_is_byte_identical_to_before(tmp_path: Path) -> None:
    pd = tmp_path / "projects"
    (pd / "alpha").mkdir(parents=True)
    (pd / "beta" / ".devcontainer").mkdir(parents=True)
    (pd / "delta").mkdir()
    (pd / "delta" / ".devcontainer.json").write_text("{}")
    (pd / "gamma").mkdir()
    (pd / ".hidden").mkdir()
    host = Host(tmp_path, pd)
    subprocess.run(["git", "init", "-q", str(pd / "gamma")], check=True, env=host.env())
    (pd / "gamma" / "f").write_text("x")
    assert not host.index.exists()
    result = host.host("sessions", "list", "--json")
    assert result.returncode == 0, result.stderr
    assert result.stdout == _BASELINE.replace("@PD@", str(pd))


# ---------------------------------------------------------------------------
# lib helpers
# ---------------------------------------------------------------------------


def test_lib_is_sourceable_without_side_effects(host: Host) -> None:
    result = host.lib("echo ok")
    assert result.returncode == 0 and result.stdout.strip() == "ok" and result.stderr == ""
    assert not os.path.exists(host.index)


# ---------------------------------------------------------------------------
# project-menu: a shadowed row is never launched or deleted by its name
# ---------------------------------------------------------------------------


def _menu(host: Host, selections: list[tuple[str, str]], stdin: str = "") -> subprocess.CompletedProcess[str]:
    """Run the rendered project-menu with an fzf shim that answers *selections*
    (key, row) in order, then exits 1 (Esc). zellij is a logging shim."""
    pd = str(host.projects_dir) if host.projects_dir is not None else ""
    menu = host.bin / "project-menu"
    menu.write_text(_render("project-menu.sh.j2", dev_workspace_dir=pd, remo_version=""))
    menu.chmod(0o755)
    answers = host.root / "answers"
    answers.mkdir(exist_ok=True)
    for i, (key, row) in enumerate(selections):
        (answers / str(i)).write_text(f"{key}\n{row}\n")
    counter = host.root / "fzf-count"
    counter.write_text("0")
    host._shim(
        "fzf",
        f'cat >/dev/null\nn=$(cat "{counter}")\necho $((n + 1)) >"{counter}"\n'
        f'[ -f "{answers}/$n" ] || exit 1\nprintf "%s" "$(cat "{answers}/$n")"',
    )
    host._shim("zellij", f'echo "zellij $*" >>"{host.root}/zellij.log"; exit 0')
    host._shim("sleep", "exit 0")
    assert BASH is not None
    return subprocess.run(
        [BASH, str(menu)], capture_output=True, text=True, env=host.env(), input=stdin, timeout=60
    )


def test_menu_delete_on_a_shadowed_row_never_deletes_the_index_projects_folder(
    host: Host, tmp_path: Path
) -> None:
    explicit = tmp_path / "elsewhere" / "c"
    explicit.mkdir(parents=True)
    host.write_index(f"c\t{explicit}\n")
    # The user highlights the shadowed PROJECTS_DIR/c row, presses d, and
    # confirms; the name "c" resolves to the INDEX record, so acting on it
    # would delete the other checkout.
    result = _menu(host, [("d", "c \033[33m⚠ shadowed\033[0m")], stdin="delete\n")
    assert result.returncode == 1  # the shim's Esc on the next round
    assert explicit.is_dir()
    assert (host.projects_dir / "c").is_dir()
    assert "hidden by an index entry" in result.stdout


def test_menu_launches_a_project_whose_name_contains_shadowed(host: Host) -> None:
    (host.projects_dir / "shadowed-realm").mkdir()
    result = _menu(host, [("", "shadowed-realm")])
    assert "hidden by an index entry" not in result.stdout
    log = (host.root / "zellij.log").read_text()
    assert "attach --create shadowed-realm" in log
