# Remote Coding

An Ansible project for provisioning and configuring a minimal Hetzner Cloud server for remote development.

## Getting Started

### 1. Fork This Repository

Fork this repository to your own GitHub account to customize it for your needs.

### 2. Configure Repository Secrets

In your forked repository, go to **Settings → Secrets and variables → Actions** and add the following secrets:

| Secret | Description |
|--------|-------------|
| `HETZNER_API_TOKEN` | Your Hetzner Cloud API token |
| `SSH_PRIVATE_KEY` | Your SSH private key for server access |
| `SSH_PUBLIC_KEY` | Your SSH public key to be added to the server |
| `DUCKDNS_TOKEN` | Your DuckDNS token for dynamic DNS |
| `DUCKDNS_DOMAIN` | Your DuckDNS subdomain (without `.duckdns.org`) |

### 3. Use GitHub Actions to Manage Your Server

- **Provision**: Go to **Actions → Provision Server → Run workflow**, type `yes` to confirm
- **Teardown**: Go to **Actions → Teardown Server → Run workflow**, type `yes` to confirm

The teardown action preserves your data volume, so you only pay for storage when the server is off.

## Cost Optimization with Scheduled Actions

You can schedule the provision and teardown workflows to run automatically at specific times to optimize costs. For example, provision your server at the start of your workday and tear it down at the end.

To add scheduling, edit the workflow files in `.github/workflows/` and add a `schedule` trigger:

```yaml
on:
  schedule:
    # Provision at 8 AM UTC on weekdays
    - cron: '0 8 * * 1-5'
  workflow_dispatch:
    # ... existing manual trigger
```

```yaml
on:
  schedule:
    # Teardown at 6 PM UTC on weekdays
    - cron: '0 18 * * 1-5'
  workflow_dispatch:
    # ... existing manual trigger
```

> **Note**: Scheduled runs will need to bypass the confirmation input. You may want to add a condition like `if: github.event_name == 'schedule' || github.event.inputs.confirm == 'yes'` to allow scheduled runs.

## Local Development

For running playbooks locally instead of via GitHub Actions:

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