# Remote Coding

An Ansible project for provisioning and configuring a minimal Hetzner Cloud server for remote development.

## Quick Start

```bash
cd ansible

# Install dependencies
pip install ansible hcloud
ansible-galaxy collection install -r requirements.yml

# Set environment variables
export HETZNER_API_TOKEN="your-token"
export DUCKDNS_TOKEN="your-token"
export DUCKDNS_DOMAIN="your-subdomain"

# Run full provisioning
ansible-playbook site.yml
```

See [ansible/README.md](ansible/README.md) for detailed documentation.