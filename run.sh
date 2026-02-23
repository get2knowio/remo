#!/bin/bash
# Wrapper script to run ansible commands
# Usage: ./run.sh [ansible-playbook arguments]
# Example: ./run.sh site.yml

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="$SCRIPT_DIR/ansible"

# Change to ansible directory and run ansible-playbook
cd "$ANSIBLE_DIR"
ansible-playbook "$@"
