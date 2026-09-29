"""End-to-end path for an out-of-tree provider (spec 027 FR-014), using the
in-repo fixture distribution ``remo-fixture-provider`` (tests/fixtures/...),
installed by the ``dev`` extra: entry point discovered → CLI group generated →
registry v2 round-trip → known_hosts/completion see it → the web hosts API
serializes its type as a plain string → the disable variable removes it."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from remo_cli.cli.main import cli
from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.provider_plugins import plugin_load_records
from remo_cli.core.provider_registry import descriptor_source, get_descriptor, is_provider_type
from remo_cli.core.registry import read_registry, replace_registry
from remo_cli.models.host import KnownHost

pytestmark = pytest.mark.skipif(
    not is_provider_type("fixture"),
    reason="remo-fixture-provider is not installed (uv sync --all-extras)",
)


def test_fixture_plugin_is_discovered_from_its_distribution() -> None:
    assert descriptor_source("fixture") == "remo-fixture-provider 0.1.0"
    [record] = [r for r in plugin_load_records() if r.type_name == "fixture"]
    assert record.status == "loaded" and record.api_version == 1


def test_cli_group_is_generated_from_the_descriptor() -> None:
    runner = CliRunner()
    top = runner.invoke(cli, ["--help"], catch_exceptions=False)
    assert top.exit_code == 0 and "fixture" in top.output
    group = runner.invoke(cli, ["fixture", "--help"], catch_exceptions=False)
    assert group.exit_code == 0
    for verb in ("create", "destroy", "list", "info", "snapshot"):
        assert verb in group.output


def test_registry_round_trip_and_known_hosts(tmp_config_dir: Path) -> None:
    host = KnownHost(
        type="fixture", name="fx1", host="fx1.fixture.invalid", user="remo",
        instance_id="box-0001", access_mode="direct", region="zone-a",
    )
    replace_registry([host])
    raw = (tmp_config_dir / "registry.json").read_text()
    assert '"box_id": "box-0001"' in raw and '"zone": "zone-a"' in raw

    view = read_registry()
    assert view.hosts == [host] and view.warnings == []
    assert [h.name for h in get_known_hosts(type_filter="fixture")] == ["fx1"]
    assert get_descriptor("fixture").display_name == "Fixture"


def test_create_verb_runs_the_plugin_implementation(tmp_config_dir: Path) -> None:
    result = CliRunner().invoke(cli, ["fixture", "create", "--name", "fx2"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert [h.name for h in get_known_hosts(type_filter="fixture")] == ["fx2"]


def test_web_hosts_api_serializes_the_plugin_type_as_a_string() -> None:
    pytest.importorskip("fastapi")
    from remo_cli.web.api.hosts import InstanceOut, InstanceStatus, KnownProviderType

    out = InstanceOut(instance_id="fixture/fx1", instance_type="fixture", instance_name="fx1", status=InstanceStatus.OK)
    assert out.model_dump()["instance_type"] == "fixture"
    assert "fixture" not in {m.value for m in KnownProviderType}


def test_disable_variable_removes_the_group_in_a_fresh_process() -> None:
    env = {**os.environ, "REMO_DISABLE_PROVIDER_PLUGINS": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "remo_cli", "--help"], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "fixture" not in result.stdout
    assert "incus" in result.stdout
