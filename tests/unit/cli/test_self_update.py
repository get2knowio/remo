"""CLI wiring for `remo self-update` (spec 031)."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.cli.main import cli
from remo_cli.core.errors import OperationFailedError, PreconditionError, UserAbortedError

PATCH = "remo_cli.providers.self_update.run_self_update"


def test_registered_in_root_help():
    out = CliRunner().invoke(cli, ["--help"]).output
    assert "self-update" in out


def test_help():
    r = CliRunner().invoke(cli, ["self-update", "--help"])
    assert r.exit_code == 0
    assert "--check" in r.output and "--yes" in r.output


def test_check_passes_flag_and_exits_0(mocker):
    m = mocker.patch(PATCH, side_effect=lambda **kw: print("remo 1 → 2 (stable)"))
    r = CliRunner().invoke(cli, ["self-update", "--check"])
    assert r.exit_code == 0
    assert "remo 1" in r.output
    m.assert_called_once_with(check=True, assume_yes=False)


def test_yes_flag(mocker):
    m = mocker.patch(PATCH)
    r = CliRunner().invoke(cli, ["self-update", "--yes"])
    assert r.exit_code == 0
    m.assert_called_once_with(check=False, assume_yes=True)


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (OperationFailedError("boom"), 1),
        (PreconditionError("bad record"), 1),
        (UserAbortedError("no"), 3),
    ],
)
def test_exit_codes(mocker, exc, code):
    mocker.patch(PATCH, side_effect=exc)
    assert CliRunner().invoke(cli, ["self-update"]).exit_code == code
