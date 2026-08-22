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


# ---------------------------------------------------------------------------
# Chunk 3 (host detail): new verbs, typed wrappers, models
# ---------------------------------------------------------------------------

from remo_cli.core.errors import PreconditionError  # noqa: E402
from remo_cli.core.remo_host_client import (  # noqa: E402
    DEFAULT_TIMEOUT,
    PROJECT_DELETE_TIMEOUT,
    DiskUsage,
    HostStats,
    JobRef,
    JobState,
    JobStatus,
    TempReading,
    delete_project,
    get_host_stats,
    get_job_status,
    start_project_clone,
    start_project_rebuild,
)

STATS_JSON = {
    "protocol_version": 1,
    "uptime_s": 123456.7,
    "load_1": 0.42,
    "load_5": 0.31,
    "load_15": 0.25,
    "cpu_count": 8,
    "cpu_used_pct": 12.5,
    "mem_total": 16777216000,
    "mem_used": 4194304000,
    "mem_available": 12582912000,
    "swap_total": 2147483648,
    "swap_used": 0,
    "disks": [
        {"mount": "/", "size_bytes": 100, "used_bytes": 60, "avail_bytes": 40},
        {"mount": "/home/remo/projects", "size_bytes": 500, "used_bytes": 100, "avail_bytes": 400},
    ],
    "temps": [
        {"name": "coretemp", "label": "Package id 0", "celsius": 42.0},
    ],
}

JOB_REF_JSON = {
    "protocol_version": 1,
    "job_id": "clone-20260820-a1b2c3",
    "kind": "clone",
    "project": "my-api",
}

JOB_STATUS_JSON = {
    "protocol_version": 1,
    "state": "succeeded",
    "exit_code": 0,
    "started_at": "2026-08-20T12:00:00Z",
    "finished_at": "2026-08-20T12:03:10Z",
    "log_tail": "Cloning into 'my-api'...\ndone.\n",
}


# ---------------------------------------------------------------------------
# Existing argv shapes are byte-identical after the signature extension
# ---------------------------------------------------------------------------


class TestExistingArgvShapesUnchanged:
    """Regression: adding the five new verbs must not shift a single byte of
    the pre-existing verbs' argv (especially `sessions attach`, which the
    web terminal embeds in a shell command)."""

    def test_capabilities_unchanged(self):
        assert build_remo_host_argv("capabilities") == ["remo-host", "capabilities", "--json"]

    def test_sessions_list_unchanged(self):
        assert build_remo_host_argv("sessions list") == [
            "remo-host",
            "sessions",
            "list",
            "--json",
        ]

    def test_sessions_list_json_false_unchanged(self):
        assert build_remo_host_argv("sessions list", json=False) == [
            "remo-host",
            "sessions",
            "list",
        ]

    def test_sessions_attach_unchanged(self):
        assert build_remo_host_argv("sessions attach", project="my-api") == [
            "remo-host",
            "sessions",
            "attach",
            "--project",
            "my-api",
        ]

    def test_sessions_attach_shell_cmd_unchanged(self):
        assert build_remo_host_shell_cmd("sessions attach", project="my-api") == (
            'PATH="$HOME/.local/bin:$PATH" remo-host sessions attach --project my-api'
        )

    def test_project_still_ignored_on_legacy_read_verbs(self):
        # Pre-change behavior: project was documented as ignored for the
        # read-only verbs; that stays true.
        assert build_remo_host_argv("capabilities", project="x") == [
            "remo-host",
            "capabilities",
            "--json",
        ]


# ---------------------------------------------------------------------------
# New verb argv matrix
# ---------------------------------------------------------------------------


class TestNewVerbArgv:
    def test_host_stats(self):
        assert build_remo_host_argv("host stats") == ["remo-host", "host", "stats", "--json"]

    def test_jobs_status(self):
        assert build_remo_host_argv("jobs status", job="clone-1") == [
            "remo-host",
            "jobs",
            "status",
            "--job",
            "clone-1",
            "--json",
        ]

    def test_projects_clone_repo_only(self):
        assert build_remo_host_argv("projects clone", repo="owner/repo") == [
            "remo-host",
            "projects",
            "clone",
            "--repo",
            "owner/repo",
            "--json",
        ]

    def test_projects_clone_with_name(self):
        assert build_remo_host_argv("projects clone", repo="owner/repo", name="myname") == [
            "remo-host",
            "projects",
            "clone",
            "--repo",
            "owner/repo",
            "--name",
            "myname",
            "--json",
        ]

    def test_projects_delete(self):
        assert build_remo_host_argv("projects delete", project="my-api") == [
            "remo-host",
            "projects",
            "delete",
            "--project",
            "my-api",
            "--json",
        ]

    def test_projects_rebuild(self):
        assert build_remo_host_argv("projects rebuild", project="my-api") == [
            "remo-host",
            "projects",
            "rebuild",
            "--project",
            "my-api",
            "--json",
        ]

    def test_projects_rebuild_no_cache_precedes_json(self):
        assert build_remo_host_argv("projects rebuild", project="my-api", no_cache=True) == [
            "remo-host",
            "projects",
            "rebuild",
            "--project",
            "my-api",
            "--no-cache",
            "--json",
        ]

    def test_json_false_omits_flag_on_new_verbs(self):
        assert build_remo_host_argv("host stats", json=False) == ["remo-host", "host", "stats"]

    @pytest.mark.parametrize(
        "verb,kwargs",
        [
            ("jobs status", {}),                       # missing job
            ("projects clone", {}),                    # missing repo
            ("projects delete", {}),                   # missing project
            ("projects rebuild", {}),                  # missing project
        ],
    )
    def test_missing_required_flag_raises(self, verb, kwargs):
        with pytest.raises(ValueError):
            build_remo_host_argv(verb, **kwargs)

    @pytest.mark.parametrize(
        "verb,kwargs",
        [
            ("host stats", {"project": "x"}),
            ("host stats", {"repo": "o/r"}),
            ("jobs status", {"job": "j", "project": "x"}),
            ("jobs status", {"job": "j", "no_cache": True}),
            ("projects clone", {"repo": "o/r", "project": "x"}),
            ("projects clone", {"repo": "o/r", "no_cache": True}),
            ("projects delete", {"project": "x", "repo": "o/r"}),
            ("projects delete", {"project": "x", "name": "n"}),
            ("projects rebuild", {"project": "x", "repo": "o/r"}),
            ("capabilities", {"repo": "o/r"}),
            ("sessions list", {"job": "j"}),
            ("sessions attach", {"project": "x", "no_cache": True}),
        ],
    )
    def test_flag_on_wrong_verb_raises(self, verb, kwargs):
        with pytest.raises(ValueError):
            build_remo_host_argv(verb, **kwargs)


# ---------------------------------------------------------------------------
# Wrapper argv composition + timeouts
# ---------------------------------------------------------------------------


class TestWrapperArgvAndTimeouts:
    def test_get_host_stats_argv_and_default_timeout(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(STATS_JSON).encode())

        get_host_stats(SSH_PREFIX)

        assert mock_run.call_args[0][0] == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "host",
            "stats",
            "--json",
        ]
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT

    def test_get_job_status_argv(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(JOB_STATUS_JSON).encode())

        get_job_status(SSH_PREFIX, "clone-20260820-a1b2c3")

        assert mock_run.call_args[0][0] == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "jobs",
            "status",
            "--job",
            "clone-20260820-a1b2c3",
            "--json",
        ]

    def test_start_project_clone_argv_and_detached_default_timeout(self, mocker):
        # Clone detaches host-side, so the 10s default must hold (no
        # special long timeout for a multi-minute clone).
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(JOB_REF_JSON).encode())

        start_project_clone(SSH_PREFIX, "owner/repo", name="my-api")

        assert mock_run.call_args[0][0] == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "projects",
            "clone",
            "--repo",
            "owner/repo",
            "--name",
            "my-api",
            "--json",
        ]
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT

    def test_delete_project_argv_and_30s_timeout(self, mocker):
        # Delete is the one synchronous mutating verb; it gets timeout=30.
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps({"protocol_version": 1}).encode())

        assert delete_project(SSH_PREFIX, "my-api") is None

        assert mock_run.call_args[0][0] == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "projects",
            "delete",
            "--project",
            "my-api",
            "--json",
        ]
        assert mock_run.call_args.kwargs["timeout"] == PROJECT_DELETE_TIMEOUT == 30.0

    def test_start_project_rebuild_argv_and_detached_default_timeout(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(JOB_REF_JSON).encode())

        start_project_rebuild(SSH_PREFIX, "my-api", no_cache=True)

        assert mock_run.call_args[0][0] == [
            *SSH_PREFIX,
            'PATH="$HOME/.local/bin:$PATH"',
            "remo-host",
            "projects",
            "rebuild",
            "--project",
            "my-api",
            "--no-cache",
            "--json",
        ]
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# get_host_stats parsing: happy path + tolerant degradation
# ---------------------------------------------------------------------------


class TestHostStatsParsing:
    def test_happy_path(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(STATS_JSON).encode()),
        )

        stats = get_host_stats(SSH_PREFIX)

        assert stats == HostStats(
            uptime_s=123456.7,
            load_1=0.42,
            load_5=0.31,
            load_15=0.25,
            cpu_count=8,
            cpu_used_pct=12.5,
            mem_total=16777216000,
            mem_used=4194304000,
            mem_available=12582912000,
            swap_total=2147483648,
            swap_used=0,
            disks=[
                DiskUsage(mount="/", size_bytes=100, used_bytes=60, avail_bytes=40),
                DiskUsage(
                    mount="/home/remo/projects",
                    size_bytes=500,
                    used_bytes=100,
                    avail_bytes=400,
                ),
            ],
            temps=[TempReading(name="coretemp", label="Package id 0", celsius=42.0)],
        )

    def test_minimal_payload_degrades_to_defaults_never_raises(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps({"protocol_version": 1}).encode()),
        )

        stats = get_host_stats(SSH_PREFIX)

        assert stats == HostStats()
        assert stats.disks == [] and stats.temps == []

    def test_garbage_fields_degrade_to_defaults(self):
        stats = HostStats.from_dict(
            {
                "uptime_s": "not-a-number",
                "load_1": None,
                "cpu_count": True,          # bool is not a count
                "cpu_used_pct": "12.5",     # numeric string coerces
                "mem_total": "16777216000",  # numeric string coerces
                "mem_used": -5,              # negative clamps to 0
                "swap_total": {"nested": 1},
                "disks": "not-a-list",
                "temps": {"not": "a list"},
            }
        )
        assert stats.uptime_s == 0.0
        assert stats.load_1 == 0.0
        assert stats.cpu_count == 0
        assert stats.cpu_used_pct == 12.5
        assert stats.mem_total == 16777216000
        assert stats.mem_used == 0
        assert stats.swap_total == 0
        assert stats.disks == []
        assert stats.temps == []

    def test_broken_list_entries_are_skipped_good_ones_kept(self):
        stats = HostStats.from_dict(
            {
                "disks": [
                    "not-a-dict",
                    {"size_bytes": 1},  # no mount -> skipped
                    {"mount": "/", "size_bytes": "100", "used_bytes": None},
                ],
                "temps": [
                    42,
                    {"name": "hwmon0", "label": "bad", "celsius": "garbage"},  # skipped
                    {"name": "hwmon1", "label": "ok", "celsius": "55.5"},
                ],
            }
        )
        assert stats.disks == [DiskUsage(mount="/", size_bytes=100, used_bytes=0, avail_bytes=0)]
        assert stats.temps == [TempReading(name="hwmon1", label="ok", celsius=55.5)]

    def test_unknown_extra_fields_are_tolerated(self, mocker):
        payload = {**STATS_JSON, "future_field": {"nested": True}}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )
        assert get_host_stats(SSH_PREFIX).cpu_count == 8


# ---------------------------------------------------------------------------
# Job wrappers: JobRef / JobStatus parsing
# ---------------------------------------------------------------------------


class TestJobParsing:
    def test_clone_returns_job_ref(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(JOB_REF_JSON).encode()),
        )

        ref = start_project_clone(SSH_PREFIX, "owner/repo")

        assert ref == JobRef(job_id="clone-20260820-a1b2c3", kind="clone", project="my-api")

    def test_job_ref_missing_job_id_is_malformed(self, mocker):
        payload = {"protocol_version": 1, "kind": "clone"}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(MalformedResponseError):
            start_project_clone(SSH_PREFIX, "owner/repo")

    def test_job_ref_kind_and_project_degrade_to_empty(self):
        ref = JobRef.from_dict({"job_id": "j1"})
        assert (ref.kind, ref.project) == ("", "")

    def test_job_status_happy_path(self, mocker):
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(JOB_STATUS_JSON).encode()),
        )

        status = get_job_status(SSH_PREFIX, "clone-20260820-a1b2c3")

        assert status == JobStatus(
            state=JobState.SUCCEEDED,
            exit_code=0,
            started_at="2026-08-20T12:00:00Z",
            finished_at="2026-08-20T12:03:10Z",
            log_tail="Cloning into 'my-api'...\ndone.\n",
        )

    def test_job_status_running_with_nulls(self, mocker):
        payload = {
            "protocol_version": 1,
            "state": "running",
            "exit_code": None,
            "started_at": "2026-08-20T12:00:00Z",
            "finished_at": None,
            "log_tail": None,
        }
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        status = get_job_status(SSH_PREFIX, "clone-1")

        assert status.state is JobState.RUNNING
        assert status.exit_code is None
        assert status.finished_at == ""
        assert status.log_tail == ""

    def test_job_status_unknown_state_is_malformed_not_running(self, mocker):
        # An unknown state silently coerced to "running" would poll forever.
        payload = {"protocol_version": 1, "state": "some-future-state"}
        mocker.patch(
            "remo_cli.core.remo_host_client.subprocess.run",
            return_value=_completed(0, stdout=json.dumps(payload).encode()),
        )

        with pytest.raises(MalformedResponseError):
            get_job_status(SSH_PREFIX, "clone-1")

    def test_job_status_exit_code_coerced_from_string_garbage_is_none(self):
        assert JobStatus.from_dict({"state": "failed", "exit_code": "5"}).exit_code == 5
        assert JobStatus.from_dict({"state": "failed", "exit_code": "boom"}).exit_code is None
        assert JobStatus.from_dict({"state": "failed", "exit_code": True}).exit_code is None


# ---------------------------------------------------------------------------
# Client-side pre-validation (defense in depth, FR-014)
# ---------------------------------------------------------------------------


class TestPreValidation:
    """Invalid input must raise PreconditionError BEFORE any subprocess runs —
    an unvalidated string never reaches the remote argv."""

    @pytest.mark.parametrize(
        "repo",
        [
            "owner/repo",
            "owner-1/repo_2.x",
            "https://github.com/owner/repo",
            "https://github.com/owner/repo.git",
            "https://github.com/owner/repo/",
        ],
    )
    def test_valid_repos_accepted(self, mocker, repo):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps(JOB_REF_JSON).encode())
        start_project_clone(SSH_PREFIX, repo)
        assert mock_run.called

    @pytest.mark.parametrize(
        "repo",
        [
            "",
            "owner",                          # no slash
            "owner/repo/extra",               # too many segments
            "-owner/repo",                    # leading dash
            "../..",                          # traversal disguised as owner/repo
            "owner/..",
            "git@github.com:owner/repo.git",  # ssh form not allowed
            "http://github.com/owner/repo",   # not https
            "https://gitlab.com/owner/repo",  # wrong host
            "https://github.com/owner",       # no repo segment
            "owner/repo; rm -rf /",           # shell metacharacters
            "owner/repo$(whoami)",
            "owner repo",                     # whitespace
        ],
    )
    def test_invalid_repos_rejected_before_subprocess(self, mocker, repo):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        with pytest.raises(PreconditionError):
            start_project_clone(SSH_PREFIX, repo)
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "name",
        ["", "-rf", "--name", ".", "..", "a b", "a/b", "$(whoami)", "café", "a\nb"],
    )
    def test_invalid_clone_target_name_rejected(self, mocker, name):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        with pytest.raises(PreconditionError):
            start_project_clone(SSH_PREFIX, "owner/repo", name=name)
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "project",
        ["", "-rf", "--project", ".", "..", "my project", "a/b", "; rm -rf /", "$(whoami)"],
    )
    def test_invalid_project_rejected_for_delete_and_rebuild(self, mocker, project):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        with pytest.raises(PreconditionError):
            delete_project(SSH_PREFIX, project)
        with pytest.raises(PreconditionError):
            start_project_rebuild(SSH_PREFIX, project)
        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        "job_id",
        ["", "-j", "job id", "a/b", "..", "$(id)"],
    )
    def test_invalid_job_id_rejected(self, mocker, job_id):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        with pytest.raises(PreconditionError):
            get_job_status(SSH_PREFIX, job_id)
        mock_run.assert_not_called()

    def test_valid_names_accepted(self, mocker):
        mock_run = mocker.patch("remo_cli.core.remo_host_client.subprocess.run")
        mock_run.return_value = _completed(0, stdout=json.dumps({"protocol_version": 1}).encode())
        delete_project(SSH_PREFIX, "my-api_2.0")
        assert mock_run.called
