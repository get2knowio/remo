"""US3 T061: `remo init` backend selection, fnox-missing, age-git warning, interactive identity."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.init import init_command


def test_refuses_when_fnox_missing(tmp_config_dir, mocker):
    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=False)
    runner = CliRunner()
    r = runner.invoke(init_command, ["--backend", "1password", "--non-interactive"])
    assert r.exit_code == 3
    assert "fnox" in r.output.lower()


def test_age_git_requires_accept_downgrade(tmp_config_dir, mocker):
    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=True)
    runner = CliRunner()
    r = runner.invoke(init_command, ["--backend", "age-git", "--non-interactive"])
    assert r.exit_code == 2
    assert "downgrade" in r.output.lower()


def test_age_git_with_accept_downgrade_succeeds(tmp_config_dir, mocker):
    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=True)
    runner = CliRunner()
    r = runner.invoke(
        init_command,
        ["--backend", "age-git", "--accept-downgrade", "--non-interactive"],
    )
    assert r.exit_code == 0, r.output


def test_non_interactive_without_backend_fails(tmp_config_dir, mocker):
    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=True)
    runner = CliRunner()
    r = runner.invoke(init_command, ["--non-interactive"])
    assert r.exit_code == 2


def test_1password_with_unreadable_admin_sa_rejects(tmp_config_dir, mocker):
    from remo_cli.core import fnox as fnox_mod
    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=True)
    mocker.patch(
        "remo_cli.cli.init.fnox.get",
        side_effect=fnox_mod.FnoxError("no such key"),
    )
    runner = CliRunner()
    r = runner.invoke(
        init_command,
        [
            "--backend", "1password",
            "--admin-sa-fnox-key", "missing_admin_sa",
            "--non-interactive",
        ],
    )
    assert r.exit_code == 4
    assert "interactive" in r.output.lower() or "fnox" in r.output.lower()


def test_successful_init_writes_config_0600(tmp_config_dir, mocker):
    import stat

    mocker.patch("remo_cli.cli.init.fnox.is_installed", return_value=True)
    runner = CliRunner()
    r = runner.invoke(init_command, ["--backend", "vault", "--non-interactive"])
    assert r.exit_code == 0, r.output
    cfg = tmp_config_dir / "config.yml"
    assert cfg.exists()
    mode = stat.S_IMODE(cfg.stat().st_mode)
    assert mode == 0o600
    text = cfg.read_text()
    assert "vault" in text
