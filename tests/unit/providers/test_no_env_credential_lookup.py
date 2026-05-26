"""US3 T060: regression guard — provider modules must not read credential env vars.

The `_get_*_token()` helper functions in providers/*.py are allowed to fall
back to env vars (for backward compatibility), but business-logic code paths
must route through the helper or `core.fnox.get(...)`. We assert this by
greping for the specific credential env names in non-helper contexts.
"""

from __future__ import annotations

import re
from pathlib import Path

FORBIDDEN_ENV_NAMES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "NPM_TOKEN",
    "GITHUB_TOKEN",
)


def _provider_sources() -> list[Path]:
    base = Path("src/remo_cli/providers")
    return sorted(p for p in base.glob("*.py") if p.name != "__init__.py")


def test_providers_do_not_read_credential_env_directly():
    """No `os.environ.get("AWS_ACCESS_KEY_ID")` etc anywhere in providers/."""
    for src in _provider_sources():
        text = src.read_text(encoding="utf-8")
        for env_name in FORBIDDEN_ENV_NAMES:
            # The pattern: any string literal mentioning the env name preceded
            # by os.environ.get/getenv. Match within 60 chars on the same line.
            pattern = rf"os\.(environ\.get|getenv)\(\s*[\"']{env_name}"
            matches = re.findall(pattern, text)
            assert not matches, (
                f"{src} contains forbidden credential env read for {env_name}: "
                f"{matches}"
            )


def test_hetzner_only_env_read_is_in_helper():
    """Hetzner permits a single env fallback inside `_get_hetzner_api_token`."""
    text = Path("src/remo_cli/providers/hetzner.py").read_text(encoding="utf-8")
    hits = re.findall(
        r"os\.environ\.get\(\s*[\"']HETZNER_API_TOKEN[\"']", text
    )
    # Exactly one fallback inside the helper.
    assert len(hits) == 1, f"expected one env fallback in _get_hetzner_api_token, got {len(hits)}"
