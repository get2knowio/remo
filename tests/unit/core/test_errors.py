from __future__ import annotations

import pytest

from remo_cli.core.errors import (
    MissingDependencyError,
    OperationFailedError,
    PreconditionError,
    ProviderError,
    UserAbortedError,
)


@pytest.mark.parametrize(
    ("error_cls", "expected_exit_code"),
    [
        (ProviderError, 1),
        (MissingDependencyError, 1),
        (PreconditionError, 1),
        (OperationFailedError, 1),
        (UserAbortedError, 3),
    ],
)
def test_exit_codes(error_cls: type[ProviderError], expected_exit_code: int) -> None:
    assert error_cls("boom").exit_code == expected_exit_code


@pytest.mark.parametrize(
    "error_cls",
    [MissingDependencyError, PreconditionError, OperationFailedError, UserAbortedError],
)
def test_subclasses_of_provider_error(error_cls: type[ProviderError]) -> None:
    assert issubclass(error_cls, ProviderError)


def test_message_preserved_and_str() -> None:
    err = ProviderError("something failed")
    assert err.message == "something failed"
    assert str(err) == "something failed"


def test_carries_underlying_detail() -> None:
    err = OperationFailedError("playbook failed (rc=4)")
    assert "rc=4" in str(err)
