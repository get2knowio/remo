"""Tests for remo_cli.models.session_target."""

from remo_cli.models.session_target import (
    DevcontainerRunning,
    SessionTarget,
    ZellijState,
    derive_session_target_id,
)


# -----------------------------------------------------------------------
# derive_session_target_id() determinism
# -----------------------------------------------------------------------


class TestDeriveSessionTargetIdDeterminism:
    """Same inputs always produce the same id; different inputs diverge."""

    def test_same_inputs_produce_same_id(self):
        first = derive_session_target_id("incus", "myhost/dev", "my-api")
        second = derive_session_target_id("incus", "myhost/dev", "my-api")
        assert first == second

    def test_different_project_produces_different_id(self):
        a = derive_session_target_id("incus", "myhost/dev", "my-api")
        b = derive_session_target_id("incus", "myhost/dev", "notes")
        assert a != b

    def test_different_instance_name_produces_different_id(self):
        a = derive_session_target_id("aws", "devbox", "my-api")
        b = derive_session_target_id("aws", "otherbox", "my-api")
        assert a != b

    def test_different_instance_type_produces_different_id(self):
        a = derive_session_target_id("aws", "devbox", "my-api")
        b = derive_session_target_id("hetzner", "devbox", "my-api")
        assert a != b

    def test_id_is_nonempty_hex_string(self):
        result = derive_session_target_id("incus", "myhost/dev", "my-api")
        assert isinstance(result, str)
        assert len(result) > 0
        int(result, 16)  # raises ValueError if not valid hex

    def test_id_does_not_contain_raw_project_substring(self):
        """Opacity smoke check: the id must not leak the raw project name."""
        project = "super-secret-project"
        result = derive_session_target_id("incus", "myhost/dev", project)
        assert project not in result

    def test_id_does_not_contain_raw_path_like_substrings(self):
        """Opacity smoke check: the id must not leak path-like fragments."""
        instance_name = "myhost/devcontainer"
        result = derive_session_target_id("incus", instance_name, "my-api")
        assert "myhost" not in result
        assert "devcontainer" not in result
        assert "/" not in result


# -----------------------------------------------------------------------
# SessionTarget construction
# -----------------------------------------------------------------------


class TestSessionTargetConstruction:
    """Basic construction of the SessionTarget dataclass."""

    def test_basic_construction(self):
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
        assert target.instance_type == "incus"
        assert target.project == "my-api"
        assert target.zellij_state == ZellijState.ACTIVE
        assert target.devcontainer_running == DevcontainerRunning.RUNNING

    def test_zellij_state_string_values_match_protocol(self):
        assert ZellijState.ACTIVE.value == "active"
        assert ZellijState.EXITED.value == "exited"
        assert ZellijState.ABSENT.value == "absent"

    def test_devcontainer_running_string_values_match_protocol(self):
        assert DevcontainerRunning.RUNNING.value == "running"
        assert DevcontainerRunning.STOPPED.value == "stopped"
        assert DevcontainerRunning.UNKNOWN.value == "unknown"

    def test_no_devcontainer_target(self):
        target = SessionTarget(
            id=derive_session_target_id("incus", "myhost/dev", "notes"),
            instance_type="incus",
            instance_name="myhost/dev",
            project="notes",
            has_devcontainer=False,
            zellij_state=ZellijState.ABSENT,
            devcontainer_running=DevcontainerRunning.UNKNOWN,
            discovered_at="2026-07-13T00:00:00Z",
        )
        assert target.has_devcontainer is False
        assert target.devcontainer_running == DevcontainerRunning.UNKNOWN
