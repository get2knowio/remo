"""Docs must describe the devcontainer runtime honestly (spec 026, Principle VIII).

The conditional default exists because deacon is *not verified* on hosts whose
kernel refuses nested overlayfs. These checks keep that statement, the `auto`
request value, and the role table in the docs — and keep "experimental" out
of the description of what is now the default.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text()


def test_nested_overlayfs_doc_states_conditional_default_and_unverified() -> None:
    text = _read("docs/nested-overlayfs.md")
    section = text[text.index("### The `deacon` runtime") :]
    assert "not verified" in section
    assert "auto" in section and "keeps the reference CLI" in section
    assert "BUILDX_BUILDER=remo-native" in section


def test_proxmox_doc_documents_auto_and_switching() -> None:
    text = _read("docs/proxmox.md")
    assert "## Devcontainer runtime (deacon by default)" in text
    assert "`auto`" in text
    assert "Stop\nrunning projects first" in text or "Stop running projects first" in text.replace("**", "")
    assert "unsupported" in text  # mixed runtimes


def test_readme_env_var_row_documents_auto() -> None:
    row = next(line for line in _read("README.md").splitlines() if "REMO_DEVCONTAINER_RUNTIME" in line and line.startswith("|"))
    assert "`auto` (default" in row


def test_ansible_readme_lists_the_deacon_role() -> None:
    text = _read("ansible/README.md")
    assert re.search(r"^\| `deacon` \|", text, flags=re.M)


def test_no_doc_calls_the_default_experimental() -> None:
    for rel in ("docs/proxmox.md", "README.md", "ansible/README.md", "ansible/roles/user_setup/defaults/main.yml", "ansible/tasks/configure_dev_tools.yml", "src/remo_cli/core/config.py", "src/remo_cli/core/provider_registry.py"):
        assert "experimental" not in _read(rel).lower(), rel
