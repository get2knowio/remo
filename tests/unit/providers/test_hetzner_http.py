"""Characterization + regression tests for the four Hetzner HTTP call sites
(019-hygiene-deps-docs US5 / research.md R3).

`_hetzner_api()` is the module's one canonical request constructor, but
three other sites historically bypassed it with hand-rolled
`urllib.request.Request`/`urlopen` calls, each with a *different* error
contract:

- `_query_hetzner_server_ip`: silently returns `""` on ANY failure (missing
  token or transport error), 15s timeout, never raises.
- `info()`'s server lookup: raises `PreconditionError("HETZNER_API_TOKEN is
  not set.")` on a missing token and `PreconditionError("No Hetzner server
  found with name '<n>'.")` when no server matches -- both strings are
  subtly different from `_hetzner_api`'s and `_get_server_by_name`'s own
  wording and must NOT be substituted. Its transport-error text is now
  `_hetzner_api`'s own message, surfaced unchanged -- re-wrapping it printed
  the "Hetzner API" prefix twice. 15s timeout.
- `info()`'s volume lookup: best-effort -- any failure is swallowed, leaving
  `volume_size` empty rather than failing the whole `info()` call. 15s
  timeout.

These tests were written and confirmed GREEN against the *unmodified*
hand-rolled implementation before it was consolidated onto `_hetzner_api`
(T045 in tasks.md), and must stay green afterwards (T046-T049) -- that
before/after stability is the FR-024 "preserve behavior exactly" evidence.
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock

import pytest

from remo_cli.core.errors import OperationFailedError, PreconditionError
from remo_cli.providers import hetzner as providers_hetzner


def _fake_response(payload: dict) -> MagicMock:
    """A MagicMock usable as the `with urlopen(...) as resp:` context value."""
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    resp.read.return_value = json.dumps(payload).encode()
    return resp


# ---------------------------------------------------------------------------
# _query_hetzner_server_ip: silent "" on any failure
# ---------------------------------------------------------------------------


class TestQueryHetznerServerIp:
    def test_returns_empty_string_when_token_missing(self, monkeypatch, mocker):
        monkeypatch.delenv("HETZNER_API_TOKEN", raising=False)
        urlopen = mocker.patch("remo_cli.providers.hetzner.urllib.request.urlopen")

        result = providers_hetzner._query_hetzner_server_ip("dev1")

        assert result == ""
        urlopen.assert_not_called()

    def test_returns_empty_string_on_transport_error(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            side_effect=urllib.error.URLError("boom"),
        )

        result = providers_hetzner._query_hetzner_server_ip("dev1")

        assert result == ""

    def test_returns_ip_on_success(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        payload = {
            "servers": [
                {"public_net": {"ipv4": {"ip": "1.2.3.4"}}},
            ]
        }
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response(payload),
        )

        result = providers_hetzner._query_hetzner_server_ip("dev1")

        assert result == "1.2.3.4"

    def test_uses_15s_timeout(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        urlopen = mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response({"servers": []}),
        )

        providers_hetzner._query_hetzner_server_ip("dev1")

        assert urlopen.call_args.kwargs.get("timeout") == 15

    def test_returns_empty_string_on_unparseable_body(self, monkeypatch, mocker):
        """A 2xx carrying a non-JSON body (proxy/CDN HTML error page, truncated
        response) must still hit the silent-"" contract. The hand-rolled site
        caught `json.JSONDecodeError` explicitly; consolidation moved that
        responsibility into `_hetzner_api`, which now raises
        `OperationFailedError` -- a `ProviderError` this site swallows."""
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        resp = MagicMock()
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        resp.read.return_value = b"<html>502 Bad Gateway</html>"
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen", return_value=resp
        )

        assert providers_hetzner._query_hetzner_server_ip("dev1") == ""

    def test_returns_empty_string_for_ipv6_only_server(self, monkeypatch, mocker):
        """Hetzner reports an IPv6-only server as `public_net.ipv4 = null` --
        key present, value None. A chained `.get(k, {})` returns None from the
        second hop and raises AttributeError on the third, crashing `create()`
        after the VM is already provisioned but before it is registered."""
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        payload = {"servers": [{"public_net": {"ipv4": None, "ipv6": {"ip": "2a01::/64"}}}]}
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response(payload),
        )

        assert providers_hetzner._query_hetzner_server_ip("dev1") == ""


# ---------------------------------------------------------------------------
# info(): server lookup -- raises with its own wording
# ---------------------------------------------------------------------------


class TestInfoServerLookup:
    def test_raises_precondition_error_when_token_missing(self, monkeypatch):
        """info() no longer keeps its own duplicate token check -- it now
        surfaces `_hetzner_api`'s single canonical message. Two divergent
        strings for one condition was the drift consolidation set out to
        remove; the error *class* is unchanged."""
        monkeypatch.delenv("HETZNER_API_TOKEN", raising=False)

        with pytest.raises(PreconditionError) as exc_info:
            providers_hetzner.info("dev1")

        assert str(exc_info.value) == (
            "HETZNER_API_TOKEN is not set; cannot reach the Hetzner Cloud API."
        )

    def test_raises_precondition_error_when_no_server_found(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response({"servers": []}),
        )

        with pytest.raises(PreconditionError) as exc_info:
            providers_hetzner.info("dev1")

        assert str(exc_info.value) == "No Hetzner server found with name 'dev1'."

    def test_defaults_server_name_to_remo_in_not_found_message(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response({"servers": []}),
        )

        with pytest.raises(PreconditionError) as exc_info:
            providers_hetzner.info("")

        assert str(exc_info.value) == "No Hetzner server found with name 'remo'."

    def test_transport_error_message_uses_canonical_helper_prefix(
        self, monkeypatch, mocker
    ):
        """info()'s server lookup funnels through the canonical
        `_hetzner_api()` and surfaces its message unchanged. It used to
        re-wrap with its own "Hetzner API request failed: " prefix, which
        printed the prefix twice (`_hetzner_api` raises `from None`, so the
        interpolated error was already a formatted message). research.md R3
        notes no pre-existing test pinned the byte-exact old string, and the
        helper's method+path text is strictly more informative."""
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        err = urllib.error.URLError("boom")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            side_effect=err,
        )

        with pytest.raises(OperationFailedError) as exc_info:
            providers_hetzner.info("dev1")

        message = str(exc_info.value)
        assert message.startswith("Hetzner API GET /servers?name=dev1 failed:")
        assert message.count("Hetzner API") == 1, f"prefix duplicated: {message}"
        assert "boom" in message

    def test_uses_15s_timeout_for_server_lookup(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        urlopen = mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            return_value=_fake_response({"servers": []}),
        )

        with pytest.raises(PreconditionError):
            providers_hetzner.info("dev1")

        assert urlopen.call_args.kwargs.get("timeout") == 15


# ---------------------------------------------------------------------------
# info(): volume lookup -- best-effort, swallows failures
# ---------------------------------------------------------------------------


class TestInfoVolumeLookup:
    def _server_payload(self) -> dict:
        return {
            "servers": [
                {
                    "name": "dev1",
                    "id": 1,
                    "status": "running",
                    "server_type": {"name": "cx22", "cores": 2, "memory": 4, "disk": 40},
                    "public_net": {"ipv4": {"ip": "1.2.3.4"}},
                    "datacenter": {"location": {"name": "nbg1"}},
                }
            ]
        }

    def test_volume_lookup_transport_failure_does_not_raise(self, monkeypatch, mocker, capsys):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            side_effect=[
                _fake_response(self._server_payload()),
                urllib.error.URLError("volume boom"),
            ],
        )

        # Must not raise -- best-effort, leaves volume_size empty.
        providers_hetzner.info("dev1")

        out = capsys.readouterr().out
        assert "(none attached)" in out

    def test_volume_lookup_uses_15s_timeout(self, monkeypatch, mocker):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        urlopen = mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            side_effect=[
                _fake_response(self._server_payload()),
                urllib.error.URLError("volume boom"),
            ],
        )

        providers_hetzner.info("dev1")

        assert urlopen.call_count == 2
        assert urlopen.call_args_list[1].kwargs.get("timeout") == 15

    def test_volume_lookup_success_reports_size(self, monkeypatch, mocker, capsys):
        monkeypatch.setenv("HETZNER_API_TOKEN", "faketoken")
        mocker.patch(
            "remo_cli.providers.hetzner.urllib.request.urlopen",
            side_effect=[
                _fake_response(self._server_payload()),
                _fake_response({"volumes": [{"size": 50}]}),
            ],
        )

        providers_hetzner.info("dev1")

        out = capsys.readouterr().out
        assert "50 GB" in out
