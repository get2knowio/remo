"""US1 T047 + US6 T089/T090: devcontainer socket mount + language detection."""

import json
from pathlib import Path

import pytest

from remo_cli.core import devcontainer


def test_socket_name_includes_hash(tmp_path: Path):
    project = tmp_path / "test-broker"
    project.mkdir()
    name = devcontainer.socket_name(project)
    assert name.startswith("test-broker-")
    assert name.endswith(".sock")
    # 8-hex suffix
    middle = name.removeprefix("test-broker-").removesuffix(".sock")
    assert len(middle) == 8
    assert all(c in "0123456789abcdef" for c in middle)


def test_socket_name_path_specific(tmp_path: Path):
    a = tmp_path / "alpha" / "test-broker"
    b = tmp_path / "beta" / "test-broker"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert devcontainer.socket_name(a) != devcontainer.socket_name(b)


def test_ensure_socket_mount_adds_entry(tmp_path: Path):
    (tmp_path / ".devcontainer").mkdir()
    dc = tmp_path / ".devcontainer" / "devcontainer.json"
    dc.write_text(json.dumps({"name": "x", "image": "img"}), encoding="utf-8")
    changed = devcontainer.ensure_socket_mount(dc, tmp_path)
    assert changed is True
    data = json.loads(dc.read_text())
    mounts = data["mounts"]
    assert any("/run/remo-broker/sock" in m for m in mounts)


def test_ensure_socket_mount_idempotent(tmp_path: Path):
    (tmp_path / ".devcontainer").mkdir()
    dc = tmp_path / ".devcontainer" / "devcontainer.json"
    dc.write_text(json.dumps({"name": "x", "image": "img"}), encoding="utf-8")
    devcontainer.ensure_socket_mount(dc, tmp_path)
    second = devcontainer.ensure_socket_mount(dc, tmp_path)
    assert second is False


def test_ensure_socket_mount_missing_file_noop(tmp_path: Path):
    dc = tmp_path / "missing.json"
    assert devcontainer.ensure_socket_mount(dc, tmp_path) is False


def test_language_detection_node(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    img = devcontainer.detect_language_image(tmp_path)
    assert "javascript-node" in img


def test_language_detection_python(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    img = devcontainer.detect_language_image(tmp_path)
    assert "python" in img


def test_language_detection_rust(tmp_path: Path):
    (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
    img = devcontainer.detect_language_image(tmp_path)
    assert "rust" in img


def test_language_detection_go(tmp_path: Path):
    (tmp_path / "go.mod").write_text("module x", encoding="utf-8")
    img = devcontainer.detect_language_image(tmp_path)
    assert "go" in img


def test_language_detection_ruby(tmp_path: Path):
    (tmp_path / "Gemfile").write_text("", encoding="utf-8")
    img = devcontainer.detect_language_image(tmp_path)
    assert "ruby" in img


def test_language_detection_default_fallback(tmp_path: Path):
    img = devcontainer.detect_language_image(tmp_path)
    assert "base" in img and "ubuntu" in img


def test_synthesize_devcontainer_writes_with_socket_mount(tmp_path: Path):
    target = devcontainer.synthesize_devcontainer_json(tmp_path)
    data = json.loads(target.read_text())
    assert "mounts" in data
    assert any("/run/remo-broker/sock" in m for m in data["mounts"])


def test_synthesize_devcontainer_idempotent(tmp_path: Path):
    devcontainer.synthesize_devcontainer_json(tmp_path)
    target = tmp_path / ".remo" / "devcontainer.json"
    target.write_text(json.dumps({"name": "custom"}), encoding="utf-8")
    devcontainer.synthesize_devcontainer_json(tmp_path)
    # File unchanged
    assert json.loads(target.read_text()) == {"name": "custom"}


def test_strip_jsonc_preserves_double_slash_in_string():
    raw = '{"path": "a//b"}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed["path"] == "a//b"


def test_strip_jsonc_preserves_block_comment_chars_in_string():
    raw = '{"glob": "/* literal */"}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed["glob"] == "/* literal */"


def test_strip_jsonc_removes_line_comment():
    raw = '{"a": 1 // comment\n, "b": 2}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed == {"a": 1, "b": 2}


def test_strip_jsonc_removes_block_comment():
    raw = '{"a": /* c */ 1}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed == {"a": 1}


def test_strip_jsonc_double_slash_inside_string_with_leading_space():
    raw = '{"x": " //inside"}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed["x"] == " //inside"


def test_strip_jsonc_handles_escaped_quote_in_string():
    raw = r'{"q": "he said \"hi //there\""}'
    parsed = json.loads(devcontainer._strip_jsonc(raw))
    assert parsed["q"] == 'he said "hi //there"'


def test_ensure_socket_mount_with_jsonc_string_containing_slashes(tmp_path: Path):
    (tmp_path / ".devcontainer").mkdir()
    dc = tmp_path / ".devcontainer" / "devcontainer.json"
    dc.write_text(
        '{\n  "name": "x",\n  "image": "img",\n  "workspaceFolder": "/a//b" // trailing\n}\n',
        encoding="utf-8",
    )
    changed = devcontainer.ensure_socket_mount(dc, tmp_path)
    assert changed is True
    data = json.loads(dc.read_text())
    assert data["workspaceFolder"] == "/a//b"
    assert any("/run/remo-broker/sock" in m for m in data["mounts"])
