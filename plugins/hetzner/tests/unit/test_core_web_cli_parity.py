"""Hetzner rows of remo's out-of-date-nudge parity tests, moved out of core
``tests/integration/test_web_cli_parity.py`` when Hetzner became a plugin.

The one-line "your web deployment may be out of date" nudge must fire after a
SUCCESSFUL `remo hetzner create`/`destroy` iff a push cache exists. Pure
in-process CLI tests: the provider business logic is mocked to succeed.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from remo_cli.core.web_adopt import CachedInstance, DeploymentCache, save_push_cache

_NUDGE = "may now be out of date"
_IMPL = "remo_provider_hetzner.provider"


def _seed_push_cache() -> None:
    save_push_cache(
        {"dep-nudge": DeploymentCache(instances={"n": CachedInstance("f" * 64, ["line"])})}
    )


def _command(verb: str):
    from remo_cli.cli.providers.factory import build_provider_group
    from remo_cli.core.provider_registry import get_descriptor

    return build_provider_group(get_descriptor("hetzner")).commands[verb]


def _mock_success(mocker, verb: str) -> None:
    if verb == "destroy":
        # Shared destroy template: core/lifecycle.run_destroy drives
        # teardown()/snapshot_list() and gates on its own confirmation prompt.
        mocker.patch(f"{_IMPL}.teardown", return_value=None)
        mocker.patch(f"{_IMPL}.snapshot_list", return_value=[])
        mocker.patch("remo_cli.core.lifecycle.confirm", return_value=True)
    else:
        mocker.patch(f"{_IMPL}.{verb}", return_value=0)


class TestHetznerOutOfDateNudge:
    @pytest.mark.parametrize("verb", ["create", "destroy"])
    def test_provider_create_destroy_nudges_with_cache(self, tmp_config_dir, mocker, verb):
        _mock_success(mocker, verb)
        _seed_push_cache()
        result = CliRunner().invoke(_command(verb), [])
        assert result.exit_code == 0, result.output
        assert _NUDGE in result.output

    @pytest.mark.parametrize("verb", ["create", "destroy"])
    def test_provider_create_destroy_absent_without_cache(
        self, tmp_config_dir, mocker, verb
    ):
        _mock_success(mocker, verb)
        result = CliRunner().invoke(_command(verb), [])
        assert result.exit_code == 0, result.output
        assert _NUDGE not in result.output
