"""Adopt pairing-code plumbing (012-web-adopt-pairing, T010).

The CLI sends the pasted pairing code as `Authorization: Bearer <code>` on every
setup call, and a dormant `404` maps to an actionable "reopen the page for a
fresh code" message (FR-018/FR-020).
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from remo_cli.core.web_adopt import SetupApiClient, SetupNotFoundError


def test_bearer_header_carries_the_pairing_code(mocker):
    client = SetupApiClient("http://svc:8080", "the-pairing-code")

    captured = {}

    class _Resp:
        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(request, timeout=None):
        captured["auth"] = request.get_header("Authorization")
        return _Resp()

    mocker.patch.object(urllib.request, "urlopen", side_effect=fake_urlopen)
    client.get_status()
    assert captured["auth"] == "Bearer the-pairing-code"


def test_dormant_404_leads_with_code_rotation(mocker):
    client = SetupApiClient("http://svc:8080", "stale-code")

    def raise_404(request, timeout=None):
        raise urllib.error.HTTPError(
            "http://svc:8080/api/v1/setup/status", 404, "Not Found", {}, None
        )

    mocker.patch.object(urllib.request, "urlopen", side_effect=raise_404)
    with pytest.raises(SetupNotFoundError) as excinfo:
        client.get_status()
    message = str(excinfo.value)
    assert "dormant" in message
    assert "fresh code" in message
    # #159: the leading advice is "copy the code from the MOST RECENT mint",
    # not "reopen the page" — reopening mints again, rotating away whatever the
    # operator just copied, which is how the trap compounds.
    assert "most recent" in message.lower()
    assert message.lower().index("most recent") < message.lower().index("reopen")
