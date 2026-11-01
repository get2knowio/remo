#!/usr/bin/env bash
# Prove `remo.providers` entry-point discovery on BUILT wheels, not the source
# tree (#222, Constitution IX: entry points are package metadata).
#
#   scripts/check_plugin_wheels.sh DIST_DIR
#
# DIST_DIR holds remo_cli-*.whl and remo_provider_hetzner-*.whl (from
# `uv build --wheel --all-packages`). Each scenario installs into its own
# fresh venv, so nothing from the source tree or a dev environment leaks in.
# Used by ci.yml (wheel-install) and dev-build.yml.
set -euo pipefail

dist="$(cd "${1:?usage: $0 DIST_DIR}" && pwd)"
repo="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

remo_whl="$(ls "$dist"/remo_cli-*.whl)"
hetzner_whl="$(ls "$dist"/remo_provider_hetzner-*.whl)"

fail() { echo "FAIL: $*" >&2; exit 1; }
venv() { uv venv -q "$work/$1"; echo "$work/$1/bin"; }

echo "== 1. remo-cli alone: three built-ins, no hetzner, hcloud not installed"
bin="$(venv core)"
uv pip install -q --python "$bin/python" "$remo_whl"
out="$("$bin/remo" providers)"; echo "$out"
for t in incus proxmox aws; do grep -qE "^$t +.* builtin +loaded" <<<"$out" || fail "$t not a loaded built-in"; done
grep -q '^hetzner ' <<<"$out" && fail "hetzner present without its plugin"
"$bin/remo" hetzner --help >/dev/null 2>&1 && fail "'remo hetzner' exists without its plugin"
"$bin/python" -c 'import hcloud' 2>/dev/null && fail "hcloud installed by remo-cli alone"
meta="$("$bin/python" -c 'from importlib.metadata import metadata; print("\n".join(metadata("remo-cli").get_all("Requires-Dist") or []))')"
grep -qxE "remo-provider-hetzner; extra == [\"']hetzner[\"']" <<<"$meta" || fail "remo-cli lacks the [hetzner] extra: $meta"
grep -qi 'hcloud' <<<"$meta" && fail "remo-cli still requires hcloud"
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

echo "OK: plugin discovery verified on built wheels"
