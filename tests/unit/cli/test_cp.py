"""Tests for remo.cli.cp – parse_remote_spec() and the cp Click command."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.cp import cp, parse_remote_spec
from remo_cli.models.host import KnownHost


# ===========================================================================
# parse_remote_spec() – pure unit tests (no mocking needed)
# ===========================================================================


class TestParseRemoteSpecBareColon:
    """Bare-colon prefix (`:path`) should resolve to a remote with empty env name."""

    def test_bare_colon_simple_path(self):
        assert parse_remote_spec(":path") == ("remote", "", "path")

    def test_bare_colon_absolute_path(self):
        assert parse_remote_spec(":/tmp/file.txt") == ("remote", "", "/tmp/file.txt")

    def test_bare_colon_nested_path(self):
        assert parse_remote_spec(":/var/log/app.log") == ("remote", "", "/var/log/app.log")

    def test_bare_colon_relative_path(self):
        assert parse_remote_spec(":relative/dir/") == ("remote", "", "relative/dir/")


class TestParseRemoteSpecNamed:
    """Named remote specs (`name:path`) with 2+ char names."""

    def test_short_name(self):
        assert parse_remote_spec("ab:path") == ("remote", "ab", "path")

    def test_hostname_with_dots(self):
        assert parse_remote_spec("my-host.io:path") == ("remote", "my-host.io", "path")

    def test_name_with_digits(self):
        assert parse_remote_spec("host01:path") == ("remote", "host01", "path")

    def test_name_with_underscores_and_hyphens(self):
        assert parse_remote_spec("my_host-1:path") == ("remote", "my_host-1", "path")

    def test_named_absolute_path(self):
        assert parse_remote_spec("devbox:/home/user/code") == ("remote", "devbox", "/home/user/code")

    def test_named_with_trailing_slash(self):
        assert parse_remote_spec("prod:/var/www/") == ("remote", "prod", "/var/www/")


class TestParseRemoteSpecLocal:
    """Local paths (no colon notation)."""

    def test_simple_relative_path(self):
        assert parse_remote_spec("local/path") == ("local", "", "local/path")

    def test_absolute_path(self):
        assert parse_remote_spec("/home/user/file.txt") == ("local", "", "/home/user/file.txt")

    def test_dot_relative(self):
        assert parse_remote_spec("./file.txt") == ("local", "", "./file.txt")

    def test_parent_relative(self):
        assert parse_remote_spec("../file.txt") == ("local", "", "../file.txt")

    def test_plain_filename(self):
        assert parse_remote_spec("file.txt") == ("local", "", "file.txt")


class TestParseRemoteSpecWindowsDriveLetters:
    """Single-letter prefixes must be treated as local (Windows drive letters)."""

    def test_c_drive(self):
        assert parse_remote_spec("C:path") == ("local", "", "C:path")

    def test_d_drive(self):
        assert parse_remote_spec("D:path") == ("local", "", "D:path")

    def test_lowercase_drive(self):
        assert parse_remote_spec("c:path") == ("local", "", "c:path")

    def test_z_drive(self):
        assert parse_remote_spec("Z:path") == ("local", "", "Z:path")


class TestParseRemoteSpecEdgeCases:
    """Edge cases and boundary conditions."""

    def test_two_char_name_is_remote(self):
        """Two-character names should be treated as remote, not drive letters."""
        spec_type, name, path = parse_remote_spec("ab:path")
        assert spec_type == "remote"
        assert name == "ab"

    def test_colon_in_path_portion(self):
        """Colons in the path portion after the first split should be preserved."""
        spec_type, name, path = parse_remote_spec("host:path:with:colons")
        assert spec_type == "remote"
        assert name == "host"
        # The regex captures everything after the first colon as the path.
        assert path == "path:with:colons"

    def test_name_starting_with_digit(self):
        assert parse_remote_spec("1host:path") == ("remote", "1host", "path")

    def test_empty_string(self):
        """An empty string should be treated as a local path."""
        assert parse_remote_spec("") == ("local", "", "")


# ===========================================================================
# cp Click command – integration tests using CliRunner with mocks
# ===========================================================================


def _make_host(**overrides) -> KnownHost:
    """Create a KnownHost with sensible defaults, overriding as needed."""
    defaults = {
        "type": "incus",
        "name": "test-env",
        "host": "192.168.1.100",
        "user": "remo",
    }
    defaults.update(overrides)
    return KnownHost(**defaults)


class TestCpValidation:
    """Test cp command validation without hitting real hosts or filesystems."""

    def test_missing_arguments_gives_error(self):
        """Providing fewer than 2 arguments should produce an error."""
        runner = CliRunner()
        result = runner.invoke(cp, [":only-one-arg"])
        assert result.exit_code != 0
        assert "at least 2 arguments" in result.output.lower() or "expected" in result.output.lower()

    def test_no_arguments_gives_error(self):
        """Providing zero positional arguments should fail."""
        runner = CliRunner()
        result = runner.invoke(cp, [])
        assert result.exit_code != 0

    def test_mixed_local_and_remote_sources_gives_error(self):
        """Mixing local and remote source paths should be rejected."""
        runner = CliRunner()
        result = runner.invoke(cp, ["local.txt", ":remote.txt", ":/dest/"])
        assert result.exit_code != 0
        assert "mix" in result.output.lower() or "cannot" in result.output.lower()

    def test_both_sides_remote_gives_error(self):
        """Both sources and destination being remote should be rejected."""
        runner = CliRunner()
        result = runner.invoke(cp, [":src.txt", ":dest/"])
        assert result.exit_code != 0
        assert "remote to remote" in result.output.lower() or "one side must be local" in result.output.lower()

    def test_no_remote_side_gives_error(self):
        """All-local paths (no colon notation) should be rejected."""
        runner = CliRunner()
        result = runner.invoke(cp, ["local1.txt", "local2.txt"])
        assert result.exit_code != 0
        assert "no remote" in result.output.lower() or "remote" in result.output.lower()


class TestCpUpload:
    """Test upload (local -> remote) path with mocked dependencies."""

    def test_upload_single_file(self, mocker, tmp_path):
        """A simple upload of one local file to a remote destination."""
        src_file = tmp_path / "upload.txt"
        src_file.write_text("content")

        fake_host = _make_host()
        mock_resolve = mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mock_build_ssh = mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=(["-o", "StrictHostKeyChecking=no"], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, [str(src_file), ":/tmp/"])

        assert result.exit_code == 0
        mock_resolve.assert_called_once()
        mock_build_ssh.assert_called_once_with(fake_host)
        mock_transfer.assert_called_once()
        call_kwargs = mock_transfer.call_args
        assert call_kwargs.kwargs["dest"] == "remo@192.168.1.100:/tmp/"

    def test_upload_named_env(self, mocker, tmp_path):
        """Upload to a named environment resolves via resolve_remo_host_by_name."""
        src_file = tmp_path / "data.bin"
        src_file.write_text("binary")

        fake_host = _make_host(name="mybox")
        mock_resolve_by_name = mocker.patch(
            "remo_cli.cli.cp.resolve_remo_host_by_name", return_value=fake_host,
        )
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, [str(src_file), "mybox:/tmp/"])

        assert result.exit_code == 0
        mock_resolve_by_name.assert_called_once_with("mybox")

    def test_upload_nonexistent_source_gives_error(self, mocker):
        """Uploading a file that does not exist should fail before transfer."""
        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, ["/nonexistent/file.txt", ":/tmp/"])

        assert result.exit_code != 0
        mock_transfer.assert_not_called()

    def test_upload_directory_without_recursive_gives_error(self, mocker, tmp_path):
        """Uploading a directory without -r should fail."""
        src_dir = tmp_path / "mydir"
        src_dir.mkdir()

        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, [str(src_dir), ":/tmp/"])

        assert result.exit_code != 0
        assert "directory" in result.output.lower() or "-r" in result.output
        mock_transfer.assert_not_called()

    def test_upload_directory_with_recursive_succeeds(self, mocker, tmp_path):
        """Uploading a directory with -r should proceed to transfer."""
        src_dir = tmp_path / "mydir"
        src_dir.mkdir()

        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, ["-r", str(src_dir), ":/tmp/"])

        assert result.exit_code == 0
        mock_transfer.assert_called_once()
        assert mock_transfer.call_args.kwargs["recursive"] is True


class TestCpDownload:
    """Test download (remote -> local) path with mocked dependencies."""

    def test_download_single_file(self, mocker, tmp_path):
        """Download a single file from remote to local."""
        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, [":/var/log/app.log", str(tmp_path)])

        assert result.exit_code == 0
        mock_transfer.assert_called_once()
        call_kwargs = mock_transfer.call_args.kwargs
        assert call_kwargs["sources"] == ["remo@192.168.1.100:/var/log/app.log"]
        assert call_kwargs["dest"] == str(tmp_path)

    def test_download_named_env(self, mocker, tmp_path):
        """Download from a named environment uses resolve_remo_host_by_name."""
        fake_host = _make_host(name="prod")
        mock_resolve_by_name = mocker.patch(
            "remo_cli.cli.cp.resolve_remo_host_by_name", return_value=fake_host,
        )
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, ["prod:/etc/config.yml", str(tmp_path)])

        assert result.exit_code == 0
        mock_resolve_by_name.assert_called_once_with("prod")

    def test_download_multiple_remote_sources(self, mocker, tmp_path):
        """Multiple remote source files should all be passed to transfer."""
        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, [":/file1.txt", ":/file2.txt", str(tmp_path)])

        assert result.exit_code == 0
        call_kwargs = mock_transfer.call_args.kwargs
        assert call_kwargs["sources"] == [
            "remo@192.168.1.100:/file1.txt",
            "remo@192.168.1.100:/file2.txt",
        ]


class TestCpTransferFailure:
    """Verify that non-zero rsync return codes propagate as exit codes."""

    def test_nonzero_transfer_rc_propagates(self, mocker, tmp_path):
        """When transfer returns a non-zero code, cp should exit with that code."""
        src_file = tmp_path / "upload.txt"
        src_file.write_text("content")

        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mocker.patch("remo_cli.cli.cp.transfer", return_value=23)  # rsync partial transfer

        runner = CliRunner()
        result = runner.invoke(cp, [str(src_file), ":/tmp/"])

        assert result.exit_code == 23


class TestCpOptions:
    """Verify that CLI options are forwarded correctly."""

    def test_progress_flag_forwarded(self, mocker, tmp_path):
        """The --progress flag should be passed through to transfer()."""
        src_file = tmp_path / "file.txt"
        src_file.write_text("data")

        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, ["--progress", str(src_file), ":/tmp/"])

        assert result.exit_code == 0
        assert mock_transfer.call_args.kwargs["progress"] is True

    def test_recursive_flag_forwarded(self, mocker, tmp_path):
        """The -r flag should be passed through to transfer()."""
        src_dir = tmp_path / "dir"
        src_dir.mkdir()

        fake_host = _make_host()
        mocker.patch("remo_cli.cli.cp.resolve_remo_host", return_value=fake_host)
        mocker.patch(
            "remo_cli.cli.cp.build_ssh_opts",
            return_value=([], "remo@192.168.1.100"),
        )
        mock_transfer = mocker.patch("remo_cli.cli.cp.transfer", return_value=0)

        runner = CliRunner()
        result = runner.invoke(cp, ["-r", str(src_dir), ":/tmp/"])

        assert result.exit_code == 0
        assert mock_transfer.call_args.kwargs["recursive"] is True
