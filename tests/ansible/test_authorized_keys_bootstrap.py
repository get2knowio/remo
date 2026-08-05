"""Coverage for the cloud-injected key bootstrap (#121).

`user_setup`'s "Copy authorized_keys from root to remo user (Hetzner)" task had
no provider guard, so it also fired on Proxmox LXCs — where `/home/ubuntu` is
absent and PVE injects a key into root — and `ansible.builtin.copy` *replaces*
the destination. Every key remo had not placed there was destroyed on every
`remo <provider> upgrade`, in practice the `remo-web@…` line that
`remo web push` installs by design.

The fix has three parts, each asserted below:

* **Scoped** — Proxmox and Incus set `user_setup_bootstrap_cloud_keys: false`;
  Hetzner and AWS, which genuinely need the bootstrap, leave it true.
* **Merging** — `ansible.posix.authorized_key` (additive, per-key) replaces
  `ansible.builtin.copy` (wholesale replacement), on both the configure path
  (`user_setup`) and the create path (`proxmox_container`'s `pct push`).
* **First-contact-only** — the bootstrap is gated on the remo user not already
  having a non-empty authorized_keys.

The `proxmox_container` merge is additionally exercised for real: its shell
body is extracted from the task file and run against a temp directory, both for
the "key missing" and "key already present" branches (Principles VI and VII).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSIBLE_DIR = REPO_ROOT / "ansible"
USER_SETUP_TASKS = ANSIBLE_DIR / "roles" / "user_setup" / "tasks" / "main.yml"
USER_SETUP_DEFAULTS = ANSIBLE_DIR / "roles" / "user_setup" / "defaults" / "main.yml"
PROXMOX_CONTAINER_TASKS = ANSIBLE_DIR / "roles" / "proxmox_container" / "tasks" / "main.yml"

BOOTSTRAP_FLAG = "user_setup_bootstrap_cloud_keys"
NEEDS_FACT = "user_setup_needs_key_bootstrap"

#: Playbooks whose configure play must disable the bootstrap. Incus/Proxmox
#: install the key into the remo user's own file at create time. `ssh_configure`
#: has a different reason for the same answer: no cloud provider injected a key
#: on a manually-added host, and root's authorized_keys there belongs to the
#: machine's owner, not to a workspace account remo may copy from.
SCOPED_OFF_PLAYBOOKS = [
    "proxmox_configure.yml",
    "incus_configure.yml",
    "proxmox_site.yml",
    "incus_site.yml",
    "ssh_configure.yml",
]
#: Playbooks that must keep it on: the key only exists under root/ubuntu there.
SCOPED_ON_PLAYBOOKS = [
    "hetzner_configure.yml",
    "aws_configure.yml",
    "hetzner_site.yml",
    "aws_site.yml",
]

AUTHORIZED_KEYS_DEST = "/home/{{ remo_user }}/.ssh/authorized_keys"


def _tasks(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, list), f"{path} is expected to be a flat list of tasks"
    return data


def _by_name(path: Path, name: str) -> dict[str, Any]:
    for task in _tasks(path):
        if task.get("name") == name:
            return task
    raise AssertionError(f"{path}: no task named {name!r}")


def _when_clauses(task: dict[str, Any]) -> list[str]:
    when = task.get("when", [])
    return [when] if isinstance(when, str) else list(when)


class TestUserSetupNoLongerOverwrites:
    def test_no_copy_module_targets_authorized_keys(self) -> None:
        """The regression guard: `copy` replaces, and that is what revoked keys."""
        offenders = [
            task.get("name")
            for task in _tasks(USER_SETUP_TASKS)
            if task.get("ansible.builtin.copy", {}).get("dest") == AUTHORIZED_KEYS_DEST
        ]
        assert offenders == [], (
            "ansible.builtin.copy onto authorized_keys replaces the file wholesale, "
            f"revoking keys remo did not place there (#121): {offenders}"
        )

    def test_bootstrap_uses_the_additive_module(self) -> None:
        task = _by_name(USER_SETUP_TASKS, "Seed the remo user's authorized_keys from the cloud-injected key")
        args = task["ansible.posix.authorized_key"]
        assert args["state"] == "present"
        assert args["exclusive"] is False, (
            "exclusive: true would prune every key not in the cloud-injected set"
        )
        assert args["user"] == "{{ remo_user }}"


class TestUserSetupScoping:
    def test_flag_defaults_to_true(self) -> None:
        """Hetzner/AWS need the bootstrap, so the role default keeps it on."""
        defaults = yaml.safe_load(USER_SETUP_DEFAULTS.read_text())
        assert defaults[BOOTSTRAP_FLAG] is True

    @pytest.mark.parametrize(
        "task_name",
        [
            "Select the cloud-injected key source",
            "Read the cloud-injected authorized_keys",
            "Seed the remo user's authorized_keys from the cloud-injected key",
        ],
    )
    def test_every_mutating_bootstrap_task_is_gated(self, task_name: str) -> None:
        clauses = " ".join(_when_clauses(_by_name(USER_SETUP_TASKS, task_name)))
        assert BOOTSTRAP_FLAG in clauses or NEEDS_FACT in clauses, (
            f"{task_name!r} runs unconditionally — that is exactly how the "
            "'(Hetzner)' task ended up firing on Proxmox (#121)"
        )

    @pytest.mark.parametrize(
        "task_name",
        [
            "Check whether the remo user already has authorized_keys",
            "Check if ubuntu user has authorized_keys (AWS EC2 injects keys to ubuntu)",
            "Check if root has authorized_keys (Hetzner injects keys to root)",
        ],
    )
    def test_probe_stats_stay_unconditional(self, task_name: str) -> None:
        """A `when` here would leave the register holding a skip result, and the
        gates below would have to chain through its absent `.stat` subtree."""
        assert "when" not in _by_name(USER_SETUP_TASKS, task_name)

    def test_first_contact_gate_reads_remo_authorized_keys(self) -> None:
        task = _by_name(USER_SETUP_TASKS, "Decide whether a cloud-injected key bootstrap is still needed")
        expr = task["ansible.builtin.set_fact"][NEEDS_FACT]
        assert "remo_authorized_keys.stat.exists" in expr
        assert "remo_authorized_keys.stat.size" in expr
        assert BOOTSTRAP_FLAG in expr

    def test_registered_stats_are_read_defensively(self) -> None:
        """Principle V — these stats are now conditional, so they can be skipped."""
        text = USER_SETUP_TASKS.read_text()
        for register in ("remo_authorized_keys", "ubuntu_authorized_keys", "root_authorized_keys"):
            for attr in (".stat.exists", ".stat.size"):
                for line in text.splitlines():
                    if f"{register}{attr}" in line:
                        assert "| default(" in line, f"undefended access: {line.strip()}"

    @pytest.mark.parametrize("playbook", SCOPED_OFF_PLAYBOOKS)
    def test_container_providers_disable_the_bootstrap(self, playbook: str) -> None:
        plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
        flags = [
            play.get("vars", {}).get(BOOTSTRAP_FLAG)
            for play in plays
            if BOOTSTRAP_FLAG in (play.get("vars") or {})
        ]
        assert flags == [False], (
            f"{playbook} must set {BOOTSTRAP_FLAG}: false — this provider writes the "
            "key into the remo user's own authorized_keys at create time (#121)"
        )

    @pytest.mark.parametrize("playbook", SCOPED_ON_PLAYBOOKS)
    def test_cloud_providers_keep_the_bootstrap(self, playbook: str) -> None:
        plays = yaml.safe_load((ANSIBLE_DIR / playbook).read_text())
        for play in plays:
            assert BOOTSTRAP_FLAG not in (play.get("vars") or {}), (
                f"{playbook} must leave the bootstrap on: without it a first-contact "
                "provision cannot seed the remo user and locks the operator out"
            )


class TestProxmoxCreatePathMerges:
    def test_pct_push_no_longer_targets_authorized_keys(self) -> None:
        for task in _tasks(PROXMOX_CONTAINER_TASKS):
            cmd = task.get("ansible.builtin.command", {})
            cmd_str = cmd.get("cmd", "") if isinstance(cmd, dict) else ""
            if "pct push" in cmd_str:
                assert "authorized_keys" not in cmd_str, (
                    "pct push onto authorized_keys replaces the file, revoking keys a "
                    "prior `remo web push` installed (#121)"
                )

    def test_merge_task_reports_changed_only_when_it_appended(self) -> None:
        task = _by_name(PROXMOX_CONTAINER_TASKS, "Merge staged pubkey into authorized_keys")
        assert "remo_authorized_keys_changed=1" in task["changed_when"]
        assert "| default('')" in task["changed_when"]

    def test_staging_push_is_never_reported_as_changed(self) -> None:
        """Staging is scratch; only the merge decides whether anything changed."""
        task = _by_name(PROXMOX_CONTAINER_TASKS, "Stage SSH pubkey inside container via pct push")
        assert task["changed_when"] is False


def _merge_script() -> str:
    task = _by_name(PROXMOX_CONTAINER_TASKS, "Merge staged pubkey into authorized_keys")
    argv = task["ansible.builtin.command"]["argv"]
    return argv[-1]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
class TestProxmoxMergeBehavior:
    """Run the shipped merge body for real, both branches.

    The script is rendered for the default `remo` user and then re-pointed at a
    temp home; `install`/`chown` are stubbed because the test does not run as
    root. Everything that matters — append-if-absent, the changed marker, and
    the staging cleanup — is the shipped code.
    """

    KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOperatorKeyForTests operator@laptop"
    WEB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIServiceKeyForTests remo-web@dep-1234"

    def _harness(self, tmp_path: Path) -> tuple[str, Path, Path]:
        script = _merge_script()
        # Rendered by Ansible from role defaults; assert the shape we re-point.
        script = script.replace("{{ proxmox_container_ssh_user | quote }}", "'remo'")
        script = script.replace(
            "{{ proxmox_container_pubkey_staging_path | quote }}",
            f"'{tmp_path / 'staged.pub'}'",
        )
        assert '/home/$user/.ssh' in script
        script = script.replace("/home/$user/.ssh", str(tmp_path / ".ssh"))

        stub_bin = tmp_path / "bin"
        stub_bin.mkdir()
        (stub_bin / "install").write_text('#!/bin/sh\nfor a in "$@"; do :; done\nmkdir -p "$a"\n')
        (stub_bin / "chown").write_text("#!/bin/sh\nexit 0\n")
        for stub in stub_bin.iterdir():
            stub.chmod(0o755)

        return script, tmp_path / "staged.pub", tmp_path / ".ssh" / "authorized_keys"

    def _run(self, tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}")
        return subprocess.run(
            ["bash", "-c", script], capture_output=True, text=True, check=False, env=env
        )

    def _changed(self, result: subprocess.CompletedProcess[str]) -> bool:
        return "remo_authorized_keys_changed=1" in result.stdout

    def test_existing_keys_survive_the_merge(self, tmp_path: Path) -> None:
        """The #121 acceptance case: a prior `remo web push` line is preserved."""
        script, staged, ak = self._harness(tmp_path)
        staged.write_text(self.KEY + "\n")
        ak.parent.mkdir(parents=True)
        ak.write_text(self.WEB_KEY + "\n")

        result = self._run(tmp_path, script)

        assert result.returncode == 0, result.stderr
        assert self._changed(result)
        assert ak.read_text().splitlines() == [self.WEB_KEY, self.KEY]

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """Principle VII: re-running create against a live container changes nothing."""
        script, staged, ak = self._harness(tmp_path)
        staged.write_text(self.KEY + "\n")

        first = self._run(tmp_path, script)
        after_first = ak.read_text()
        staged.write_text(self.KEY + "\n")
        second = self._run(tmp_path, script)

        assert self._changed(first)
        assert not self._changed(second)
        assert ak.read_text() == after_first

    def test_staged_file_is_cleaned_up(self, tmp_path: Path) -> None:
        script, staged, _ = self._harness(tmp_path)
        staged.write_text(self.KEY + "\n")

        self._run(tmp_path, script)

        assert not staged.exists()

    def test_authorized_keys_ends_0600(self, tmp_path: Path) -> None:
        script, staged, ak = self._harness(tmp_path)
        staged.write_text(self.KEY + "\n")

        self._run(tmp_path, script)

        assert ak.stat().st_mode & 0o777 == 0o600

    def test_comments_and_blanks_in_the_staged_file_are_skipped(self, tmp_path: Path) -> None:
        script, staged, ak = self._harness(tmp_path)
        staged.write_text(f"# comment\n\n{self.KEY}\n")

        result = self._run(tmp_path, script)

        assert self._changed(result)
        assert ak.read_text().splitlines() == [self.KEY]
