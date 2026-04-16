"""Tests for remo.core.config module."""

import os

import pytest

from remo_cli.core.config import (
    get_ansible_dir,
    get_known_hosts_path,
    get_project_root,
    get_remo_home,
    is_verbose,
)


# -----------------------------------------------------------------------
# get_remo_home()
# -----------------------------------------------------------------------


class TestGetRemoHome:
    """Resolution of the remo config directory."""

    def test_remo_home_env_takes_priority(self, tmp_path, monkeypatch):
        """REMO_HOME env var takes priority over all other resolution methods."""
        custom_dir = tmp_path / "custom_remo"
        monkeypatch.setenv("REMO_HOME", str(custom_dir))
        # Also set XDG to prove REMO_HOME wins
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        result = get_remo_home()
        assert result == custom_dir
        assert result.is_dir()

    def test_xdg_config_home_fallback(self, tmp_path, monkeypatch):
        """When REMO_HOME is unset, XDG_CONFIG_HOME/remo is used."""
        monkeypatch.delenv("REMO_HOME", raising=False)
        xdg_dir = tmp_path / "xdg_config"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
        result = get_remo_home()
        assert result == xdg_dir / "remo"
        assert result.is_dir()

    def test_default_home_config_remo(self, tmp_path, monkeypatch):
        """When REMO_HOME and XDG_CONFIG_HOME are both unset, ~/.config/remo is used."""
        monkeypatch.delenv("REMO_HOME", raising=False)
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        result = get_remo_home()
        assert result == tmp_path / ".config" / "remo"
        assert result.is_dir()

    def test_creates_directory_if_missing(self, tmp_path, monkeypatch):
        """The config directory is created if it does not exist."""
        config_dir = tmp_path / "nonexistent" / "deep" / "path"
        monkeypatch.setenv("REMO_HOME", str(config_dir))
        assert not config_dir.exists()
        result = get_remo_home()
        assert result == config_dir
        assert result.is_dir()

    def test_existing_directory_is_not_recreated(self, tmp_path, monkeypatch):
        """An existing directory is returned without error."""
        config_dir = tmp_path / "existing"
        config_dir.mkdir()
        monkeypatch.setenv("REMO_HOME", str(config_dir))
        result = get_remo_home()
        assert result == config_dir
        assert result.is_dir()


# -----------------------------------------------------------------------
# get_known_hosts_path()
# -----------------------------------------------------------------------


class TestGetKnownHostsPath:
    """Path to the known_hosts registry file."""

    def test_returns_known_hosts_under_remo_home(self, tmp_path, monkeypatch):
        """Returns remo_home / 'known_hosts'."""
        config_dir = tmp_path / "remo_cfg"
        monkeypatch.setenv("REMO_HOME", str(config_dir))
        result = get_known_hosts_path()
        assert result == config_dir / "known_hosts"
        # The parent directory should have been created by get_remo_home().
        assert result.parent.is_dir()


# -----------------------------------------------------------------------
# is_verbose()
# -----------------------------------------------------------------------


class TestIsVerbose:
    """REMO_VERBOSE flag checking."""

    def test_returns_true_when_set_to_1(self, monkeypatch):
        """Returns True only when REMO_VERBOSE is '1'."""
        monkeypatch.setenv("REMO_VERBOSE", "1")
        assert is_verbose() is True

    def test_returns_false_when_set_to_0(self, monkeypatch):
        """Returns False when REMO_VERBOSE is '0'."""
        monkeypatch.setenv("REMO_VERBOSE", "0")
        assert is_verbose() is False

    def test_returns_false_when_unset(self, monkeypatch):
        """Returns False when REMO_VERBOSE is not set."""
        monkeypatch.delenv("REMO_VERBOSE", raising=False)
        assert is_verbose() is False

    def test_returns_false_for_non_1_value(self, monkeypatch):
        """Returns False for values other than '1' (e.g. 'true', 'yes')."""
        monkeypatch.setenv("REMO_VERBOSE", "true")
        assert is_verbose() is False

    def test_returns_false_for_empty_string(self, monkeypatch):
        """Returns False when REMO_VERBOSE is an empty string."""
        monkeypatch.setenv("REMO_VERBOSE", "")
        assert is_verbose() is False


# -----------------------------------------------------------------------
# get_project_root()
# -----------------------------------------------------------------------


class TestGetProjectRoot:
    """Resolution of the project root by walking up to pyproject.toml."""

    def test_finds_pyproject_toml(self):
        """Finds the project root where pyproject.toml lives.

        This test relies on the actual project structure being available,
        which is guaranteed in the test environment.
        """
        root = get_project_root()
        assert (root / "pyproject.toml").is_file()

    def test_returns_path_object(self):
        """Returns a Path object."""
        from pathlib import Path

        root = get_project_root()
        assert isinstance(root, Path)


# -----------------------------------------------------------------------
# get_ansible_dir()
# -----------------------------------------------------------------------


class TestGetAnsibleDir:
    """Resolution of the ansible/ directory relative to the project root."""

    def test_finds_ansible_directory(self):
        """Finds the ansible/ directory in the project.

        This test relies on the actual project structure being available.
        """
        ansible_dir = get_ansible_dir()
        assert ansible_dir.is_dir()
        assert ansible_dir.name == "ansible"

    def test_ansible_dir_is_under_project_root(self):
        """The ansible/ directory is located under the project root."""
        ansible_dir = get_ansible_dir()
        project_root = get_project_root()
        # The ansible dir should be relative to or under the project root.
        assert str(ansible_dir).startswith(str(project_root))

    def test_skips_python_package_named_ansible(self, tmp_path, monkeypatch):
        """get_ansible_dir() must not return the ansible-core Python package.

        When remo is installed as a uv tool (non-editable), the walk-up from
        site-packages/remo_cli/core/ hits site-packages/ansible/ — which is
        the ansible-core Python module, not remo's playbooks directory.  The
        fix is to skip any ansible/ candidate that contains __init__.py.
        """
        from remo_cli.core import config as config_mod

        # Build a fake site-packages layout:
        #   fake_root/
        #     site-packages/
        #       ansible/          ← Python package (has __init__.py) — must be skipped
        #         __init__.py
        #       remo_cli/
        #         ansible/        ← remo playbooks (no __init__.py) — must be returned
        #           incus_configure.yml
        #         core/
        #           config.py     ← fake __file__ anchor
        site_packages = tmp_path / "site-packages"
        python_ansible = site_packages / "ansible"
        python_ansible.mkdir(parents=True)
        (python_ansible / "__init__.py").write_text("")  # marks it as a Python package

        remo_ansible = site_packages / "remo_cli" / "ansible"
        remo_ansible.mkdir(parents=True)
        (remo_ansible / "incus_configure.yml").write_text("---")  # remo playbook

        fake_config = site_packages / "remo_cli" / "core" / "config.py"
        fake_config.parent.mkdir(parents=True, exist_ok=True)
        fake_config.write_text("")

        monkeypatch.setattr(config_mod, "__file__", str(fake_config))

        result = config_mod.get_ansible_dir()
        assert result == remo_ansible
        assert not (result / "__init__.py").exists()
