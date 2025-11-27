#!/bin/bash
# Wrapper script to source .env file and run ansible commands
# Usage: ./run.sh [ansible-playbook arguments]
# Example: ./run.sh site.yml

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
ANSIBLE_DIR="$SCRIPT_DIR/ansible"

# Source .env file if it exists (only exports valid KEY=VALUE lines)
if [ -f "$ENV_FILE" ]; then
    echo "Loading environment variables from .env file..."
    while IFS='=' read -r key value; do
        # Skip empty lines and comments
        [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
        # Only export if key is a valid variable name
        if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            export "$key=$value"
        fi
    done < "$ENV_FILE"
else
    echo "Warning: .env file not found at $ENV_FILE"
    echo "Copy .env.example to .env and fill in your values"
    echo "Continuing with existing environment variables..."
fi

# Change to ansible directory and run ansible-playbook
cd "$ANSIBLE_DIR"
ansible-playbook "$@"
