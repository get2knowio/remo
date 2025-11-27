# Remote Coding

An Ansible project for provisioning and configuring a minimal Hetzner Cloud server for remote development.

## Quick Start

```bash
# Install dependencies
cd ansible
pip install ansible hcloud
ansible-galaxy collection install -r requirements.yml
cd ..

# Set up environment variables
cp .env.example .env
# Edit .env with your actual values

# Run full provisioning
./run.sh site.yml
```

See [ansible/README.md](ansible/README.md) for detailed documentation.