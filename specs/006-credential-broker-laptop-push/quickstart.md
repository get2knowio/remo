# Quickstart: Credential Broker (Sidecar Devcontainer Model)

**Audience**: Reviewer or implementer validating the end-to-end sidecar flow on a real remo instance.

## Prereqs

```bash
cd /workspaces/remote-coding
uv sync --all-extras
uv run remo --version
```

Use a sibling `remo-broker` checkout at `/workspaces/remo-broker` when validating cross-repo wire/schema behavior.

Pick one provider (`aws`, `hetzner`, `incus`, or `proxmox`) and one test project with a `.devcontainer/` configuration.

## 1. Provision or reconcile an instance

```bash
uv run remo <provider> create <instance>
# or, for an existing box:
uv run remo <provider> update <instance>
```

**Expected**
- The host has a running `remo-broker` service.
- The project picker contains `_remo-vault`.
- No new laptop-side commands were required.

## 2. Open the sidecar and store credentials

```bash
uv run remo shell -p _remo-vault
```

Inside `_remo-vault`:

```bash
gh auth login --web
fnox set gh
fnox set aws
remo-list-creds
remo-vend-status
exit
```

**Expected**
- `remo-list-creds` shows names/metadata only, never plaintext values.
- `remo-vend-status` shows broker protocol v2, `secret_count`, and `secrets_loaded_at`.

## 3. Create or update the project manifest outside the project devcontainer

From the host shell or inside `_remo-vault`, edit:

```text
~/projects/<project>/.remo/manifest.toml
```

Example:

```toml
schema_version = 1
project = "<project>"

[secrets.gh]
fetch_as = "env"
env_var = "GH_TOKEN"

[secrets.aws]
fetch_as = "file"
file_path = "~/.aws/credentials"
file_mode = "0600"
template = """
[default]
aws_access_key_id={{aws_access_key_id}}
aws_secret_access_key={{aws_secret_access_key}}
"""
```

Reload it:

```bash
remo-reload <project>
```

## 4. Start the project devcontainer

```bash
uv run remo shell -p <project>
```

Inside the project devcontainer, verify:

```bash
printf '%s\n' "$GH_TOKEN" | head -c 4
stat ~/.aws/credentials
mount | grep tmpfs | grep .aws
```

**Expected**
- `GH_TOKEN` is present in the environment.
- `~/.aws/credentials` exists with mode `0600`.
- The rendered credentials file lives on tmpfs, not persistent disk.
- The checked-in devcontainer config stays unchanged because Remo injects the manifest/socket feature through a generated `devcontainer --config` wrapper.

Optional detached validation:

```bash
uv run remo shell -p <project> --detach --exec 'env | grep GH_TOKEN'
uv run remo shell -p <project> --exec 'tail -n 20 ~/.local/state/remo/<project>.log'
```

**Expected**
- Detached commands run through the same secret-vending wrapper as the interactive shell.
- The remote log contains command output, not secret material.

## 5. Verify the fail-closed missing-secret path

Edit the manifest to reference a secret that is not present in `_remo-vault`, then reload:

```toml
[secrets.missing_demo]
fetch_as = "env"
env_var = "MISSING_DEMO"
```

```bash
remo-reload <project>
uv run remo shell -p <project>
```

**Expected**
- Startup retries for about 15 seconds.
- The project startup exits non-zero without handing off to the user's normal shell.
- The error clearly names the missing secret.
- A broker protocol mismatch fails immediately instead of retrying for the full window.

## 6. Verify no useful credentials exist at provisioning rest

On a freshly provisioned project devcontainer before you manually run login tools there:

```bash
find / -type f \( -name '*.env' -o -name 'credentials*' -o -name '.netrc' -o -path '*/.aws/*' -o -path '*/.config/gh/*' \) 2>/dev/null
```

**Expected**
- No useful credential files are present from remo provisioning alone.

## 7. Verify broker audit output

On the host:

```bash
sudo tail -n 20 /var/log/remo-broker/audit.log
```

**Expected**
- `SecretsPushed` appears after sidecar credential updates.
- Counts are logged, but plaintext values are not.
- Failed `remo-test-project` runs point you back at the same audit log path.

## 8. Cleanup

```bash
uv run remo <provider> destroy <instance>
```

**Expected**
- The entire instance, including `_remo-vault`, is torn down in one flow.
- No separate sidecar cleanup command is needed.
