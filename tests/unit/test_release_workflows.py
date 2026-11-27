"""Structure of the release workflows (specs/031-install-path, FR-001/003/005).

Parsed with PyYAML (present via ansible-core). These pin the properties that
make one build feed both the GitHub release and PyPI, and that stop plugins
reaching PyPI; none of it can be exercised without a real tag push.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

WF = Path(__file__).resolve().parents[2] / ".github" / "workflows"
SHA = re.compile(r"^[0-9a-f]{40}$")


def _load(name: str) -> dict:
    return yaml.safe_load((WF / name).read_text(encoding="utf-8"))


def _runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uses(job: dict) -> list[str]:
    return [s["uses"] for s in job["steps"] if "uses" in s]


def _step_with_uses(job: dict, prefix: str) -> dict:
    return next(s for s in job["steps"] if s.get("uses", "").startswith(prefix))


class TestReleaseYml:
    wf = _load("release.yml")
    jobs = wf["jobs"]

    def test_build_job_builds_everything_once(self) -> None:
        runs = _runs(self.jobs["build"])
        assert "uv build --all-packages" in runs
        assert "scripts/check_plugin_wheels.sh" in runs
        assert "release_manifest.py generate" in runs
        assert "release_manifest.py verify" in runs
        assert _step_with_uses(self.jobs["build"], "actions/upload-artifact")

    def test_release_job_attaches_wheels_and_manifest_and_attests(self) -> None:
        job = self.jobs["release"]
        assert "build" in job["needs"]
        step = _step_with_uses(job, "softprops/action-gh-release")
        files = step["with"]["files"]
        assert "*.whl" in files
        # #290: the manifest marks a release installable, so it is uploaded
        # strictly after the wheels it lists, never alongside them.
        assert "manifest.json" not in files
        steps = job["steps"]
        manifest_steps = [
            i for i, s in enumerate(steps)
            if "gh release upload" in s.get("run", "") and "dist/manifest.json" in s["run"]
        ]
        assert len(manifest_steps) == 1
        assert manifest_steps[0] > steps.index(step)
        _step_with_uses(job, "actions/attest-build-provenance")
        perms = job["permissions"]
        assert perms["id-token"] == "write"
        assert perms["attestations"] == "write"
        assert perms["contents"] == "write"

    def test_publish_does_not_rebuild_and_only_ships_remo_cli(self) -> None:
        job = self.jobs["publish"]
        runs = _runs(job)
        assert "uv build" not in runs
        _step_with_uses(job, "actions/download-artifact")
        assert "remo_cli-*" in runs
        assert "remo_provider" not in runs  # no plugin wheel is copied
        pub = _step_with_uses(job, "pypa/gh-action-pypi-publish")
        assert pub["with"]["packages-dir"] == "pypi-dist/"

    def test_third_party_actions_are_sha_pinned(self) -> None:
        for name, job in self.jobs.items():
            for uses in _uses(job):
                if uses.startswith("./"):
                    continue
                ref = uses.split("@", 1)[1]
                assert SHA.match(ref), f"{name}: {uses} is not SHA-pinned"

    def test_release_notes_lead_with_the_installer(self) -> None:
        runs = _runs(self.jobs["release"])
        assert "https://raw.githubusercontent.com/get2knowio/remo/main/install.sh" in runs

    def test_prerelease_detection_step_kept(self) -> None:
        names = [s.get("name") for s in self.jobs["build"]["steps"]]
        assert "Check if pre-release" in names


class TestDevBuildYml:
    wf = _load("dev-build.yml")
    job = wf["jobs"]["prerelease"]
    runs = _runs(job)

    def test_generates_and_verifies_manifest(self) -> None:
        assert "release_manifest.py generate" in self.runs
        assert "release_manifest.py verify" in self.runs
        assert "--channel prerelease" in self.runs

    def test_attaches_only_wheels_and_manifest_at_the_built_commit(self) -> None:
        assert "dist/*.whl dist/manifest.json" in self.runs
        assert not re.search(r"dist/\*(?!\.)", self.runs.split("gh release create")[1])
        assert '--target "$GITHUB_SHA"' in self.runs

    def test_attests_wheels(self) -> None:
        _step_with_uses(self.job, "actions/attest-build-provenance")
        perms = self.job["permissions"]
        assert perms["id-token"] == "write"
        assert perms["attestations"] == "write"
        assert perms["contents"] == "write"

    def test_still_refuses_v_tags(self) -> None:
        assert "v*)" in self.runs and "release.yml" in self.runs

    def test_notes_show_the_installer(self) -> None:
        assert "install.sh" in self.runs and "--with" in self.runs


class TestCiYml:
    job = _load("ci.yml")["jobs"]["wheel-install"]

    def test_proves_both_release_shapes(self) -> None:
        runs = _runs(self.job)
        assert runs.count("release_manifest.py generate") >= 2
        assert runs.count("release_manifest.py verify") >= 2
        assert "--channel stable" in runs and "--channel prerelease" in runs
        assert re.search(r"--tag \"?v", runs) and re.search(r"--tag \"?rc-", runs)

    def test_lint_covers_the_manifest_script(self) -> None:
        lint = _runs(_load("ci.yml")["jobs"]["lint"])
        assert "ruff check src/remo_cli scripts/release_manifest.py" in lint
