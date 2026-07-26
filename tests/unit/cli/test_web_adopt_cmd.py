"""Unit tests for the `remo web adopt`/`remo web push` CLI commands.

Originally 011-web-adopt (T027); updated for 017-web-adopt-simplify (T007):
`remo web adopt` is now a DEPRECATED ALIAS that prints a deprecation notice and
delegates to the unified `remo web push` code path (`core/web_adopt.run_push`).
There is no separate `run_adopt` split anymore — the CLI patches/asserts against
`run_push`.

Covers the contract in specs/017-web-adopt-simplify/contracts/cli-web-push.md:

- URL/token resolution order (argument -> env -> prompt; --token -> env ->
  hidden prompt).
- Exit codes: a completed run (even with per-instance skips) exits 0; every
  AdoptError hard failure (mount-configured, dormant, empty registry, tunnel)
  exits 1 with the contract's clear message.
- Flag pass-through: --via/--allow-empty/--yes/--force reach run_push.
- Deprecation: `remo web adopt` prints a deprecation notice and still delegates.
- `remo web push --help` documents the unified adopt-or-resync behavior and
  lists --force.
- Trust invariant (SC-009 / FR-004): the deprecated alias forwards only the URL,
  code, and known flags — no personal-key/credential material — to run_push.
- NFR: `remo web adopt` must work without the `web` extra — importing
  `remo_cli.cli.web` and running `remo web adopt --help` must never import
  `remo_cli.web.*` / fastapi / uvicorn (subprocess with blocked imports,
  same pattern as tests/unit/web/test_lazy_import.py).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from remo_cli.cli.web import adopt, push
from remo_cli.core import web_adopt
from remo_cli.core.web_adopt import (
    OUTCOME_SKIPPED_UNREACHABLE,
    AdoptResult,
    EmptyRegistryError,
    InstanceOutcome,
    MountConfiguredError,
    SetupNotFoundError,
    TunnelError,
)
from remo_cli.models.host import KnownHost

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = _REPO_ROOT / "src"

# Ensure resolution tests never pick up ambient env from the developer's
# shell; individual tests override these as needed.
_CLEAN_ENV: dict[str, str | None] = {"REMO_API_URL": None, "REMO_API_TOKEN": None}


def _completed_result_with_skips() -> AdoptResult:
    """A finished flow whose summary contains skips — still exit code 0.

    ``revocations`` is left to its default (empty list); the delegated
    ``run_push`` contract is otherwise unchanged for the CLI's purposes.
    """
    host = KnownHost(type="hetzner", name="webserver", host="5.6.7.8", user="remo")
    return AdoptResult(
        outcomes=[
            InstanceOutcome(
                host,
                OUTCOME_SKIPPED_UNREACHABLE,
                detail="host key scan timed out after 20s",
            )
        ],
        verify={"all_passed": False, "results": []},
        applied={"registry_instances": 1, "host_key_instances": 0},
        deployment_id="dep-1234",
    )


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_run_push(mocker):
    """Patch run_push at its source module (both `adopt` and `push` import it
    lazily, inside the function body, so patching the source is seen by the
    CLI). `remo web adopt` is now a deprecated alias delegating to run_push."""
    return mocker.patch(
        "remo_cli.core.web_adopt.run_push",
        return_value=_completed_result_with_skips(),
    )


class TestUrlResolutionOrder:
    """Service URL: argument -> REMO_API_URL -> interactive prompt."""

    def test_url_argument_beats_env(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            ["http://from-arg:8080"],
            env={"REMO_API_URL": "http://from-env:8080", "REMO_API_TOKEN": "tok"},
        )

        assert result.exit_code == 0
        assert mock_run_push.call_args[0][0] == "http://from-arg:8080"

    def test_env_beats_prompt(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            [],
            env={"REMO_API_URL": "http://from-env:8080", "REMO_API_TOKEN": "tok"},
        )

        assert result.exit_code == 0
        assert mock_run_push.call_args[0][0] == "http://from-env:8080"
        assert "Service URL" not in result.output

    def test_prompt_when_no_arg_and_no_env(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            [],
            env=_CLEAN_ENV,
            input="http://from-prompt:8080\nprompted-token\n",
        )

        assert result.exit_code == 0
        assert "Service URL" in result.output
        assert mock_run_push.call_args[0][0] == "http://from-prompt:8080"


class TestTokenResolutionOrder:
    """Pairing code: --token -> REMO_API_TOKEN -> hidden prompt."""

    def test_token_option_beats_env(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            ["http://svc:8080", "--token", "from-option"],
            env={"REMO_API_URL": None, "REMO_API_TOKEN": "from-env"},
        )

        assert result.exit_code == 0
        assert mock_run_push.call_args[0][1] == "from-option"

    def test_env_token_beats_prompt(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            ["http://svc:8080"],
            env={"REMO_API_URL": None, "REMO_API_TOKEN": "from-env"},
        )

        assert result.exit_code == 0
        assert mock_run_push.call_args[0][1] == "from-env"
        assert "Pairing code" not in result.output

    def test_hidden_prompt_when_no_option_and_no_env(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            ["http://svc:8080"],
            env=_CLEAN_ENV,
            input="sekrit-code\n",
        )

        assert result.exit_code == 0
        assert "Pairing code" in result.output
        assert mock_run_push.call_args[0][1] == "sekrit-code"
        # hide_input=True: the typed code must never be echoed back.
        assert "sekrit-code" not in result.output


class TestExitCodes:
    """Contract exit codes: completed -> 0; AdoptError hard failure -> 1."""

    def test_completed_flow_with_skips_exits_zero(self, runner, mock_run_push):
        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 0
        mock_run_push.assert_called_once()

    def test_mount_configured_exits_one_with_read_only_mounts_message(
        self, runner, mock_run_push
    ):
        mock_run_push.side_effect = MountConfiguredError(
            web_adopt._MOUNT_CONFIGURED_MSG, status=409
        )

        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 1
        # Contract error-message requirement: state that the deployment is
        # configured via read-only mounts and adoption does not apply.
        assert "read-only mounts" in result.output
        assert "adoption does not apply" in result.output

    def test_dormant_surface_exits_one_with_reopen_message(self, runner, mock_run_push):
        mock_run_push.side_effect = SetupNotFoundError(
            "the pairing code is no longer valid — the setup surface at "
            "http://svc:8080 is dormant (HTTP 404). Reopen the adopt page to mint "
            "a fresh code, then retry.",
            status=404,
        )

        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "stale-code"], env=_CLEAN_ENV
        )

        assert result.exit_code == 1
        assert "dormant" in result.output
        assert "fresh code" in result.output

    def test_empty_registry_exits_one_and_names_allow_empty(
        self, runner, mock_run_push
    ):
        mock_run_push.side_effect = EmptyRegistryError(
            web_adopt._empty_registry_message()
        )

        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 1
        # Contract: name --allow-empty and the wrong-workstation risk.
        assert "--allow-empty" in result.output
        assert "wrong" in result.output and "workstation" in result.output

    def test_tunnel_failure_exits_one_with_tunnel_message(
        self, runner, mock_run_push
    ):
        mock_run_push.side_effect = TunnelError(
            "--via tunnel to jumphost did not become ready within 15s"
        )

        result = runner.invoke(
            adopt,
            ["http://svc:8080", "--token", "tok", "--via", "jumphost"],
            env=_CLEAN_ENV,
        )

        assert result.exit_code == 1
        assert "--via tunnel to jumphost" in result.output

    def test_message_constants_satisfy_contract(self):
        """The module-level messages themselves carry the contract wording."""
        assert "read-only mounts" in web_adopt._MOUNT_CONFIGURED_MSG
        empty_msg = web_adopt._empty_registry_message()
        assert "--allow-empty" in empty_msg
        assert "wrong" in empty_msg


class TestDeprecatedAdoptAlias:
    """`remo web adopt` is a deprecated alias that delegates to run_push."""

    def test_adopt_prints_deprecation_notice_and_still_delegates(
        self, runner, mock_run_push
    ):
        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 0
        # The deprecation notice must be surfaced to the operator (FR-008).
        assert "is deprecated" in result.output
        assert "remo web push" in result.output
        # ...but the command still works: exactly one delegation to run_push.
        mock_run_push.assert_called_once()

    def test_adopt_forwards_only_clean_delegation_surface(self, runner, mock_run_push):
        """Trust invariant (SC-009 / FR-004): the deprecated alias forwards ONLY
        the URL, code, and the known flags to run_push — no key paths, no
        personal identity, no extra credential material. The deep trust
        invariants live in the core unit tests; here we assert the CLI's
        delegation surface stays clean."""
        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 0
        args, kwargs = mock_run_push.call_args
        # Positionals: exactly the URL and the pairing code — nothing else.
        assert args == ("http://svc:8080", "tok")
        # Keywords: exactly the known flags, no key/identity/credential material.
        assert kwargs == {
            "via": None,
            "allow_empty": False,
            "assume_yes": False,
            "force": False,
        }
        assert set(kwargs) == {"via", "allow_empty", "assume_yes", "force"}
        # Nothing key-like leaks into the delegation surface.
        forwarded = list(args) + list(kwargs) + [str(v) for v in kwargs.values()]
        for item in forwarded:
            lowered = item.lower()
            assert "identity" not in lowered
            assert "id_" not in lowered
            assert ".pub" not in lowered
            assert "private" not in lowered


class TestFlagPassThrough:
    """--via/--allow-empty/--yes/--force must reach run_push unchanged."""

    def test_all_flags_forwarded_with_force(self, runner, mock_run_push):
        result = runner.invoke(
            adopt,
            [
                "http://svc:8080",
                "--token",
                "tok",
                "--via",
                "jumphost",
                "--allow-empty",
                "--yes",
                "--force",
            ],
            env=_CLEAN_ENV,
        )

        assert result.exit_code == 0
        args, kwargs = mock_run_push.call_args
        assert args == ("http://svc:8080", "tok")
        assert kwargs == {
            "via": "jumphost",
            "allow_empty": True,
            "assume_yes": True,
            "force": True,
        }

    def test_defaults_forwarded_when_flags_omitted(self, runner, mock_run_push):
        result = runner.invoke(
            adopt, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 0
        _, kwargs = mock_run_push.call_args
        assert kwargs == {
            "via": None,
            "allow_empty": False,
            "assume_yes": False,
            "force": False,
        }

    def test_push_forwards_force_true(self, runner, mock_run_push):
        result = runner.invoke(
            push,
            ["http://svc:8080", "--token", "tok", "--force"],
            env=_CLEAN_ENV,
        )

        assert result.exit_code == 0
        args, kwargs = mock_run_push.call_args
        assert args == ("http://svc:8080", "tok")
        assert kwargs["force"] is True

    def test_push_force_defaults_false(self, runner, mock_run_push):
        result = runner.invoke(
            push, ["http://svc:8080", "--token", "tok"], env=_CLEAN_ENV
        )

        assert result.exit_code == 0
        _, kwargs = mock_run_push.call_args
        assert kwargs["force"] is False


class TestPushHelp:
    """`remo web push --help` documents the unified adopt-or-resync behavior."""

    def test_help_documents_unified_adopt_or_resync(self, runner):
        result = runner.invoke(push, ["--help"])

        assert result.exit_code == 0
        # Help text (src/remo_cli/cli/web.py push docstring) advertises that the
        # first push adopts and later pushes re-sync — one auto-detected path.
        lowered = result.output.lower()
        assert "adopt" in lowered
        assert "re-sync" in lowered or "resync" in lowered

    def test_help_lists_force_flag(self, runner):
        result = runner.invoke(push, ["--help"])

        assert result.exit_code == 0
        assert "--force" in result.output


# ---------------------------------------------------------------------------
# No-web-extra guarantee: `remo web adopt` is stdlib-HTTP only and must work
# with the `web` extra absent. Run in a fresh subprocess with fastapi/uvicorn/
# starlette AND remo_cli.web blocked at the import level, so the test proves
# the boundary regardless of what happens to be installed in this environment
# (same pattern as tests/unit/web/test_lazy_import.py).
# ---------------------------------------------------------------------------

_BLOCK_AND_RUN_ADOPT_HELP = """
import builtins
import sys

_BLOCKED = ("fastapi", "uvicorn", "starlette", "remo_cli.web")
_real_import = builtins.__import__


def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
    for blocked in _BLOCKED:
        if name == blocked or name.startswith(blocked + "."):
            raise ImportError(f"blocked for test: {name}")
    return _real_import(name, globals, locals, fromlist, level)


builtins.__import__ = _fake_import

# Sanity-check the block is in effect so the assertions below can be trusted.
try:
    import fastapi  # noqa: F401
    raise SystemExit("SANITY FAIL: fastapi import was not blocked")
except ImportError:
    pass

# 1. Importing the CLI module (as `remo_cli.cli.main._register_commands()`
#    does) must not touch the web service package.
import remo_cli.cli.web  # noqa: F401

# 2. `remo web adopt --help` must render without the web extra. Adopt's own
#    imports are lazy (core-only), so even the command body never needs it.
from click.testing import CliRunner
from remo_cli.cli.main import cli

result = CliRunner().invoke(cli, ["web", "adopt", "--help"])
assert result.exit_code == 0, f"--help failed: {result.output}"
assert "adopt" in result.output.lower()

web_service_modules = sorted(
    m for m in sys.modules if m == "remo_cli.web" or m.startswith("remo_cli.web.")
)
assert not web_service_modules, f"web service modules imported: {web_service_modules}"
for blocked in ("fastapi", "uvicorn", "starlette"):
    assert blocked not in sys.modules, f"{blocked} was imported"

print("ADOPT_NO_WEB_EXTRA_OK")
"""


class TestAdoptWithoutWebExtra:
    def test_import_and_help_never_import_web_package_or_fastapi(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(_SRC_DIR)
        result = subprocess.run(
            [sys.executable, "-c", _BLOCK_AND_RUN_ADOPT_HELP],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        assert result.returncode == 0, (
            "`remo web adopt --help` required the web extra or imported "
            f"remo_cli.web.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "ADOPT_NO_WEB_EXTRA_OK" in result.stdout
