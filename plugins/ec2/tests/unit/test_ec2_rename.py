"""The ``aws`` -> ``ec2`` rename, end to end with the real descriptor (#258).

Core proves the mechanism with a made-up type (tests/unit/core/
test_provider_rename.py); this pins that the ec2 plugin actually declares it,
so an existing user's ``aws`` entries and ``remo aws`` scripts keep working.
"""

from __future__ import annotations

import importlib
import json

from click.testing import CliRunner

from remo_cli.core import registry
from remo_cli.core.provider_registry import canonical_type_name, get_descriptor

OLD_ENTRY = {
    "type": "aws",
    "name": "box",
    "host": "203.0.113.7",
    "user": "remo",
    "access": "ssm",
    "aws": {"instance_id": "i-0abc", "region": "us-east-1"},
}


def _write(config_dir, body: str, name: str = "registry.json") -> None:
    (config_dir / name).write_text(body)


def _doc(config_dir) -> dict:
    return json.loads((config_dir / "registry.json").read_text())


def test_descriptor_declares_the_rename():
    descriptor = get_descriptor("ec2")
    assert descriptor.legacy_type_names == ("aws",)
    assert descriptor.deprecated_cli_aliases == ("aws",)
    assert canonical_type_name("aws") == "ec2"


def test_connection_hooks_resolve_to_real_functions():
    connection = get_descriptor("ec2").connection
    assert connection.mode_field_aware
    for path in (connection.proxy_hook, connection.pre_connect_hook):
        assert path
        module, _, func = path.rpartition(".")
        assert callable(getattr(importlib.import_module(module), func)), path


def test_v2_aws_entry_reads_as_ec2_and_is_rewritten_once(tmp_config_dir):
    _write(tmp_config_dir, json.dumps({"version": 2, "hosts": [OLD_ENTRY]}))
    (host,) = registry.read_registry().hosts
    assert (host.type, host.instance_id, host.region, host.access_mode) == (
        "ec2",
        "i-0abc",
        "us-east-1",
        "ssm",
    )

    registry.mutate_registry(lambda hosts: hosts)
    first = _doc(tmp_config_dir)
    assert [(e["type"], e.get("ec2"), "aws" in e) for e in first["hosts"]] == [
        ("ec2", {"instance_id": "i-0abc", "region": "us-east-1"}, False)
    ]
    registry.mutate_registry(lambda hosts: hosts)
    assert _doc(tmp_config_dir) == first


def test_legacy_known_hosts_aws_line_migrates_to_ec2(tmp_config_dir):
    _write(tmp_config_dir, "aws:box:203.0.113.7:remo:i-0abc:ssm:us-east-1\n", "known_hosts")
    (host,) = registry.read_registry().hosts
    assert (host.type, host.instance_id, host.region) == ("ec2", "i-0abc", "us-east-1")
    assert [e["type"] for e in _doc(tmp_config_dir)["hosts"]] == ["ec2"]


def test_remo_aws_alias_is_hidden_warns_and_runs():
    from remo_cli.cli.main import cli

    runner = CliRunner()
    assert "aws" not in runner.invoke(cli, ["--help"]).output.split()
    result = runner.invoke(cli, ["aws", "list", "--help"])
    assert result.exit_code == 0, result.output
    assert "`remo aws` is deprecated" in result.stderr
    assert "use `remo ec2`" in result.stderr
    assert "Usage:" in result.stdout
