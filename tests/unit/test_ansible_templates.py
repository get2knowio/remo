"""Jinja2 parse check for every Ansible template in the repo.

Catches syntax errors before they hit a real Ansible run — most notably the
``${#var}`` bash array-length idiom, which Jinja2 reads as a ``{#`` comment
opener and consumes the rest of the file looking for ``#}``. That class of
bug otherwise only surfaces on a live host during a smoke test.

We use ``Environment.parse`` rather than ``render`` so the test doesn't need
to know what variables each template expects.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, TemplateSyntaxError

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = REPO_ROOT / "ansible"


def _all_templates() -> list[Path]:
    return sorted(TEMPLATE_ROOT.rglob("*.j2"))


@pytest.mark.parametrize("template_path", _all_templates(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_template_parses(template_path: Path) -> None:
    env = Environment(autoescape=False)
    source = template_path.read_text()
    try:
        env.parse(source)
    except TemplateSyntaxError as exc:
        pytest.fail(
            f"{template_path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.message}"
        )
