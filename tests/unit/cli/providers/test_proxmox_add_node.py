"""US1 T046: assert `remo proxmox add-node` matches incus add-node behavior."""

import stat
import subprocess

from click.testing import CliRunner

from remo_cli.cli.providers.proxmox import proxmox


def _fake_ssh_ok(host, user, command):  # noqa: ARG001
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")


def test_add_node_writes_nodes_yml_0600(tmp_config_dir, mocker):
    mocker.patch("remo_cli.providers.proxmox._ssh_run", side_effect=_fake_ssh_ok)
    runner = CliRunner()
    r = runner.invoke(
        proxmox,
        [
            "add-node", "lab-prox-02",
            "--host", "10.0.0.42",
            "--ssh-user", "root",
            "--admin-sa-fnox-key", "proxmox_lab_prox_02_admin_sa",
        ],
    )
    assert r.exit_code == 0, r.output
    path = tmp_config_dir / "nodes.yml"
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_add_node_conflict_exits_6(tmp_config_dir, mocker):
    mocker.patch("remo_cli.providers.proxmox._ssh_run", side_effect=_fake_ssh_ok)
    runner = CliRunner()
    r1 = runner.invoke(
        proxmox,
        ["add-node", "lab-prox-02", "--host", "1.1.1.1",
         "--ssh-user", "root", "--admin-sa-fnox-key", "k_admin_sa"],
    )
    assert r1.exit_code == 0
    r2 = runner.invoke(
        proxmox,
        ["add-node", "lab-prox-02", "--host", "2.2.2.2",
         "--ssh-user", "root", "--admin-sa-fnox-key", "k_admin_sa"],
    )
    assert r2.exit_code == 6


def test_add_node_helper_install_does_not_touch_helper_file(tmp_config_dir, mocker):
    """Regression for finding 10: pre-creating an empty
    /usr/local/libexec/remo-broker-tokens shadowed the role's copy task
    (force: false), silently breaking per-developer token management.
    The helper-install SSH command must NOT touch/chmod that file — only
    the parent dir + per-developer token dir."""
    captured: list[str] = []

    def _capture(host, user, command):  # noqa: ARG001
        captured.append(command)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="OK\n", stderr="")

    mocker.patch("remo_cli.providers.proxmox._ssh_run", side_effect=_capture)
    runner = CliRunner()
    r = runner.invoke(
        proxmox,
        ["add-node", "lab-prox-02", "--host", "10.0.0.42",
         "--ssh-user", "root", "--admin-sa-fnox-key", "k_admin_sa"],
    )
    assert r.exit_code == 0, r.output
    assert captured, "expected helper-install SSH command"
    cmd = captured[0]
    assert "touch /usr/local/libexec/remo-broker-tokens" not in cmd
    assert "chmod 0755 /usr/local/libexec/remo-broker-tokens" not in cmd
    assert "install -d -m 0755 /usr/local/libexec" in cmd
    assert "/var/lib/remo-broker/instance-tokens/" in cmd
    assert "echo OK" in cmd
