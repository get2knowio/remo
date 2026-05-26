"""Thin wrapper around the broker_install Ansible role.

Each provider's `*_configure.yml` includes the role directly; this helper
exists so Python-driven flows (one-off installs, rotations) can drive the
same role without going through a full configure playbook.
"""

from __future__ import annotations

from remo_cli.core import ansible_runner
from remo_cli.core.config import BROKER_PINNED_VERSION


def run_broker_install_role(
    host: str,
    provider: str,
    extra_vars: dict[str, str] | None = None,
) -> int:
    """Invoke the broker_install role against a single host.

    Returns the ansible-playbook exit code.
    """
    vars_list: list[str] = [
        "-e",
        f"target_host={host}",
        "-e",
        f"target_provider={provider}",
        "-e",
        f"broker_version={BROKER_PINNED_VERSION}",
    ]
    for k, v in (extra_vars or {}).items():
        vars_list.extend(["-e", f"{k}={v}"])

    return ansible_runner.run_playbook(
        playbook=f"{provider}_configure.yml",
        extra_vars=vars_list,
    )
