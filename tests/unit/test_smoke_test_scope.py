"""What the provider smoke tests run on, and why.

These jobs provision real instances, so their triggers are a cost decision as
much as a coverage one:

- ``aws`` and ``hetzner`` create **billable** cloud resources. They do not run on
  pull requests.
- ``incus`` creates a container local to the runner, so it is free beyond runner
  minutes, and it exercises the same create → configure → destroy path. It *does*
  run on pull requests, and is what keeps a PR from merging with a broken
  provisioning path.

Before this split, a change touching only ``cli/main.py`` (PR #226, a Windows
error message) provisioned an EC2 instance, a Hetzner server and a container —
about 39 minutes of runner time plus real spend, to prove a string.

The asymmetry between the jobs is deliberate and easy to "tidy up" into
consistency, which would either restore the cost or drop PR coverage entirely.
Hence these tests.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_YML = REPO_ROOT / ".github" / "workflows" / "smoke-test.yml"

PAID_PROVIDERS = ["aws", "hetzner"]
FREE_PROVIDERS = ["incus"]


def _job_block(name: str) -> str:
    """The YAML for one top-level job, up to the next job at the same indent."""
    lines = SMOKE_YML.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln == f"  {name}:"), None)
    assert start is not None, f"job {name!r} not found in smoke-test.yml"

    block = [lines[start]]
    for ln in lines[start + 1 :]:
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", ln):
            break
        block.append(ln)
    return "\n".join(block)


def _if_condition(name: str) -> str:
    """The job's `if:` expression, comments stripped."""
    block = _job_block(name)
    match = re.search(r"if: >-\n((?:\s+(?!#).*\n)+)", block)
    assert match, f"no `if:` block found for job {name!r}"
    return " ".join(match.group(1).split())


def _trigger_paths(event: str) -> list[str]:
    """The `paths:` list for one `on:` event."""
    text = SMOKE_YML.read_text(encoding="utf-8")
    match = re.search(rf"^  {event}:\n(.*?)(?=^  [a-z_]+:$)", text, re.S | re.M)
    assert match, f"event {event!r} not found"
    return re.findall(r"^      - '([^']+)'", match.group(1), re.M)


class TestPaidProvidersSkipPullRequests:
    @pytest.mark.parametrize("provider", PAID_PROVIDERS)
    def test_not_run_on_pull_request(self, provider: str) -> None:
        """A PR must never provision a billable instance."""
        assert "github.event_name != 'pull_request'" in _if_condition(provider), (
            f"the {provider} job would run on pull requests, provisioning a "
            "billable instance for every PR that touches provider code"
        )

    @pytest.mark.parametrize("provider", PAID_PROVIDERS)
    def test_still_runs_elsewhere(self, provider: str) -> None:
        """Excluding PRs must not exclude push-to-main, the weekly schedule, or a
        manual dispatch — otherwise the coverage is simply gone."""
        condition = _if_condition(provider)
        assert "github.event_name != 'workflow_dispatch'" in condition
        assert f"github.event.inputs.provider == '{provider}'" in condition
        # No blanket push/schedule exclusion crept in alongside.
        assert "github.event_name != 'push'" not in condition
        assert "github.event_name != 'schedule'" not in condition

    @pytest.mark.parametrize("provider", PAID_PROVIDERS)
    def test_dependabot_still_excluded(self, provider: str) -> None:
        assert "github.actor != 'dependabot[bot]'" in _if_condition(provider)


class TestFreeProviderCoversPullRequests:
    @pytest.mark.parametrize("provider", FREE_PROVIDERS)
    def test_runs_on_pull_request(self, provider: str) -> None:
        """This is the whole reason the paid ones can skip PRs. If incus also
        stopped running on PRs, a provisioning regression would reach main
        unchecked."""
        assert "github.event_name != 'pull_request'" not in _if_condition(provider), (
            f"the {provider} job has been excluded from pull requests, leaving "
            "PRs with no end-to-end provisioning coverage at all"
        )

    def test_the_local_provider_is_the_one_kept(self) -> None:
        """incus is free because the container is local to the runner. If that
        ever changes, this split stops being free and needs revisiting."""
        block = _job_block("incus")
        assert "remo incus create" in block
        assert "runs-on: ubuntu-latest" in block


class TestPathFilters:
    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_web_subtree_is_excluded(self, event: str) -> None:
        """The web service plays no part in provisioning, so a console-only
        change should provision nothing."""
        paths = _trigger_paths(event)
        assert "!src/remo_cli/web/**" in paths, (
            f"the {event} trigger still provisions for web-only changes"
        )

    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_negation_follows_what_it_narrows(self, event: str) -> None:
        """GitHub evaluates path patterns in order, so a `!` before the broad
        pattern it narrows has no effect — it would silently do nothing."""
        paths = _trigger_paths(event)
        broad = paths.index("src/remo_cli/**")
        negation = paths.index("!src/remo_cli/web/**")
        assert negation > broad, (
            "'!src/remo_cli/web/**' must come AFTER 'src/remo_cli/**' or it is ignored"
        )

    @pytest.mark.parametrize("event", ["pull_request", "push"])
    def test_provisioning_relevant_paths_still_trigger(self, event: str) -> None:
        """The exclusion must stay surgical. Provider logic, shared core, the
        Ansible layer and dependency changes all affect provisioning."""
        paths = _trigger_paths(event)
        for required in ("src/remo_cli/**", "ansible/**", "pyproject.toml", "uv.lock"):
            assert required in paths, f"{required} no longer triggers smoke tests"
