"""Tests for devcontainer-runtime resolution in providers/proxmox.create.

The runtime resolves as: explicit flag > REMO_DEVCONTAINER_RUNTIME env >
built-in default ("devcontainer"). The resolved value is passed to Ansible as
`-e devcontainer_runtime=<value>`.
"""

from __future__ import annotations

import click
import pytest

from remo_cli.providers import proxmox as providers_proxmox


def _extra_value(extra_vars: list[str], key: str) -> str | None:
    """Return the value of the last `-e key=value` entry, or None."""
    prefix = f"{key}="
    found: str | None = None
    for item in extra_vars:
        if item.startswith(prefix):
            found = item[len(prefix):]
    return found


@pytest.fixture
def capture_runtime(mocker):
    """Patch create's side effects; return a getter for the runtime extra-var.

    run_playbook returns rc=1 so create() skips all post-provision work
    (vmid lookup, known_hosts save) and returns immediately.
    """
    captured: dict[str, list[str]] = {}

    def fake_run(playbook, extra_vars=None, **kwargs):
        captured["extra_vars"] = extra_vars or []
        return 1

    mocker.patch("remo_cli.providers.proxmox.run_playbook", side_effect=fake_run)
    mocker.patch("remo_cli.providers.proxmox.detect_timezone", return_value="")
    mocker.patch(
        "remo_cli.providers.proxmox.get_current_version", return_value="unknown"
    )
    mocker.patch("remo_cli.providers.proxmox.remove_known_host")

    def run(devcontainer_runtime=None):
        providers_proxmox.create(
            name="dev1", host="pve", devcontainer_runtime=devcontainer_runtime
        )
        return _extra_value(captured["extra_vars"], "devcontainer_runtime")

    return run


def test_default_runtime_is_devcontainer(capture_runtime, monkeypatch):
    monkeypatch.delenv("REMO_DEVCONTAINER_RUNTIME", raising=False)
    assert capture_runtime() == "devcontainer"


def test_explicit_flag_selects_deacon(capture_runtime, monkeypatch):
    monkeypatch.delenv("REMO_DEVCONTAINER_RUNTIME", raising=False)
    assert capture_runtime(devcontainer_runtime="deacon") == "deacon"


def test_env_var_default_selects_deacon(capture_runtime, monkeypatch):
    monkeypatch.setenv("REMO_DEVCONTAINER_RUNTIME", "deacon")
    assert capture_runtime() == "deacon"


def test_flag_overrides_env_var(capture_runtime, monkeypatch):
    monkeypatch.setenv("REMO_DEVCONTAINER_RUNTIME", "deacon")
    assert capture_runtime(devcontainer_runtime="devcontainer") == "devcontainer"


def test_invalid_env_var_is_rejected(capture_runtime, monkeypatch):
    # A mis-cased/bogus env value must not silently fall back to the Node
    # runtime; the flag path is guarded by click.Choice, the env path here.
    monkeypatch.setenv("REMO_DEVCONTAINER_RUNTIME", "Deacon")
    with pytest.raises(click.BadParameter):
        capture_runtime()
