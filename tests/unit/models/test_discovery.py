"""Tests for remo_cli.models.discovery."""

from remo_cli.models.capability import RemoteCapability
from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus, TypedError
from remo_cli.models.session_target import (
    DevcontainerRunning,
    SessionTarget,
    ZellijState,
    derive_session_target_id,
)


# -----------------------------------------------------------------------
# DiscoverySnapshot construction
# -----------------------------------------------------------------------


class TestDiscoverySnapshotOkStatus:
    """A successful discovery snapshot carries capability + targets, no error."""

    def test_ok_snapshot_with_targets(self):
        target = SessionTarget(
            id=derive_session_target_id("incus", "myhost/dev", "my-api"),
            instance_type="incus",
            instance_name="myhost/dev",
            project="my-api",
            has_devcontainer=True,
            zellij_state=ZellijState.ACTIVE,
            devcontainer_running=DevcontainerRunning.RUNNING,
            discovered_at="2026-07-13T00:00:00Z",
        )
        capability = RemoteCapability.from_dict(
            {
                "protocol_version": 1,
                "host_tools_version": "2.1.0",
                "projects_root": "/home/remo/projects",
                "operations": ["capabilities", "sessions.list", "sessions.attach"],
                "zellij": True,
                "docker": True,
            }
        )
        snapshot = DiscoverySnapshot(
            instance_id="abc123",
            instance_type="incus",
            instance_name="myhost/dev",
            status=InstanceStatus.OK,
            capability=capability,
            targets=[target],
            refreshed_at="2026-07-13T00:00:01Z",
        )
        assert snapshot.status == InstanceStatus.OK
        assert snapshot.capability is capability
        assert snapshot.targets == [target]
        assert snapshot.error is None

    def test_ok_snapshot_may_have_empty_targets(self):
        """An instance with no projects is still a valid ok snapshot (FR-006)."""
        snapshot = DiscoverySnapshot(
            instance_id="abc123",
            instance_type="incus",
            instance_name="myhost/dev",
            status=InstanceStatus.OK,
            capability=None,
            targets=[],
            refreshed_at="2026-07-13T00:00:01Z",
        )
        assert snapshot.targets == []


class TestDiscoverySnapshotNonOkStatus:
    """A non-ok snapshot carries a typed error instead of a fabricated success."""

    def test_unreachable_snapshot_has_typed_error(self):
        error = TypedError(
            code="unreachable",
            message="SSH connection timed out",
            retryable=True,
            remediation="Check network connectivity and try again.",
        )
        snapshot = DiscoverySnapshot(
            instance_id="abc123",
            instance_type="aws",
            instance_name="devbox",
            status=InstanceStatus.UNREACHABLE,
            error=error,
            refreshed_at="2026-07-13T00:00:01Z",
        )
        assert snapshot.status == InstanceStatus.UNREACHABLE
        assert snapshot.capability is None
        assert snapshot.targets == []
        assert snapshot.error is error
        assert snapshot.error.retryable is True

    def test_incompatible_protocol_snapshot(self):
        error = TypedError(
            code="incompatible_protocol",
            message="Host reports protocol_version=2, client supports [1,1]",
            retryable=False,
            remediation="Update remo on this instance to a compatible version.",
        )
        snapshot = DiscoverySnapshot(
            instance_id="def456",
            instance_type="hetzner",
            instance_name="webserver",
            status=InstanceStatus.INCOMPATIBLE_PROTOCOL,
            error=error,
            refreshed_at="2026-07-13T00:00:01Z",
        )
        assert snapshot.status == InstanceStatus.INCOMPATIBLE_PROTOCOL
        assert snapshot.error.code == "incompatible_protocol"

    def test_all_instance_status_values_match_protocol(self):
        assert InstanceStatus.OK.value == "ok"
        assert InstanceStatus.UNREACHABLE.value == "unreachable"
        assert InstanceStatus.AUTH_FAILED.value == "auth_failed"
        assert InstanceStatus.NO_REMO_HOST.value == "no_remo_host"
        assert InstanceStatus.INCOMPATIBLE_PROTOCOL.value == "incompatible_protocol"
        assert InstanceStatus.MALFORMED.value == "malformed"
        assert InstanceStatus.TIMEOUT.value == "timeout"
