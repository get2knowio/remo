"""Unit tests for remo_cli.core.remo_host_client module.

No real SSH: subprocess.run is mocked at the module boundary
(`remo_cli.core.remo_host_client.subprocess.run`) so these tests exercise
argv construction, exit-code classification, and JSON parsing/validation in
isolation.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from remo_cli.core.remo_host_client import (
    DEFAULT_PAYLOAD_CAP,
    SUPPORTED_PROTOCOL_RANGE,
    DevcontainerRunning,
    IncompatibleProtocolError,
    MalformedResponseError,
    PayloadTooLargeError,
    ProjectEntry,
    RemoHostCommandError,
    RemoHostExitReason,
    RemoteCapability,
    SshTransportError,
    ZellijState,
    build_remo_host_argv,
    build_remo_host_shell_cmd,
    get_capabilities,
    list_sessions,
)

SSH_PREFIX = ["ssh", "-o", "BatchMode=yes", "remo@example-host"]


def _completed(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


CAPABILITIES_JSON = {
    "protocol_version": 1,
    "host_tools_version": "2.1.0",
    "projects_root": "/home/remo/projects",
    "operations": ["capabilities", "sessions.list", "sessions.attach"],
    "zellij": True,
    "docker": True,
}

SESSIONS_JSON = {
    "protocol_version": 1,
    "projects_root": "/home/remo/projects",
    "projects": [
        {
            "name": "my-api",
            "has_devcontainer": True,
            "zellij_state": "active",
            "devcontainer_running": "running",
        },
        {
            "name": "notes",
            "has_devcontainer": False,
            "zellij_state": "absent",
            "devcontainer_running": "unknown",
        },
    ],
}


# ---------------------------------------------------------------------------
# SUPPORTED_PROTOCOL_RANGE sanity
# ---------------------------------------------------------------------------


def test_supported_protocol_range_is_1_1():
    assert SUPPORTED_PROTOCOL_RANGE == (1, 1)


# ---------------------------------------------------------------------------
# build_remo_host_argv
# ---------------------------------------------------------------------------


class TestBuildRemoHostArgv:
    def test_capabilities(self):
        assert build_remo_host_argv("capabilities") == ["remo-host", "capabilities", "--json"]

    def test_sessions_list(self):
        assert build_remo_host_argv("sessions list") == ["remo-host", "sessions", "list", "--json"]

    def test_sessions_list_json_false_omits_flag(self):
        assert build_remo_host_argv("sessions list", json=False) == ["remo-host", "sessions", "list"]

    def test_sessions_attach_basic(self):
        argv = build_remo_host_argv("sessions attach", project="my-api")
        assert argv == ["remo-host", "sessions", "attach", "--project", "my-api"]

    def test_sessions_attach_requires_project(self):
        with pytest.raises(ValueError):
            build_remo_host_argv("sessions attach")

    @pytest.mark.parametrize(
        "name",
        [
            "my project",       # spaces
            "café",              # unicode
            "-rf",               # leading dash, flag-like
            "--project",         # looks like another flag entirely
            "; rm -rf /",        # shell metacharacters
            "$(whoami)",         # command substitution syntax
        ],
    )
    def test_sessions_attach_project_is_single_intact_argv_element(self, name):
        """The project name must survive as ONE argv element, verbatim.

        This is what makes it safe: subprocess.run(argv) with a list (no
        shell=True) passes each element directly to execve, so a name like
        "-rf" or "$(whoami)" can never be split, glob-expanded, or
        interpreted as a shell construct.
        """
        argv = build_remo_host_argv("sessions attach", project=name)
        # The project value must not have been split on whitespace, and it
        # must land as exactly the single element after the --project flag.
        assert argv == ["remo-host", "sessions", "attach", "--project", name]
        assert len(argv) == 5


class TestBuildRemoHostShellCmd:
    def test_quotes_project_with_space(self):
        cmd = build_remo_host_shell_cmd("sessions attach", project="my project")
        assert cmd == (
            'PATH="$HOME/.local/bin:$PATH" remo-host sessions attach --project \'my project\''
        )

    def test_prefixes_path_so_remote_shell_finds_local_bin(self):
        # ~/.local/bin isn't on a non-interactive ssh shell's PATH, so the
        # command must carry the PATH prefix (unquoted, so the remote shell
        # expands $HOME/$PATH) before remo-host.
        cmd = build_remo_host_shell_cmd("sessions attach", project="api")
        assert cmd.startswith('PATH="$HOME/.local/bin:$PATH" remo-host ')

    def test_quotes_leading_dash_project_safely(self):
        cmd = build_remo_host_shell_cmd("sessions attach", project="-rf")
        # shlex.join always quotes tokens that could be misparsed as options
        # when re-split; critically, re-splitting the command portion must
        # reproduce the exact original argv (the PATH prefix is the first word).
        import shlex

        split = shlex.split(cmd)
        assert split[0].startswith("PATH=")
        assert split[1:] == ["remo-host", "sessions", "attach", "--project", "-rf"]

    def test_quotes_shell_metacharacters(self):
        cmd = build_remo_host_shell_cmd("sessions attach", project="$(whoami); rm -rf /")
        import shlex

        split = shlex.split(cmd)
        assert split[0].startswith("PATH=")
        assert split[1:] == [
            "remo-host",
            "sessions",
            "attach",
            "--project",
            "$(whoami); rm -rf /",
        ]


# ---------------------------------------------------------------------------
# Argv passed to subprocess.run — exact prefix + verb composition
# ---------------------------------------------------------------------------


class TestSubprocessArgvComposition:
    def test_get_capabilities_invokes_expected_argv(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(CAPABILITIES_JSON).encode())

        get_capabilities(SSH_PREFIX)

        called_argv = mock_run.call_args[0][0]
        assert called_argv == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "capabilities",
            "--json",
        ]

    def test_list_sessions_invokes_expected_argv(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(SESSIONS_JSON).encode())

        list_sessions(SSH_PREFIX)

        called_argv = mock_run.call_args[0][0]
        assert called_argv == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "sessions",
            "list",
            "--json",
        ]

    def test_no_shell_true_used(self, mocker):
        """subprocess.run must be called with an argv list, never shell=True."""
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(CAPABILITIES_JSON).encode())

        get_capabilities(SSH_PREFIX)

        assert mock_run.call_args.kwargs.get("shell", False) is False
        assert isinstance(mock_run.call_args[0][0], list)


# ---------------------------------------------------------------------------
# Version negotiation
# ---------------------------------------------------------------------------


class TestVersionNegotiation:
    def test_version_1_is_compatible(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(CAPABILITIES_JSON).encode())

        result = get_capabilities(SSH_PREFIX)

        assert isinstance(result, RemoteCapability)
        assert result.protocol_version == 1

    def test_version_2_is_incompatible(self, mocker):
        payload = {**CAPABILITIES_JSON, "protocol_version": 2}
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(payload).encode())

        with pytest.raises(IncompatibleProtocolError) as exc_info:
            get_capabilities(SSH_PREFIX)

        assert exc_info.value.reported_version == 2
        assert exc_info.value.supported_range == (1, 1)

    def test_version_0_is_incompatible(self, mocker):
        payload = {**CAPABILITIES_JSON, "protocol_version": 0}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(IncompatibleProtocolError):
            get_capabilities(SSH_PREFIX)

    def test_incompatible_protocol_is_distinct_type_not_generic(self, mocker):
        """IncompatibleProtocolError must not be a bare ValueError/generic error
        the caller could confuse with e.g. malformed-JSON or a usage error."""
        payload = {**CAPABILITIES_JSON, "protocol_version": 99}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(IncompatibleProtocolError):
            get_capabilities(SSH_PREFIX)

        # And it must NOT be raised as a bare ValueError/MalformedResponseError.
        assert not issubclass(IncompatibleProtocolError, MalformedResponseError)

    def test_missing_protocol_version_is_malformed_not_incompatible(self, mocker):
        payload = {k: v for k, v in CAPABILITIES_JSON.items() if k != "protocol_version"}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(MalformedResponseError):
            get_capabilities(SSH_PREFIX)


# ---------------------------------------------------------------------------
# Malformed JSON
# ---------------------------------------------------------------------------


class TestMalformedJson:
    def test_invalid_json_raises_typed_error_not_json_decode_error(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=b"{not valid json!!"),
        )

        with pytest.raises(MalformedResponseError) as exc_info:
            get_capabilities(SSH_PREFIX)

        # It should NOT bubble up as an uncaught json.JSONDecodeError.
        assert not isinstance(exc_info.value, json.JSONDecodeError)

    def test_json_array_instead_of_object_is_malformed(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=b"[1, 2, 3]"),
        )

        with pytest.raises(MalformedResponseError):
            get_capabilities(SSH_PREFIX)

    def test_empty_stdout_is_malformed(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=b""),
        )

        with pytest.raises(MalformedResponseError):
            get_capabilities(SSH_PREFIX)

    def test_non_utf8_stdout_is_malformed(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=b"\xff\xfe\x00garbage"),
        )

        with pytest.raises(MalformedResponseError):
            get_capabilities(SSH_PREFIX)

    def test_malformed_json_error_message_is_actionable(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=b"{not valid json!!"),
        )

        with pytest.raises(MalformedResponseError) as exc_info:
            get_capabilities(SSH_PREFIX)

        message = str(exc_info.value)
        assert message  # non-empty
        assert "json" in message.lower() or "malformed" in message.lower()


# ---------------------------------------------------------------------------
# Payload size cap
# ---------------------------------------------------------------------------


class TestPayloadSizeCap:
    def test_default_cap_is_256kib(self):
        assert DEFAULT_PAYLOAD_CAP == 256 * 1024

    def test_oversized_payload_rejected_before_parsing(self, mocker):
        # Build a technically-valid-JSON blob that exceeds the cap, padded
        # with a huge string value. If the cap check ran AFTER parsing this
        # would still succeed (proving the ordering); we assert it is
        # rejected as PayloadTooLargeError, not e.g. a generic MemoryError
        # or a successful parse.
        huge_value = "x" * (DEFAULT_PAYLOAD_CAP + 1024)
        oversized_payload = {**CAPABILITIES_JSON, "host_tools_version": huge_value}
        raw = json.dumps(oversized_payload).encode()
        assert len(raw) > DEFAULT_PAYLOAD_CAP

        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=raw),
        )

        with pytest.raises(PayloadTooLargeError) as exc_info:
            get_capabilities(SSH_PREFIX)

        assert exc_info.value.size == len(raw)
        assert exc_info.value.cap == DEFAULT_PAYLOAD_CAP

    def test_configurable_cap_is_honored(self, mocker):
        raw = json.dumps(CAPABILITIES_JSON).encode()
        small_cap = len(raw) - 1  # just under the actual payload size

        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=raw),
        )

        with pytest.raises(PayloadTooLargeError):
            get_capabilities(SSH_PREFIX, payload_cap=small_cap)

    def test_payload_within_configured_cap_succeeds(self, mocker):
        raw = json.dumps(CAPABILITIES_JSON).encode()
        generous_cap = len(raw) + 1

        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=raw),
        )

        result = get_capabilities(SSH_PREFIX, payload_cap=generous_cap)
        assert result.protocol_version == 1


# ---------------------------------------------------------------------------
# Exit code classification
# ---------------------------------------------------------------------------


class TestExitCodeClassification:
    @pytest.mark.parametrize(
        "code,reason",
        [
            (2, RemoHostExitReason.USAGE_ERROR),
            (3, RemoHostExitReason.INVALID_PROJECT),
            (4, RemoHostExitReason.UNSUPPORTED_SUBCOMMAND),
            (5, RemoHostExitReason.INTERNAL_ERROR),
        ],
    )
    def test_documented_exit_codes_map_to_typed_reason(self, mocker, code, reason):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(code, stderr=b"diagnostic message"),
        )

        with pytest.raises(RemoHostCommandError) as exc_info:
            get_capabilities(SSH_PREFIX)

        assert exc_info.value.returncode == code
        assert exc_info.value.reason == reason

    def test_ssh_255_is_transport_error_not_command_error(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(255, stderr=b"Permission denied (publickey)."),
        )

        with pytest.raises(SshTransportError) as exc_info:
            get_capabilities(SSH_PREFIX)

        assert exc_info.value.returncode == 255
        # SshTransportError must be distinguishable from RemoHostCommandError.
        assert not isinstance(exc_info.value, RemoHostCommandError)

    def test_subprocess_timeout_is_transport_error(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ssh", timeout=10),
        )

        with pytest.raises(SshTransportError):
            get_capabilities(SSH_PREFIX)

    def test_subprocess_oserror_is_transport_error(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            side_effect=OSError("ssh: command not found"),
        )

        with pytest.raises(SshTransportError):
            get_capabilities(SSH_PREFIX)

    def test_unknown_nonzero_exit_is_still_typed(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(17, stderr=b"???"),
        )

        with pytest.raises(RemoHostCommandError) as exc_info:
            get_capabilities(SSH_PREFIX)

        assert exc_info.value.reason == RemoHostExitReason.UNKNOWN


# ---------------------------------------------------------------------------
# Happy path: capabilities
# ---------------------------------------------------------------------------


class TestCapabilitiesHappyPath:
    def test_parses_into_typed_result(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(CAPABILITIES_JSON).encode()),
        )

        result = get_capabilities(SSH_PREFIX)

        assert result == RemoteCapability(
            protocol_version=1,
            host_tools_version="2.1.0",
            projects_root="/home/remo/projects",
            operations=["capabilities", "sessions.list", "sessions.attach"],
            zellij=True,
            docker=True,
        )

    def test_unknown_extra_top_level_fields_are_tolerated(self, mocker):
        payload = {**CAPABILITIES_JSON, "future_field": {"nested": True}, "another": 42}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        result = get_capabilities(SSH_PREFIX)
        assert result.protocol_version == 1


# ---------------------------------------------------------------------------
# Happy path: sessions list
# ---------------------------------------------------------------------------


class TestSessionsListHappyPath:
    def test_parses_into_typed_project_entries(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(SESSIONS_JSON).encode()),
        )

        entries = list_sessions(SSH_PREFIX)

        assert entries == [
            ProjectEntry(
                name="my-api",
                has_devcontainer=True,
                zellij_state=ZellijState.ACTIVE,
                devcontainer_running=DevcontainerRunning.RUNNING,
            ),
            ProjectEntry(
                name="notes",
                has_devcontainer=False,
                zellij_state=ZellijState.ABSENT,
                devcontainer_running=DevcontainerRunning.UNKNOWN,
            ),
        ]

    def test_parses_git_status_fields_when_present(self, mocker):
        payload = {
            "protocol_version": 1,
            "projects_root": "/home/remo/projects",
            "projects": [
                {
                    "name": "api",
                    "has_devcontainer": True,
                    "zellij_state": "active",
                    "devcontainer_running": "running",
                    "git_tracked": True,
                    "git_dirty": True,
                    "git_ahead": 2,
                    "git_behind": 1,
                }
            ],
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )
        entry = list_sessions(SSH_PREFIX)[0]
        assert (entry.git_tracked, entry.git_dirty, entry.git_ahead, entry.git_behind) == (
            True,
            True,
            2,
            1,
        )

    def test_git_fields_default_when_absent_backcompat(self, mocker):
        # An older host omits git_* keys entirely; the entry must still parse
        # with git defaults (not tracked, clean, no ahead/behind).
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(SESSIONS_JSON).encode()),
        )
        entry = list_sessions(SSH_PREFIX)[0]
        assert (entry.git_tracked, entry.git_dirty, entry.git_ahead, entry.git_behind) == (
            False,
            False,
            0,
            0,
        )

    def test_git_counts_coerced_from_strings_and_clamped(self, mocker):
        payload = {
            "protocol_version": 1,
            "projects_root": "/home/remo/projects",
            "projects": [
                {
                    "name": "api",
                    "has_devcontainer": False,
                    "zellij_state": "absent",
                    "devcontainer_running": "unknown",
                    "git_tracked": True,
                    "git_ahead": "3",  # host emitted a string
                    "git_behind": -1,  # nonsense negative clamps to 0
                }
            ],
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )
        entry = list_sessions(SSH_PREFIX)[0]
        assert (entry.git_ahead, entry.git_behind) == (3, 0)

    def test_unknown_extra_fields_on_entry_are_tolerated(self, mocker):
        payload = {
            "protocol_version": 1,
            "projects_root": "/home/remo/projects",
            "projects": [
                {
                    "name": "my-api",
                    "has_devcontainer": True,
                    "zellij_state": "active",
                    "devcontainer_running": "running",
                    "future_field": "surprise",
                }
            ],
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        entries = list_sessions(SSH_PREFIX)
        assert len(entries) == 1
        assert entries[0].name == "my-api"

    def test_unknown_zellij_state_enum_value_skips_entry_gracefully(self, mocker):
        """An unrecognized zellij_state must not blow up the whole parse —
        the offending entry is dropped, other entries still parse."""
        payload = {
            "protocol_version": 1,
            "projects_root": "/home/remo/projects",
            "projects": [
                {
                    "name": "broken-project",
                    "has_devcontainer": False,
                    "zellij_state": "some-future-state",
                    "devcontainer_running": "unknown",
                },
                {
                    "name": "notes",
                    "has_devcontainer": False,
                    "zellij_state": "absent",
                    "devcontainer_running": "unknown",
                },
            ],
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        entries = list_sessions(SSH_PREFIX)

        names = [e.name for e in entries]
        assert "broken-project" not in names
        assert "notes" in names

    def test_unknown_devcontainer_running_enum_value_skips_entry_gracefully(self, mocker):
        payload = {
            "protocol_version": 1,
            "projects_root": "/home/remo/projects",
            "projects": [
                {
                    "name": "broken-project",
                    "has_devcontainer": True,
                    "zellij_state": "active",
                    "devcontainer_running": "some-future-status",
                },
            ],
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        entries = list_sessions(SSH_PREFIX)
        assert entries == []

    def test_missing_projects_key_is_malformed(self, mocker):
        payload = {"protocol_version": 1, "projects_root": "/home/remo/projects"}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(MalformedResponseError):
            list_sessions(SSH_PREFIX)

    def test_empty_projects_list_is_valid(self, mocker):
        payload = {"protocol_version": 1, "projects_root": "/home/remo/projects", "projects": []}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        assert list_sessions(SSH_PREFIX) == []
