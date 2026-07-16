"""Mapping tests for the hosts/sessions API response models.

Verifies the newer fields — instance `region` and per-project git status —
propagate from the domain models through `_instance_out` / `_target_out` into
the JSON response shapes.
"""

from __future__ import annotations

from remo_cli.models.discovery import DiscoverySnapshot, InstanceStatus
from remo_cli.models.session_target import (
    DevcontainerRunning,
    SessionTarget,
    ZellijState,
)
from remo_cli.web.api.hosts import _instance_out, _target_out


def test_instance_out_carries_region() -> None:
    snapshot = DiscoverySnapshot(
        instance_id="abc123",
        instance_type="aws",
        instance_name="use1",
        status=InstanceStatus.OK,
        region="us-east-1",
    )
    out = _instance_out(snapshot)
    assert out.region == "us-east-1"


def test_instance_out_region_defaults_to_empty() -> None:
    snapshot = DiscoverySnapshot(
        instance_id="abc123",
        instance_type="incus",
        instance_name="lab",
        status=InstanceStatus.OK,
    )
    assert _instance_out(snapshot).region == ""


def test_target_out_carries_git_status() -> None:
    target = SessionTarget(
        id="deadbeef",
        instance_type="proxmox",
        instance_name="dev1",
        project="api",
        has_devcontainer=True,
        zellij_state=ZellijState.ACTIVE,
        devcontainer_running=DevcontainerRunning.RUNNING,
        discovered_at="2026-07-14T00:00:00Z",
        git_tracked=True,
        git_dirty=True,
        git_ahead=2,
        git_behind=1,
    )
    out = _target_out(target)
    assert (out.git_tracked, out.git_dirty, out.git_ahead, out.git_behind) == (True, True, 2, 1)


def test_target_out_git_defaults_when_unset() -> None:
    target = SessionTarget(
        id="deadbeef",
        instance_type="proxmox",
        instance_name="dev1",
        project="notes",
        has_devcontainer=False,
        zellij_state=ZellijState.ABSENT,
        devcontainer_running=DevcontainerRunning.UNKNOWN,
        discovered_at="2026-07-14T00:00:00Z",
    )
    out = _target_out(target)
    assert (out.git_tracked, out.git_dirty, out.git_ahead, out.git_behind) == (False, False, 0, 0)
