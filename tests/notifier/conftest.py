"""Shared fixtures for notifier tests (spec 008 — agentsh-sourced flow)."""

from __future__ import annotations

import textwrap
import uuid
from pathlib import Path

import pytest

from remo_cli.notifier.config import NotifierConfig, load_config
from remo_cli.notifier.models import AgentshRequest, ApprovalDecision, Decision
from remo_cli.notifier.transports.base import NotificationTransport, ResponseCallback


class FakeTransport(NotificationTransport):
    """A controllable transport for server/state tests.

    - ``fail_send``: raise on the next send (simulates FR-008 delivery failure).
    - ``auto_resolve``: if set, resolve the request synchronously inside send.
    - ``healthy_flag``: drives ``healthy()``.
    """

    name = "fake"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.fail_send = False
        self.healthy_flag = True
        self.auto_resolve: Decision | None = None
        self.sent: list[AgentshRequest] = []
        self.cancelled: list[tuple[str, str]] = []
        self._callbacks: dict[str, ResponseCallback] = {}

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def healthy(self) -> bool:
        return self.healthy_flag

    async def send_approval_request(
        self, request: AgentshRequest, on_response: ResponseCallback
    ) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append(request)
        self._callbacks[request.id] = on_response
        if self.auto_resolve is not None:
            on_response(
                ApprovalDecision(decision=self.auto_resolve, responder="telegram:tester")
            )

    async def cancel(self, approval_id: str, *, outcome: str = "cancelled") -> None:
        self.cancelled.append((approval_id, outcome))

    def human_decides(self, approval_id: str, decision: Decision) -> None:
        """Test helper: simulate a human tapping a button."""
        self._callbacks[approval_id](
            ApprovalDecision(decision=decision, responder="telegram:tester")
        )


class FakeAgentsh:
    """Duck-typed AgentshClient: serves a pending list and records resolutions."""

    def __init__(self, requests: list[AgentshRequest] | None = None) -> None:
        self.pending: list[AgentshRequest] = list(requests or [])
        self.resolved: list[tuple[str, Decision, str]] = []
        self.fail_poll = False

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def poll(self) -> list[AgentshRequest]:
        if self.fail_poll:
            from remo_cli.notifier.agentsh_client import AgentshError

            raise AgentshError("poll boom")
        return list(self.pending)

    async def resolve(self, approval_id: str, decision: Decision, *, reason: str = "") -> bool:
        self.resolved.append((approval_id, decision, reason))
        self.pending = [r for r in self.pending if r.id != approval_id]
        return True


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    p = tmp_path / "telegram_token"
    p.write_text("12345:FAKE-TOKEN")
    return p


@pytest.fixture
def agentsh_key_file(tmp_path: Path) -> Path:
    p = tmp_path / "agentsh_key"
    p.write_text("approver-key-abc")
    return p


@pytest.fixture
def config_toml(tmp_path: Path, token_file: Path, agentsh_key_file: Path) -> Path:
    p = tmp_path / "notifier.toml"
    p.write_text(
        textwrap.dedent(
            f"""
            [server]
            listen_host = "127.0.0.1"
            listen_port = 18181
            log_level = "info"

            [approval]
            default_timeout_seconds = 300
            max_timeout_seconds = 1800
            max_pending_approvals = 50

            [transport]
            type = "telegram"

            [transport.telegram]
            bot_token_file = "{token_file}"
            authorized_chat_id = 987654321
            message_parse_mode = "MarkdownV2"

            [agentsh]
            api_url = "http://172.17.0.1:8080"
            api_key_file = "{agentsh_key_file}"
            poll_interval_seconds = 1

            [instance]
            id = "test-instance"
            """
        ).strip()
    )
    return p


@pytest.fixture
def config(config_toml: Path) -> NotifierConfig:
    return load_config(config_toml)


def make_request(**overrides) -> AgentshRequest:
    """Build an agentsh ``Request`` for tests (command kind by default)."""
    data: dict = {
        "id": str(uuid.uuid4()),
        "kind": "command",
        "target": "rm -rf /tmp/x",
        "rule": "demo",
        "message": "approve rm?",
        "session_id": "sess-1",
    }
    data.update(overrides)
    return AgentshRequest.model_validate(data)
