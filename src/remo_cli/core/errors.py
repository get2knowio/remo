"""Typed provider error taxonomy (contracts/errors.md).

Business-layer verbs (providers/*.py, core templates) raise these instead of
calling sys.exit or raising bare RuntimeError. There is exactly one
translation boundary from exception to process exit code: the CLI factory's
``provider_command`` wrapper (cli/providers/factory.py). Non-CLI consumers
(the web service) catch ``ProviderError`` directly.
"""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for all provider-layer failures.

    ``exit_code`` is the process exit code the CLI translation boundary
    should use when this error escapes a command callback.
    """

    exit_code = 1

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class MissingDependencyError(ProviderError):
    """An optional provider SDK (boto3, hcloud, ...) is not installed."""


class PreconditionError(ProviderError):
    """Invalid input, entry not found, wrong state, or an unknown provider type."""


class OperationFailedError(ProviderError):
    """A subprocess/playbook/API call failed; message carries the underlying detail."""


class UserAbortedError(ProviderError):
    """The user declined a confirmation prompt."""

    exit_code = 3
