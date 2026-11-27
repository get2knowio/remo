"""scripts/release_manifest.py: generate + verify (contracts/release-manifest.md)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_manifest.py"
REPO = "get2knowio/remo"


def _load():
    spec = importlib.util.spec_from_file_location("release_manifest", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["release_manifest"] = mod
    spec.loader.exec_module(mod)
    return mod


rm = _load()


def make_wheel(dist: Path, name: str, version: str) -> Path:
    norm = name.replace("-", "_")
    path = dist / f"{norm}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            f"{norm}-{version}.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
    return path


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    make_wheel(d, "remo-provider-hetzner", "0.1.0")
    make_wheel(d, "remo-cli", "5.0.0rc1")
    make_wheel(d, "remo-provider-ec2", "0.1.0")
    (d / "remo_cli-5.0.0rc1.tar.gz").write_bytes(b"sdist")
    return d


def gen(dist: Path, tag="rc-5.0.0rc1", channel="prerelease") -> Path:
    out = dist.parent / "manifest.json"
    rc = rm.main(
        ["generate", "--dist", str(dist), "--tag", tag, "--channel", channel,
         "--repo", REPO, "--out", str(out)]
    )
    assert rc == 0
    return out


def verify(dist: Path, manifest: Path, tag: str | None = None) -> int:
    argv = ["verify", "--dist", str(dist), "--manifest", str(manifest)]
    if tag:
        argv += ["--tag", tag]
    return rm.main(argv)


def rewrite(manifest: Path, fn) -> None:
    data = json.loads(manifest.read_text())
    fn(data)
    manifest.write_text(rm.render_manifest(data))


class TestGenerate:
    def test_exact_layout(self, dist: Path) -> None:
        text = gen(dist).read_text()
        lines = text.splitlines()
        assert text.endswith("\n")
        assert lines[0] == (
            '{"format":1,"release":"5.0.0rc1","tag":"rc-5.0.0rc1",'
            '"channel":"prerelease","repo":"get2knowio/remo","components":['
        )
        assert lines[-1] == "]}"
        body = lines[1:-1]
        assert len(body) == 3
        base = "https://github.com/get2knowio/remo/releases/download/rc-5.0.0rc1/"
        sha = hashlib.sha256((dist / "remo_cli-5.0.0rc1-py3-none-any.whl").read_bytes()).hexdigest()
        assert body[0] == (
            '{"name":"remo-cli","dist":"remo-cli","version":"5.0.0rc1","kind":"cli",'
            f'"file":"remo_cli-5.0.0rc1-py3-none-any.whl","url":"{base}remo_cli-5.0.0rc1-py3-none-any.whl",'
            f'"sha256":"{sha}"}},'
        )
        assert body[1].startswith('{"name":"ec2","dist":"remo-provider-ec2","version":"0.1.0","kind":"plugin"')
        assert body[1].endswith("},")
        assert body[2].startswith('{"name":"hetzner"')
        assert body[2].endswith("}")

    def test_json_round_trip_and_order(self, dist: Path) -> None:
        data = json.loads(gen(dist).read_text())
        assert [c["name"] for c in data["components"]] == ["remo-cli", "ec2", "hetzner"]
        assert [c["kind"] for c in data["components"]] == ["cli", "plugin", "plugin"]
        assert list(data["components"][0]) == [
            "name", "dist", "version", "kind", "file", "url", "sha256"]
        assert data["release"] == "5.0.0rc1"

    def test_sdists_ignored(self, dist: Path) -> None:
        text = gen(dist).read_text()
        assert "tar.gz" not in text

    def test_unknown_wheel_refused(self, dist: Path, capsys) -> None:
        make_wheel(dist, "requests", "2.0.0")
        rc = rm.main(["generate", "--dist", str(dist), "--tag", "rc-5.0.0rc1",
                      "--channel", "prerelease", "--repo", REPO,
                      "--out", str(dist.parent / "m.json")])
        assert rc == 2
        assert "requests" in capsys.readouterr().err

    def test_no_cli_wheel_refused(self, tmp_path: Path, capsys) -> None:
        d = tmp_path / "d"
        d.mkdir()
        make_wheel(d, "remo-provider-ec2", "0.1.0")
        rc = rm.main(["generate", "--dist", str(d), "--tag", "v1.0.0",
                      "--channel", "stable", "--repo", REPO, "--out", str(tmp_path / "m")])
        assert rc == 2

    def test_wheel_without_metadata_refused(self, dist: Path) -> None:
        bad = dist / "junk-1.0-py3-none-any.whl"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("x.txt", "x")
        rc = rm.main(["generate", "--dist", str(dist), "--tag", "rc-5.0.0rc1",
                      "--channel", "prerelease", "--repo", REPO,
                      "--out", str(dist.parent / "m.json")])
        assert rc == 2

    @pytest.mark.parametrize(
        ("tag", "channel"),
        [("rc-9.9.9rc1", "prerelease"), ("rc-5.0.0rc2", "prerelease"),
         ("v9.9.9", "stable"), ("v5.0.0", "stable")],
    )
    def test_tag_must_name_the_cli_release(self, dist: Path, capsys, tag, channel) -> None:
        out = dist.parent / "manifest.json"
        rc = rm.main(["generate", "--dist", str(dist), "--tag", tag, "--channel", channel,
                      "--repo", REPO, "--out", str(out)])
        assert rc != 0
        assert not out.exists()
        assert "does not name release 5.0.0rc1" in capsys.readouterr().err

    def test_stable_tag_for_prerelease_version_allowed(self, dist: Path) -> None:
        # release.yml cuts `v` tags for release-please pre-releases too; the
        # manifest channel follows the tag prefix, and the version must match.
        m = gen(dist, tag="v5.0.0rc1", channel="stable")
        assert verify(dist, m, tag="v5.0.0rc1") == 0

    def test_default_out_is_dist_manifest(self, dist: Path) -> None:
        rc = rm.main(["generate", "--dist", str(dist), "--tag", "rc-5.0.0rc1",
                      "--channel", "prerelease", "--repo", REPO])
        assert rc == 0
        assert (dist / "manifest.json").is_file()

    def test_script_runs_as_subprocess(self, dist: Path) -> None:
        out = dist.parent / "m.json"
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "generate", "--dist", str(dist), "--tag",
             "rc-5.0.0rc1", "--channel", "prerelease", "--repo", REPO, "--out", str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "verify", "--dist", str(dist), "--manifest", str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


class TestVerify:
    def test_clean_passes(self, dist: Path) -> None:
        m = gen(dist)
        assert verify(dist, m) == 0
        assert verify(dist, m, tag="rc-5.0.0rc1") == 0

    def test_stable_clean_passes(self, tmp_path: Path) -> None:
        d = tmp_path / "s"
        d.mkdir()
        make_wheel(d, "remo-cli", "5.0.0")
        m = gen(d, tag="v5.0.0", channel="stable")
        assert verify(d, m, tag="v5.0.0") == 0

    def test_invalid_json(self, dist: Path, capsys) -> None:
        m = gen(dist)
        m.write_text("{not json")
        assert verify(dist, m) != 0
        assert "json" in capsys.readouterr().err.lower()

    def test_invalid_layout(self, dist: Path, capsys) -> None:
        m = gen(dist)
        data = json.loads(m.read_text())
        m.write_text(json.dumps(data, indent=2) + "\n")  # valid JSON, wrong layout
        assert verify(dist, m) != 0
        assert "layout" in capsys.readouterr().err.lower()

    def test_missing_manifest(self, dist: Path) -> None:
        assert verify(dist, dist.parent / "nope.json") != 0

    def test_channel_tag_mismatch(self, dist: Path, capsys) -> None:
        m = gen(dist)
        rewrite(m, lambda d: d.update(channel="stable"))
        assert verify(dist, m) != 0
        assert "channel" in capsys.readouterr().err

    def test_tag_arg_mismatch(self, dist: Path, capsys) -> None:
        m = gen(dist)
        assert verify(dist, m, tag="rc-9.9.9rc9") != 0
        assert "tag" in capsys.readouterr().err

    def test_cli_version_differs_from_release(self, dist: Path, capsys) -> None:
        m = gen(dist)

        def f(d):
            # Keep tag and urls consistent with the forged release so the
            # cli-version rule, not the tag rule, is what refuses it.
            d.update(release="5.0.0rc2", tag="rc-5.0.0rc2")
            for c in d["components"]:
                c["url"] = c["url"].replace("/rc-5.0.0rc1/", "/rc-5.0.0rc2/")
        rewrite(m, f)
        assert verify(dist, m) != 0
        assert "cli version 5.0.0rc1 differs" in capsys.readouterr().err

    def test_tag_names_another_release(self, dist: Path, capsys) -> None:
        # The Tier 2 defect: a manifest whose tag (and so every url) names a
        # different release than the wheels it lists must not verify.
        m = gen(dist)

        def f(d):
            d["tag"] = "rc-9.9.9rc1"
            for c in d["components"]:
                c["url"] = c["url"].replace("/rc-5.0.0rc1/", "/rc-9.9.9rc1/")
        rewrite(m, f)
        assert verify(dist, m) != 0
        assert "expected 'rc-5.0.0rc1'" in capsys.readouterr().err
        assert verify(dist, m, tag="rc-9.9.9rc1") != 0

    def test_extra_wheel_in_dist(self, dist: Path, capsys) -> None:
        m = gen(dist)
        make_wheel(dist, "remo-provider-other", "0.1.0")
        assert verify(dist, m) != 0
        assert "remo_provider_other" in capsys.readouterr().err

    def test_missing_file(self, dist: Path, capsys) -> None:
        m = gen(dist)
        (dist / "remo_provider_ec2-0.1.0-py3-none-any.whl").unlink()
        assert verify(dist, m) != 0
        assert "remo_provider_ec2" in capsys.readouterr().err

    def test_sha_mismatch(self, dist: Path, capsys) -> None:
        m = gen(dist)
        w = dist / "remo_provider_ec2-0.1.0-py3-none-any.whl"
        w.write_bytes(w.read_bytes() + b"x")
        assert verify(dist, m) != 0
        assert "sha256" in capsys.readouterr().err

    def test_foreign_url(self, dist: Path, capsys) -> None:
        m = gen(dist)

        def f(d):
            d["components"][1]["url"] = "https://evil.example/x.whl"
        rewrite(m, f)
        assert verify(dist, m) != 0
        assert "url" in capsys.readouterr().err

    def test_duplicate_name(self, dist: Path, capsys) -> None:
        m = gen(dist)

        def f(d):
            d["components"][2]["name"] = "ec2"
        rewrite(m, f)
        assert verify(dist, m) != 0
        assert "duplicate" in capsys.readouterr().err

    def test_plugin_named_web(self, dist: Path, capsys) -> None:
        m = gen(dist)

        def f(d):
            d["components"][2]["name"] = "web"
        rewrite(m, f)
        assert verify(dist, m) != 0
        assert "web" in capsys.readouterr().err
