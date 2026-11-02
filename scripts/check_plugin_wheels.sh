#!/usr/bin/env bash
# Prove `remo.providers` entry-point discovery on BUILT wheels, not the source
# tree (#222, Constitution IX: entry points are package metadata).
#
#   scripts/check_plugin_wheels.sh DIST_DIR
#
# DIST_DIR holds remo_cli-*.whl, remo_provider_hetzner-*.whl and
# remo_provider_ec2-*.whl (from `uv build --wheel --all-packages`). Each
# scenario installs into its own fresh venv, so nothing from the source tree or
# a dev environment leaks in. Used by ci.yml (wheel-install) and dev-build.yml.
set -euo pipefail

dist="$(cd "${1:?usage: $0 DIST_DIR}" && pwd)"
repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

remo_whl="$(ls "$dist"/remo_cli-*.whl)"
hetzner_whl="$(ls "$dist"/remo_provider_hetzner-*.whl)"
ec2_whl="$(ls "$dist"/remo_provider_ec2-*.whl)"

fail() { echo "FAIL: $*" >&2; exit 1; }
venv() { uv venv -q "$work/$1"; echo "$work/$1/bin"; }
site_mb() { du -sm "$("$1/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')" | cut -f1; }

echo "== 1. remo-cli alone: two built-ins, no hetzner/ec2, neither hcloud nor boto3 installed"
bin="$(venv core)"
uv pip install -q --python "$bin/python" "$remo_whl"
out="$("$bin/remo" providers)"; echo "$out"
for t in incus proxmox; do grep -qE "^$t +.* builtin +loaded" <<<"$out" || fail "$t not a loaded built-in"; done
for t in hetzner ec2 aws; do
  grep -q "^$t " <<<"$out" && fail "$t present without its plugin"
  "$bin/remo" "$t" --help >/dev/null 2>&1 && fail "'remo $t' exists without its plugin"
done
for mod in hcloud boto3 botocore; do
  "$bin/python" -c "import $mod" 2>/dev/null && fail "$mod installed by remo-cli alone"
done
core_mb="$(site_mb "$bin")"
meta="$("$bin/python" -c 'from importlib.metadata import metadata; print("\n".join(metadata("remo-cli").get_all("Requires-Dist") or []))')"
grep -qxE "remo-provider-hetzner; extra == [\"']hetzner[\"']" <<<"$meta" || fail "remo-cli lacks the [hetzner] extra: $meta"
grep -qxE "remo-provider-ec2; extra == [\"']ec2[\"']" <<<"$meta" || fail "remo-cli lacks the [ec2] extra: $meta"
grep -qi 'hcloud' <<<"$meta" && fail "remo-cli still requires hcloud"
grep -qiE '^boto' <<<"$meta" && fail "remo-cli still requires boto3"
grep -qi 'remo-fixture-provider' <<<"$meta" && fail "the test fixture leaked into remo-cli's metadata"

echo "== 2. #222: the in-repo fixture plugin is discovered from the wheel"
bin="$(venv fixture)"
uv pip install -q --python "$bin/python" "$remo_whl" "$repo/tests/fixtures/remo_fixture_provider"
out="$("$bin/remo" providers)"; echo "$out"
grep -qE '^fixture +Fixture +remo-fixture-provider 0\.1\.0 +loaded' <<<"$out" || fail "fixture plugin not loaded"
"$bin/remo" fixture --help | grep -q 'create' || fail "'remo fixture' lacks its generated verbs"
REMO_DISABLE_PROVIDER_PLUGINS=1 "$bin/remo" --help | grep -qE '^\s+fixture\b' && fail "REMO_DISABLE_PROVIDER_PLUGINS did not hide the plugin"

echo "== 3. remo-cli + remo-provider-hetzner wheels"
bin="$(venv hetzner)"
uv pip install -q --python "$bin/python" "$remo_whl" "$hetzner_whl"
out="$("$bin/remo" providers)"; echo "$out"
grep -qE '^hetzner +Hetzner +remo-provider-hetzner [^ ]+ +loaded' <<<"$out" || fail "hetzner plugin not loaded"
for verb in create destroy upgrade resize tag list info sync snapshot; do
  "$bin/remo" hetzner --help | grep -qE "^\s+$verb\b" || fail "'remo hetzner' lacks $verb"
done
"$bin/python" -c 'import hcloud' || fail "the plugin did not bring hcloud"
"$bin/python" - <<'EOF'
from remo_cli.core.config import get_ansible_dir
from remo_provider_hetzner.provider import PLAYBOOK_DIR

core = get_ansible_dir()
for name in ("hetzner_site.yml", "hetzner_configure.yml", "hetzner_provision.yml",
             "hetzner_resize.yml", "hetzner_teardown.yml", "requirements.yml",
             "group_vars/all.yml", "roles/hetzner_server/tasks/main.yml"):
    assert (PLAYBOOK_DIR / name).is_file(), f"{name} missing from the plugin wheel ({PLAYBOOK_DIR})"
assert not list(core.glob("hetzner_*.yml")), f"hetzner playbooks still in remo-cli's wheel ({core})"
print(f"OK: plugin playbooks at {PLAYBOOK_DIR}; remo-cli's at {core}")
EOF
REMO_DISABLE_PROVIDER_PLUGINS=1 "$bin/remo" hetzner --help >/dev/null 2>&1 \
  && fail "REMO_DISABLE_PROVIDER_PLUGINS did not hide hetzner"

echo "== 4. remo-cli + remo-provider-ec2 wheels (#258: the former built-in aws)"
bin="$(venv ec2)"
uv pip install -q --python "$bin/python" "$remo_whl" "$ec2_whl"
out="$("$bin/remo" providers)"; echo "$out"
grep -qE '^ec2 +AWS EC2 +remo-provider-ec2 [^ ]+ +loaded' <<<"$out" || fail "ec2 plugin not loaded"
for verb in create destroy upgrade resize list info sync snapshot stop start reboot; do
  "$bin/remo" ec2 --help | grep -qE "^\s+$verb\b" || fail "'remo ec2' lacks $verb"
done
"$bin/remo" --help | grep -qE '^\s+aws\b' && fail "the deprecated 'remo aws' alias is not hidden"
alias_err="$("$bin/remo" aws list --help 2>&1 >/dev/null)"
grep -q 'deprecated' <<<"$alias_err" || fail "'remo aws' did not warn: $alias_err"
"$bin/python" -c 'import boto3' || fail "the plugin did not bring boto3"
"$bin/python" - <<'EOF'
from remo_cli.core.config import get_ansible_dir
from remo_provider_ec2.provider import PLAYBOOK_DIR

core = get_ansible_dir()
for name in ("aws_site.yml", "aws_configure.yml", "aws_resize.yml", "aws_teardown.yml",
             "requirements.yml", "group_vars/all.yml", "roles/aws_server/tasks/main.yml"):
    assert (PLAYBOOK_DIR / name).is_file(), f"{name} missing from the plugin wheel ({PLAYBOOK_DIR})"
assert not list(core.glob("aws_*.yml")), f"aws playbooks still in remo-cli's wheel ({core})"
assert "amazon.aws" not in (core / "requirements.yml").read_text(), "remo-cli still needs amazon.aws"
print(f"OK: plugin playbooks at {PLAYBOOK_DIR}; remo-cli's at {core}")
EOF
# An existing `aws` registry entry is managed as ec2 and rewritten once.
home="$work/home-ec2"; mkdir -p "$home"
cat >"$home/registry.json" <<'EOF'
{"version": 2, "hosts": [{"type": "aws", "name": "box", "host": "203.0.113.7",
  "user": "remo", "access": "ssm", "aws": {"instance_id": "i-0abc", "region": "us-east-1"}}]}
EOF
REMO_HOME="$home" "$bin/python" - <<'EOF'
import json
import os
import pathlib

from remo_cli.core.known_hosts import get_known_hosts
from remo_cli.core.registry import mutate_registry

(host,) = get_known_hosts(type_filter="ec2")
assert (host.instance_id, host.region) == ("i-0abc", "us-east-1"), host
mutate_registry(lambda hosts: hosts)
doc = json.loads((pathlib.Path(os.environ["REMO_HOME"]) / "registry.json").read_text())
assert [(e["type"], e.get("ec2")) for e in doc["hosts"]] == [
    ("ec2", {"instance_id": "i-0abc", "region": "us-east-1"})
], doc
print("OK: legacy aws registry entry migrated to ec2")
EOF
ec2_mb="$(site_mb "$bin")"
echo "Install size: remo-cli alone ${core_mb} MB; with remo-provider-ec2 ${ec2_mb} MB"

echo "OK: plugin discovery verified on built wheels"
