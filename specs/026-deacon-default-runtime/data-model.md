# Data Model: Make deacon the Default Devcontainer Runtime

## E1. Runtime value (`devcontainer_runtime`)

`auto` (default) | `deacon` | `devcontainer`. Sources, highest first: `--devcontainer-runtime` flag → `REMO_DEVCONTAINER_RUNTIME` → built-in default `auto`. Validated by `click.Choice` (flag) and `resolve_devcontainer_runtime` (env). Passed to Ansible as `-e devcontainer_runtime=<value>` by the proxmox provider; other providers/playbooks fall back to the Ansible default `auto`.

## E2. Effective runtime (`devcontainer_runtime_effective`) and source

`deacon` | `devcontainer`, plus `devcontainer_runtime_source` ∈ {`explicit`, `marker`, `legacy`, `nested-overlayfs`, `default`}. Computed once per play by `ansible/tasks/resolve_devcontainer_runtime.yml` (contracts/runtime-resolution.md). Consumers: role selection, `devcontainer_cli_bin`, `devcontainer_up_extra_args`, templates, `remo-host`, the warning task.

## E3. Runtime marker

Path `/home/{{ remo_user }}/.remo-devcontainer-runtime`; content exactly `deacon\n` or `devcontainer\n`; `0644`, owned by `remo_user`. Read by the resolver (trimmed; any other content = absent); written after `user_setup` when `configure_devcontainers` is on; content-compare idempotent.

## E4. Capability advertisement

`remo-host capabilities --json` → `operations` includes `projects.rebuild` iff `rebuild_supported` (case list on `DEVCONTAINER_BIN`: `devcontainer|deacon` → true). `projects rebuild` exits 4 with the existing message when unsupported. Protocol version 1 unchanged.

## E5. Pin

`deacon_version: "0.4.0"`; download `https://github.com/get2knowio/deacon/releases/download/v0.4.0/deacon-v0.4.0-{x86_64|aarch64}-unknown-linux-musl.tar.gz`; installed-version regex `[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?`, exact equality.

## State transitions (per host)

```
fresh host ──configure(auto)──▶ effective=deacon (default) or devcontainer (nested-overlayfs) → marker written
legacy host (reference CLI, no marker) ──configure(auto)──▶ effective=devcontainer (legacy) → marker written
any host with marker ──configure/upgrade(auto)──▶ effective=marker (no change)
any host ──configure(--devcontainer-runtime X)──▶ effective=X (explicit) → marker rewritten; docs: stop projects, rebuild fresh
```
