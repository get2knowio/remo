# Quickstart: Credential Broker (end-to-end manual test)

Date: 2026-05-25
Branch: 005-credential-broker

A representative pass-through covering all four providers + the two operational commands (`rotate-bootstrap`, `audit`). Reviewers can use this as the smoke-test recipe; the implementation tasks (Phase 2) will codify portions as automated tests where feasible.

Assumes a developer laptop with `fnox` installed and a chosen backend (1Password Service Account in the examples below).

## 0. One-time laptop setup

```bash
# Install fnox per https://github.com/jdx/fnox
fnox --version

# Store provisioning + admin SA credentials in fnox (one-time per backend identity)
fnox set hetzner_api_token            # paste Hetzner API token
fnox set aws_access_key_id            # paste AWS access key
fnox set aws_secret_access_key
fnox set incus_workstation_01_admin_sa # paste 1Password SA admin token for this dev's instances on workstation-01

# Configure Remo's backend choice
remo init --backend 1password
# Expected: refuses if fnox missing; refuses interactive identity types; writes laptop-side fnox config
```

Expected SC after this section: zero secret material in `~/.bashrc`, `~/.zshrc`, or any `env` output.

## 1. Register a self-hosted node (Incus example)

```bash
remo incus add-node workstation-01 \
  --host 192.168.4.10 \
  --ssh-user incusadmin \
  --admin-sa-fnox-key incus_workstation_01_admin_sa

# Expected:
# - SSH to 192.168.4.10 as incusadmin
# - Installs /usr/local/libexec/remo-broker-tokens helper (idempotent)
# - Creates /var/lib/remo-broker/instance-tokens/$USER/
# - Writes ~/.config/remo/nodes.yml (mode 0600)
```

Re-run the same command → expect "already registered" and exit `0`.

## 2. Create instances on three provider transports

```bash
# AWS — instance profile, no token on disk
remo aws create awsdev-1

# Hetzner — SSH push
remo hetzner create hetz-1

# Incus on the registered node — bind-mount
remo incus create lxc-1 --node workstation-01
```

For each, expect the new Ansible roles to run: `broker_install` (download + verify + systemd unit), then `bootstrap_token_{imds|file|mount}` (state assertion).

## 3. Verify isolation properties (User Story 1)

For each instance:

```bash
# 3a. Outside any devcontainer — no project credentials present
ssh <instance> 'env | grep -iE "token|aws|github|npm"'        # expect empty
ssh <instance> 'ls ~/.aws ~/.config/gh ~/.npmrc 2>/dev/null'  # expect empty / nonexistent

# 3b. Hetzner-specific: no token in cloud-init user-data
hcloud server describe hetz-1 -o json | jq '.user_data'       # expect null or no token

# 3c. AWS-specific: no on-disk token, IMDS reachable
ssh awsdev-1 'curl -fs http://169.254.169.254/latest/meta-data/iam/security-credentials/ | head -1'
ssh awsdev-1 'test ! -e /etc/remo-broker/bootstrap-token && echo OK'
```

## 4. Create a project, enter the devcontainer, verify the socket

On any of the instances:

```bash
remo shell <instance>
# Picks a project. If none exist, create one for the test:
mkdir -p ~/projects/test-broker && cd ~/projects/test-broker
echo '{"name":"test"}' > package.json   # triggers Node devcontainer (research R5)

# Re-pick from menu → Remo auto-synthesizes .remo/broker.toml + .remo/devcontainer.json
# Lands in a devcontainer with /run/remo-broker/sock mounted

# Inside the devcontainer:
ls /run/remo-broker/sock                          # expect socket file
echo 'GET github_token' | nc -U /run/remo-broker/sock   # expect token value (US1 AS#1)
echo 'GET npm_token'    | nc -U /run/remo-broker/sock   # expect "deny: not in manifest" (US1 AS#3)
```

## 5. Multi-device access (User Story 2)

From a different laptop where the developer has authenticated to the backend:

```bash
remo shell <instance>
# Expect immediate access, no re-bootstrapping prompt.
```

Reboot the instance via provider console; verify broker comes back up:

```bash
ssh <instance> 'systemctl is-active remo-broker.service'  # expect "active"
```

## 6. Inspect audit log (`remo audit`)

```bash
remo audit <instance> --tail 50
# Expect a table with the GET requests from step 4: one allow (github_token), one deny (npm_token).

remo audit <instance> --json | jq 'select(.decision=="deny")'
# Expect one deny line matching the npm_token attempt.
```

## 7. Rotate the bootstrap token (`remo rotate-bootstrap`)

```bash
remo rotate-bootstrap <instance>
# Expect:
# - Fresh sub-token minted via backend admin SA
# - Token delivered via provider transport
# - Broker reloads (visible via journalctl -u remo-broker)
# - Previous sub-token revoked at backend

# Verify continued operation:
remo audit <instance> --tail 5   # broker still serving
```

Idempotency check:

```bash
remo rotate-bootstrap <instance>   # immediate second call
# Expect: "Skipped — last rotation was less than 1 hour ago. Use --force to override."
```

## 8. Destroy with revocation (User Story 5)

```bash
# Stash a copy of the bootstrap token before destroy (for revocation verification — Hetzner only):
ssh hetz-1 'sudo cat /etc/remo-broker/bootstrap-token' > /tmp/leaked-token

remo destroy hetz-1
# Expect log line: "Revoking bootstrap token at backend... OK"
# Then the normal Hetzner deletion proceeds.

# Within 60 seconds (SC-005), the stashed token MUST be rejected:
sleep 65
fnox --backend-token "$(cat /tmp/leaked-token)" get any_secret   # expect auth failure
rm /tmp/leaked-token
```

## 9. Negative test: project without manifest gets minimal default (User Story 4)

```bash
ssh <instance> 'mkdir -p ~/projects/no-manifest && cd ~/projects/no-manifest && git init'
remo shell <instance>   # pick no-manifest
# Expect Remo to write .remo/broker.toml with [mcp] secrets = ["github_token"]
# and .remo/devcontainer.json with base image mcr.microsoft.com/devcontainers/base:ubuntu-24.04
ssh <instance> 'cat ~/projects/no-manifest/.remo/broker.toml'
```

## 10. Negative test: exit-to-instance-shell warning (User Story 6 AS#3)

From the project menu, pick "exit to instance shell".

Expected:
- One-time warning explaining the broker is not available outside a devcontainer.
- Drops to plain SSH shell on the instance OS.
- `printenv | grep -iE "token|secret"` returns nothing.

## Success criteria mapped

| Check | SC |
|---|---|
| Step 3 (a, c) | SC-001 (zero secrets at rest on instance) |
| Step 4 (deny line) | SC-002 (only allowlisted secrets served) |
| Step 5 (second-device) | SC-003 (multi-device, no per-device reconfig) |
| Provisioning step 2 wall-clock | SC-004 (<30 s added) |
| Step 8 sleep+check | SC-005 (token rejected within 60 s of destroy) |
| Step 4 → add npm_token → restart → re-check | SC-006 (manifest changes effective on next restart) |
