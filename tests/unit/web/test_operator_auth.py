"""Operator-auth provider seam tests (012-web-adopt-pairing, T023).

Covers ForwardAuthProvider header trust, NetworkRestrictedProvider anonymity,
and build_operator_auth_provider's fail-fast + disabled-minting behavior
(FR-009/FR-011/FR-013).
"""

from __future__ import annotations

import pytest

from remo_cli.web.operator_auth import (
    ForwardAuthProvider,
    NetworkRestrictedProvider,
    OperatorAuthConfigError,
    build_operator_auth_provider,
)


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        # Starlette headers are case-insensitive; emulate with a lookup helper.
        self._headers = {k.lower(): v for k, v in headers.items()}

    @property
    def headers(self):  # noqa: ANN202
        store = self._headers

        class _H:
            def get(self, name: str, default: str = "") -> str:
                return store.get(name.lower(), default)

        return _H()


def test_forward_auth_requires_header_name():
    with pytest.raises(OperatorAuthConfigError):
        ForwardAuthProvider("")
    with pytest.raises(OperatorAuthConfigError):
        ForwardAuthProvider("   ")


def test_forward_auth_authenticates_only_with_header():
    provider = ForwardAuthProvider("X-Forwarded-User")
    assert provider.authenticate(_FakeRequest({})) is None
    assert provider.authenticate(_FakeRequest({"X-Forwarded-User": "  "})) is None
    identity = provider.authenticate(_FakeRequest({"X-Forwarded-User": "alice"}))
    assert identity is not None
    assert identity.subject == "alice"
    assert identity.provider == "forward"
    assert provider.posture == "forward"


def test_network_restricted_is_anonymous():
    provider = NetworkRestrictedProvider()
    identity = provider.authenticate(_FakeRequest({}))
    assert identity is not None
    assert identity.provider == "network-restricted"
    assert provider.posture == "network-restricted"


class _Settings:
    def __init__(self, operator_auth: str, forward_auth_header: str = "") -> None:
        self.operator_auth = operator_auth
        self.forward_auth_header = forward_auth_header


def test_build_forward_fail_fast_without_header():
    with pytest.raises(OperatorAuthConfigError):
        build_operator_auth_provider(_Settings("forward", ""))


def test_build_forward_ok_with_header():
    provider = build_operator_auth_provider(_Settings("forward", "Remote-User"))
    assert isinstance(provider, ForwardAuthProvider)
    assert provider.header_name == "Remote-User"


def test_build_none_is_network_restricted():
    provider = build_operator_auth_provider(_Settings("none"))
    assert isinstance(provider, NetworkRestrictedProvider)


def test_build_unset_disables_minting():
    assert build_operator_auth_provider(_Settings("")) is None


def test_build_unknown_mode_raises():
    with pytest.raises(OperatorAuthConfigError):
        build_operator_auth_provider(_Settings("bogus"))
