"""SC-008/FR-024: building the full CLI never imports optional provider SDKs."""

from __future__ import annotations

import sys

import click


def test_full_cli_help_does_not_import_optional_sdks() -> None:
    from remo_cli.cli.main import cli

    click.Context(cli).get_help()

    assert "boto3" not in sys.modules
    assert "hcloud" not in sys.modules


def test_all_descriptors_mount_without_importing_optional_sdks() -> None:
    from remo_cli.cli.providers.factory import build_provider_group
    from remo_cli.core.provider_registry import all_descriptors

    for descriptor in all_descriptors():
        build_provider_group(descriptor)

    assert "boto3" not in sys.modules
    assert "hcloud" not in sys.modules
