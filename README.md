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

## Server Environment

When you SSH into the server, you'll automatically be attached to a [Zellij](https://zellij.dev/) terminal multiplexer session. This provides persistent sessions that survive disconnects.

### Nested Zellij for Devcontainers

The server's Zellij is configured as an "outer" session that only handles **tab management**. This allows you to run a second "inner" Zellij inside devcontainers without keybind conflicts.

**Outer Zellij keybinds (on the host):**
| Keybind | Action |
|---------|--------|
| `Ctrl-t` | New tab |
| `Ctrl-Tab` | Next tab |
| `Ctrl-Shift-Tab` | Previous tab |
| `Ctrl-w` | Close tab |
| `Ctrl-d` | Detach from session |

All other keybinds (pane management, resize, etc.) pass through to the inner Zellij running in your devcontainer.

### Detaching and Resuming Sessions

One of the key benefits of this setup is **persistent sessions**. You can:

1. **Detach** from your session with `Ctrl-d` — your processes keep running on the server
2. **Disconnect** from SSH entirely — the server continues running your workloads
3. **Resume later** — SSH back in and you're automatically reattached to your session

This is especially useful for:
- Long-running builds or tests
- Keeping devcontainers active between coding sessions
- Surviving network interruptions without losing work

When you reconnect, you'll find everything exactly as you left it — open tabs, running processes, and all.

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