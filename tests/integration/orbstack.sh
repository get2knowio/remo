#!/bin/bash
# OrbStack VM test harness for remo incus.
# Creates VMs, bootstraps Incus, and provisions containers for local testing.
#
# Usage:
#   ./tests/integration/orbstack.sh              # Create all VMs
#   ./tests/integration/orbstack.sh ubuntu       # Create only the ubuntu VM
#   ./tests/integration/orbstack.sh opensuse     # Create only the opensuse VM
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH_KEY_FILE="${SSH_KEY_FILE:-$HOME/.ssh/id_rsa.pub}"
SSH_USER="sysadm"
CONTAINER_NAME="dev1"

# Each line: vm-name distro[:version]
ALL_VMS=(
  "ubuntu ubuntu:24.04"
  "opensuse opensuse"
)

# --- Colors ---------------------------------------------------------------
blue()  { printf '\033[0;34m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }

# --- Preflight -------------------------------------------------------------
if ! command -v orb &>/dev/null; then
  red "OrbStack CLI (orb) not found. Install OrbStack first."
  exit 1
fi
if ! command -v remo &>/dev/null; then
  red "remo CLI not found. Install with: uv tool install --editable ."
  exit 1
fi
if ! command -v expect &>/dev/null; then
  red "expect not found. Install with: brew install expect"
  exit 1
fi
if [[ ! -f "$SSH_KEY_FILE" ]]; then
  red "SSH public key not found: $SSH_KEY_FILE"
  red "Set SSH_KEY_FILE to override."
  exit 1
fi

# --- Build cloud-init with local SSH key -----------------------------------
CLOUD_INIT=$(mktemp /tmp/remo-cloud-init.XXXXXX.yml)
trap 'rm -f "$CLOUD_INIT"' EXIT

sed "s|__SSH_PUBKEY__|$(cat "$SSH_KEY_FILE")|" \
  "$SCRIPT_DIR/orbstack-cloud-init.yml" > "$CLOUD_INIT"

# --- Filter VMs if name given on CLI ---------------------------------------
if [[ $# -gt 0 ]]; then
  target="$1"
  VMS=()
  for spec in "${ALL_VMS[@]}"; do
    vmname="${spec%% *}"
    if [[ "$vmname" == "$target" ]]; then
      VMS+=("$spec")
    fi
  done
  if [[ ${#VMS[@]} -eq 0 ]]; then
    red "Unknown VM: $target"
    red "Available: $(printf '%s ' "${ALL_VMS[@]}" | sed 's/ [^ ]*//g')"
    exit 1
  fi
else
  VMS=("${ALL_VMS[@]}")
fi

# --- Shell smoke tests (expect) -------------------------------------------
test_shell() {
  local instance="$1"

  blue "Disabling project-menu auto-start for testing..."
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    "$SSH_USER@$host" \
    "sudo incus exec $CONTAINER_NAME -- su -c \"sed -i '/# BEGIN ANSIBLE MANAGED BLOCK - PROJECT MENU/,/# END ANSIBLE MANAGED BLOCK - PROJECT MENU/d' ~/.bashrc\" remo"

  blue "Test: remo shell basic access ($instance)..."
  expect <<EXPECT
set timeout 30
spawn remo shell "$instance"
expect {
  "\\$ " {}
  timeout { puts "FAIL: timed out waiting for prompt"; exit 1 }
}
send "whoami\r"
expect {
  "remo" {}
  timeout { puts "FAIL: whoami did not return remo"; exit 1 }
}
expect "\\$ "
send "hostname\r"
expect {
  -re ".+" {}
  timeout { puts "FAIL: hostname returned nothing"; exit 1 }
}
expect "\\$ "
send "exit\r"
expect eof
puts "remo shell basic access: PASS"
EXPECT

  blue "Test: remo shell environment ($instance)..."
  expect <<EXPECT
set timeout 30
spawn remo shell "$instance"
expect {
  "\\$ " {}
  timeout { puts "FAIL: timed out waiting for prompt"; exit 1 }
}
send "echo TERM=\\\$TERM\r"
expect {
  -re "TERM=.+" {}
  timeout { puts "FAIL: TERM not set"; exit 1 }
}
expect "\\$ "
send "docker --version\r"
expect {
  -re "Docker version .+" {}
  timeout { puts "FAIL: docker not available"; exit 1 }
}
expect "\\$ "
send "exit\r"
expect eof
puts "remo shell environment: PASS"
EXPECT
}

# --- Main loop -------------------------------------------------------------
for spec in "${VMS[@]}"; do
  vmname="${spec%% *}"
  distro="${spec##* }"
  host="${vmname}.orb.local"

  blue "=== $vmname ($distro) ==="

  # Remove existing VM if present
  if orb list 2>/dev/null | grep -q "^${vmname} "; then
    blue "Removing existing VM: $vmname"
    orb delete "$vmname" -f
  fi

  # Create VM with cloud-init
  blue "Creating VM: $vmname"
  orb create "$distro" "$vmname" -c "$CLOUD_INIT"

  # Wait for cloud-init to finish (installs sshd + creates user)
  blue "Waiting for cloud-init..."
  orb run -m "$vmname" cloud-init status --wait 2>/dev/null || true

  # Clear stale host key, accept new one
  ssh-keygen -R "$host" 2>/dev/null || true

  # Verify SSH works
  blue "Testing SSH to $SSH_USER@$host..."
  if ! ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30 \
       "$SSH_USER@$host" echo "connection-ok"; then
    red "SSH failed for $vmname — skipping"
    continue
  fi

  # Bootstrap Incus
  blue "Bootstrapping Incus on $vmname..."
  remo incus bootstrap --host "$host" --user "$SSH_USER"

  # Create container
  blue "Creating container '$CONTAINER_NAME' on $vmname..."
  remo incus create --host "$host" --user "$SSH_USER" --name "$CONTAINER_NAME"

  # Smoke-test remo shell
  blue "Running shell smoke tests..."
  test_shell "${host}/${CONTAINER_NAME}"

  green "=== $vmname: done ==="
done

green "All done."
