# Contract: CLI Command Surface (post-021)

The externally visible contract of this feature: the exact per-provider command surface after the
restructure. Verbs not listed here are unchanged from today (FR-011). `[]` = optional, `<>` =
required. Every command exits `0` success / `1` failure / `3` user-aborted via the single
`provider_command` boundary.

## New instance verbs (positional NAME, all providers)

### `remo <type> upgrade NAME`

Runs exactly the in-instance configure play (apt upgrade + dev tools + remo tooling). Zero
provider-side writes; transport reads (container-IP lookup, AWS describe + local registry IP
refresh) permitted.

| Provider | Full synopsis |
|---|---|
| incus | `remo incus upgrade NAME [--host H] [--host-user U] [--only T]... [--skip T]... [-v]` |
| proxmox | `remo proxmox upgrade NAME [--host H] [--node-user U] [--devcontainer-runtime R] [--only T]... [--skip T]... [-v]` |
| aws | `remo aws upgrade NAME [--only T]... [--skip T]... [-v]` |
| hetzner | `remo hetzner upgrade NAME [--only T]... [--skip T]... [-v]` |

Errors: unregistered NAME → `PreconditionError` ("not found … run sync"-style); `type="ssh"`
entry → added-SSH-host guard message; unreachable instance IP → `PreconditionError`; playbook
rc≠0 → `OperationFailedError` (exit 1).

### `remo <type> resize NAME`

Applies only the requested resource change (including any in-guest filesystem grow the
provider's volume resize requires). Never runs the configure play.

| Provider | Dimension flags | Transport flags |
|---|---|---|
| incus | `--volume-size G`, `--cores N`, `--memory MiB` | `[--host H] [--host-user U]` |
| proxmox | `--volume-size G`, `--cores N`, `--memory MiB` | `[--host H] [--node-user U]` |
| aws | `--volume-size G` | — |
| hetzner | `--volume-size G` | — |

All take `[-v]`. Zero dimension flags → exit 1 with a message listing exactly that provider's
dimension flags. `--cores`/`--memory` do not appear in aws/hetzner `--help` (US3 scenario 4).

### `remo <type> tag NAME` — marker-supporting providers only

Writes the managed marker; nothing else. Providers: incus (`incus config set user.remo=true`),
proxmox (`pct set --tags`, appending), hetzner (API label). **`remo aws tag` does not exist** —
Click unknown-command error.

| Provider | Synopsis |
|---|---|
| incus | `remo incus tag NAME [--host H] [--host-user U]` |
| proxmox | `remo proxmox tag NAME [--host H] [--node-user U]` |
| hetzner | `remo hetzner tag NAME` |

Semantics: already tagged → prints already-tagged notice, exit 0, zero writes. Write failure →
`OperationFailedError` with underlying stderr, exit 1 (no warn-and-continue). Proxmox VMID
unresolvable (registry then host-side lookup both fail) → `PreconditionError`, exit 1.

## `host` subgroup (providers with host commands only)

```
remo incus host bootstrap [HOST] [--host-user U] [--network-type T] [-v]     # HOST default: localhost
remo proxmox host bootstrap HOST [--node-user U] [--bridge B] [--storage S] [--template T] [-v]
```

Behavior is today's bootstrap, unchanged. `remo aws --help` / `remo hetzner --help` show no
`host` subgroup. `remo incus host --help` lists only host-plane commands.

## Removed surface (Click unknown-command error, no shim)

- `remo <type> update` — all four providers
- `remo incus bootstrap`, `remo proxmox bootstrap` (flat spellings)
- `--user` on all incus/proxmox verbs → `--host-user` / `--node-user` (Click no-such-option
  error for the old spelling). Applies to: `create`, `destroy`, `info`, `sync`, `upgrade`,
  `resize`, `tag`, `host bootstrap`. (`remo add --user` — the instance login — is unrelated and
  unchanged.)

## Remedy strings (SC-003 — each must be executable and truthful)

| Site | Prints |
|---|---|
| Registry-migration tagging notice (`core/known_hosts.py`) | `remo <type> tag <name>` (+ ` --host <host>` for host-scoped types) |
| `sync` "Mark permanently:" (`core/reconcile.py::render_plan`) | `remo <type> tag <n>` (+ ` --host <h>` for host-scoped types) |
| `remo shell` version-mismatch prompt (`cli/shell.py`) | names `remo <type> upgrade <name>`; accepting runs exactly that operation (`update_entry` → `upgrade`) |

## Unchanged surface (explicit, FR-011)

`create`, `destroy`, `list`, `info`, `sync`, `snapshot create|restore|delete|list`,
`remo aws stop|start|reboot`, `remo shell`, `remo cp`, `remo add`/`remove`, `remo web …`,
`remo completion` — including `create`/`destroy`/`info`'s `--name` addressing and `create`'s
composite provision+configure+best-effort-marker behavior.
