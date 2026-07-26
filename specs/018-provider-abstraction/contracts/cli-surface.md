# Contract: Generated CLI Surface

**Producer**: `cli/providers/factory.py` from registered descriptors. Replaces the four hand-written modules. Stable surfaces: command/flag names, semantics, exit codes. Help/table *formatting* may change (FR-025).

## Shared command set (every provider)

| Command | Shared options (canonical catalog) | Notes |
|---------|-----------------------------------|-------|
| `create` | `--name` (descriptor default, shown in help) · `--volume-size` · `--only/--skip` · `-v/--verbose` · + descriptor `create_options` | `--yes/-y` accepted this release with deprecation notice, then removed (FR-010) |
| `destroy` | `NAME` arg/`--name` · `--yes/-y` (uniform semantics, FR-012) · `-v` · + descriptor `destroy_options` (e.g. Proxmox `--purge`) | Runs shared destroy template |
| `update` | `--name` · `--volume-size` · `--only/--skip` · `-v` · + descriptor `update_options` | |
| `list` | — | Shared table renderer, descriptor columns |
| `info` | `NAME`/`--name` · + descriptor `info_options` (incus/proxmox: `--host` `--user`) | |
| `sync` | `--yes` · `--dry-run` · + descriptor `sync_options` (incus: `--host` `--user` `--use-ip`; aws: `--region` `--all`; hetzner: `--all`; proxmox: `--host` `--user`) | Semantics unchanged (FR-020); `--all` only where Spec-016 defined it |
| `snapshot create/restore/delete/list` | `INSTANCE` · `SNAPSHOT_NAME` where applicable · `--region` iff `snapshot_region_scoped` | Entry-resolved once by factory; providers get `KnownHost` |

Uniformity rules (SC-002, verified by automated help/behavior comparison):

- A shared option is the *same* `OptionSpec` object everywhere → identical spelling, short form, metavar, help text.
- Completion for instance-name params generated from `name_format` (strips `host/` for HOST_SCOPED).
- Every callback wrapped by `provider_command` (single exit-code boundary).
- No command advertises a no-op flag outside a declared deprecation window.

## Per-provider declared differences (descriptor data, today's surface preserved)

| Provider | create extras | update extras | destroy extras | extra commands | default name |
|----------|--------------|---------------|----------------|----------------|--------------|
| incus | `--host` `--user` `--domain` `--image` `--cores` `--memory` `--use-ip` | `--host` `--user` `--cores` `--memory` | — | `bootstrap` | `dev1` |
| proxmox | `--host`(req) `--user` `--node` `--bridge` `--storage` `--template` `--cores` `--memory` `--unprivileged/--privileged` `--domain` `--use-ip` `--devcontainer-runtime` | `--host` `--user` `--cores` `--memory` `--devcontainer-runtime` | `--purge` | `bootstrap` | `dev1` |
| aws | `--type` `--region` `--spot` `--iam-profile` | — | — | `stop` `start` `reboot` (via error contract; `stop`/`reboot` confirmable) | login user |
| hetzner | `--type` `--location` | — | — | — | `remo` |

Verified against today's code: AWS `--region` exists on `create`, `sync`, and the snapshot subcommands only — **not** on `update`/`destroy`. `info` is a shared command on all four providers (AWS's is an extra-options-free instance of the shared command, not an extra command). **The T002 captured baseline (`tests/unit/cli/surface_baseline.py`) is authoritative over this prose matrix** — any discrepancy found while writing descriptors is resolved in favor of the baseline, and this file is corrected to match.

## Mounting & startup (FR-024 / SC-008)

- `cli/main.py`: `for d in all_descriptors(): cli.add_command(build_provider_group(d))` — replaces four explicit imports.
- Building all groups imports **no** provider implementation modules and no optional SDKs; test asserts `boto3`/`hcloud` ∉ `sys.modules` after full CLI construction and `--help`.

## Deprecations this release

| Surface | Behavior | Removal |
|---------|----------|---------|
| `create --yes/-y` (all four) | Accepted; prints deprecation notice; no effect | Next release |
