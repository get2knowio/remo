#!/usr/bin/env bash
# Pre-commit / CI gate: catch regressions on the credential-broker contract.
#
# T095: `lookup('env'` in any ansible/ file (must use lookup('pipe', 'fnox get …')).
# T096: `os.environ.get` of known credential names in providers/.
#
# Fails fast with a clear pointer.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail=0

# T095: Ansible env lookups.
# Only secrets are forbidden; non-secret env names (AWS_REGION, AWS_PROFILE,
# USER, REMO_*, TZ, …) are operational hints and allowed.
ansible_hits=$(grep -rn "lookup('env'" ansible/ || true)
ansible_creds=$(echo "$ansible_hits" | grep -v -E "(AWS_REGION|AWS_PROFILE|AWS_DEFAULT_REGION|USER|REMO_[A-Z_]+|TZ|HOSTNAME|LANG|LC_[A-Z_]+|TERM)" | grep -v '^$' || true)
if [[ -n "$ansible_creds" ]]; then
  echo "ERROR (T095): ansible/ still has env lookups for credentials:" >&2
  echo "$ansible_creds" >&2
  echo >&2
  echo "Use lookup('pipe', 'fnox get <name>') instead. See:" >&2
  echo "  specs/005-credential-broker/contracts/ansible-changes.md" >&2
  fail=1
fi

# T096: os.environ.get on forbidden credential names in providers/
forbidden=(
  "AWS_ACCESS_KEY_ID"
  "AWS_SECRET_ACCESS_KEY"
  "NPM_TOKEN"
  "GITHUB_TOKEN"
)
for name in "${forbidden[@]}"; do
  hits=$(grep -rn "os\\.environ\\.get(\"${name}\"\\|os\\.environ\\.get('${name}'" src/remo_cli/providers/ || true)
  if [[ -n "$hits" ]]; then
    echo "ERROR (T096): src/remo_cli/providers/ reads ${name} from env:" >&2
    echo "$hits" >&2
    fail=1
  fi
done

# HETZNER_API_TOKEN: allow one read inside the _get_hetzner_api_token fallback only.
hetzner_hits=$(grep -rn "os\\.environ\\.get(\"HETZNER_API_TOKEN\"\\|os\\.environ\\.get('HETZNER_API_TOKEN'" src/remo_cli/providers/ || true)
hetzner_count=$(echo "$hetzner_hits" | grep -c . || true)
if [[ "$hetzner_count" -gt 1 ]]; then
  echo "ERROR (T096): src/remo_cli/providers/hetzner.py has more than one HETZNER_API_TOKEN env read." >&2
  echo "$hetzner_hits" >&2
  echo "Only the fallback inside _get_hetzner_api_token is permitted." >&2
  fail=1
fi

if [[ "$fail" -ne 0 ]]; then
  exit 1
fi
echo "credential-leak grep gate: ok"
