"""US3 T062: assert Hetzner cloud-init user_data never contains a token-like substring.

This test inspects all `hcloud.Client.servers.create`-like call sites and
asserts the `user_data` payload (if any) contains no obviously secret
substrings. Since the actual server creation happens via Ansible (not the
hcloud Python SDK), the equivalent assertion is on the Ansible role template.
"""

from __future__ import annotations

import re
from pathlib import Path


def _hetzner_server_role_files() -> list[Path]:
    base = Path("ansible/roles/hetzner_server")
    if not base.exists():
        return []
    return sorted(base.rglob("*.yml"))


def test_hetzner_user_data_contains_no_token_lookups():
    """The hetzner_server Ansible role must not embed any token in user_data."""
    forbidden_substrings = (
        "hetzner_api_token",
        "aws_access_key",
        "aws_secret_access",
        "admin_sa_fnox_key",
        "bootstrap-token",
    )
    for src in _hetzner_server_role_files():
        text = src.read_text(encoding="utf-8")
        # Find any user_data block.
        match = re.search(r"user_data\s*:\s*\|.*?(?=^\S|\Z)", text, re.DOTALL | re.MULTILINE)
        if not match:
            continue
        body = match.group(0)
        for needle in forbidden_substrings:
            assert needle not in body, (
                f"{src}: user_data block contains forbidden token reference "
                f"`{needle}` — visible via Hetzner console / metadata API."
            )


def test_hetzner_provision_playbook_does_not_set_token_in_user_data():
    """The provision playbook must not pass `hetzner_api_token` into user_data."""
    playbook = Path("ansible/hetzner_provision.yml")
    if not playbook.exists():
        return
    text = playbook.read_text(encoding="utf-8")
    # The token should appear only in module-level params (e.g. hetzner.hcloud), not user_data.
    assert "user_data:" not in text or "hetzner_api_token" not in text.split("user_data:", 1)[-1].split("\n")[0]
