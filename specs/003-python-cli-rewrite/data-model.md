# Data Model: Python CLI Rewrite

**Feature Branch**: `003-python-cli-rewrite`
**Date**: 2026-02-28

## Entities

### KnownHost

Represents a registered development environment in the local registry.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| type | string (enum) | yes | Provider type: `incus`, `hetzner`, `aws` |
| name | string | yes | Environment name. For incus: `host/container`. For aws/hetzner: server name |
| host | string | yes | IP address or hostname for SSH connection |
| user | string | yes | SSH user (typically `remo`) |
| instance_id | string | no | AWS EC2 instance ID (e.g., `i-0abc123`) |
| access_mode | string (enum) | no | `ssm` for AWS SSM proxy, absent for direct SSH |
| region | string | no | AWS region (e.g., `us-west-2`) |

**Serialization format** (preserved from bash for backward compatibility):
```
TYPE:NAME:HOST:USER[:INSTANCE_ID[:ACCESS_MODE[:REGION]]]
```

**Identity**: A host is uniquely identified by `(type, name)`.

**Examples**:
```
incus:myhost/devcontainer:192.168.1.50:remo
aws:devbox:3.14.15.92:remo:i-0abc123def:ssm:us-west-2
hetzner:webserver:5.6.7.8:remo
```

### VersionCache

Tracks the latest available version to avoid repeated GitHub API calls.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| version | string | yes | Latest version from GitHub (semver, e.g., `v0.7.4`) |
| checked_at | timestamp | yes | When the check was performed |

**Storage**: `$REMO_HOME/latest_version_cache` — plain text, 24-hour TTL.

**Lifecycle**: Created/updated on first command invocation when cache is missing or stale. Read on every command invocation for passive update hints.

## Relationships

```
KnownHost *──── 1 Provider (by type field)
Provider  ────── * Playbook (ansible/ directory, by convention)
```

- A `KnownHost` belongs to exactly one provider (determined by `type`).
- Each provider maps to a fixed set of Ansible playbooks (e.g., `incus` → `incus_site.yml`, `incus_teardown.yml`).
- The mapping from provider to playbook paths is static configuration in the provider modules, not stored in the registry.

## State Transitions

### KnownHost Lifecycle

```
(not registered) ──create/sync──> Registered ──destroy──> (not registered)
                                      │
                                      ├──update──> Registered (same entry, tools updated)
                                      └──sync──> Registered (host/IP may change)
```

### AWS Instance States (external, queried via boto3)

```
pending → running → stopping → stopped → pending (start) → running
                  → shutting-down → terminated
running → rebooting → running
```

The CLI queries and displays these states but does not manage them in the local registry. The `auto-start` feature transitions `stopped → running` before SSH connection.

## Validation Rules

| Field | Rule |
|-------|------|
| name | Must match `^[a-zA-Z0-9][a-zA-Z0-9._/-]*$`, max 63 chars |
| host | Valid IPv4 address or hostname |
| user | Non-empty string |
| port | Integer 1-65535 |
| region | Must match `^[a-z]{2}-[a-z]+-[0-9]+$` (AWS region format) |
| tool names | Must be one of: `docker`, `nodejs`, `zellij`, `fzf`, `github_cli`, `devcontainers` |
