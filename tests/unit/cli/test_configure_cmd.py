"""CLI-layer tests for `remo configure`. Provider logic is mocked.

Unlike `add`/`remove` — which predate the taxonomy and still return ints — this
command is wrapped in `provider_command`, the single exception-to-exit-code
boundary. The mapping it enforces (0 success / 1 failure / 3 user-aborted) is
what the tests below pin, since a command that raised past the wrapper would
surface a traceback instead of the actionable message the errors carry.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.added import configure
from remo_cli.core.errors import (
    OperationFailedError,
    PreconditionError,
    UserAbortedError,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _no_drift_nudge(mocker):
    """The post-command `remo web push` nudge reads real config; stub it out."""
    return mocker.patch("remo_cli.core.web_drift.emit_out_of_date_notice")


def test_success_forwards_every_option_and_exits_zero(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.configure")

    result = runner.invoke(
        configure, ["mbp", "--skip", "docker", "--skip", "zellij", "--yes", "-v"]
    )

    assert result.exit_code == 0
    kwargs = prov.call_args.kwargs
    assert kwargs["name"] == "mbp"
    assert kwargs["tools_skip"] == ("docker", "zellij")
    assert kwargs["tools_only"] == ()
    assert kwargs["assume_yes"] is True
    assert kwargs["verbose"] is True


def test_defaults_are_conservative(runner, mocker) -> None:
    # Nothing skipped, nothing assumed, no raw ansible output.
    prov = mocker.patch("remo_cli.providers.added.configure")

    runner.invoke(configure, ["mbp"])

    kwargs = prov.call_args.kwargs
    assert kwargs["assume_yes"] is False
    assert kwargs["verbose"] is False


def test_only_flag_is_repeatable(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.configure")

    runner.invoke(configure, ["mbp", "--only", "zellij", "--only", "fzf"])

    assert prov.call_args.kwargs["tools_only"] == ("zellij", "fzf")


def test_invalid_name_rejected_before_provider(runner, mocker) -> None:
    prov = mocker.patch("remo_cli.providers.added.configure")

    result = runner.invoke(configure, ["!bad"])

    assert result.exit_code == 2
    prov.assert_not_called()


@pytest.mark.parametrize(
    ("error", "exit_code"),
    [
        (PreconditionError("not registered"), 1),
        (OperationFailedError("playbook rc=2"), 1),
        (UserAbortedError("declined"), 3),
    ],
)
def test_typed_errors_become_exit_codes_not_tracebacks(
    runner, mocker, error, exit_code
) -> None:
    mocker.patch("remo_cli.providers.added.configure", side_effect=error)

    result = runner.invoke(configure, ["mbp"])

    assert result.exit_code == exit_code
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert str(error) in result.output


def test_nudges_toward_web_push_on_success(runner, mocker, _no_drift_nudge) -> None:
    # A freshly configured host usually still needs its key pushed before it
    # shows up in `remo web`; the notice is a no-op when already in sync.
    mocker.patch("remo_cli.providers.added.configure")

    runner.invoke(configure, ["mbp"])

    _no_drift_nudge.assert_called_once()


def test_no_nudge_when_the_command_failed(runner, mocker, _no_drift_nudge) -> None:
    mocker.patch(
        "remo_cli.providers.added.configure", side_effect=PreconditionError("nope")
    )

    runner.invoke(configure, ["mbp"])

    _no_drift_nudge.assert_not_called()
