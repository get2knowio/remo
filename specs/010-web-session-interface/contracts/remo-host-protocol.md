# Contract: `remo-host` Host Command Protocol (v1)

Installed at `~/.local/bin/remo-host` on every instance via the `user_setup` Ansible role. A command,
not a service — listens on no port, runs only when invoked over SSH. JSON commands write **only** the
defined payload to stdout, diagnostics to stderr (FR-012).

## Client compatibility

- Client (`core/remo_host_client.py`) supported major protocol range: **`[1, 1]`** (inclusive).
- A host `protocol_version` within range → compatible; additive fields tolerated within a major.
- Outside range or missing command → typed incompatibility → per-instance update prompt (FR-059).
- Client rejects malformed JSON and payloads over the size cap (default 256 KiB) with actionable errors.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 2 | Usage error (bad/missing flags) |
| 3 | Invalid/nonexistent/rejected project name |
| 4 | Unsupported subcommand |
| 5 | Internal error |

(SSH-layer failures surface as ssh exit 255, distinct from the above — client classifies separately.)

## `remo-host capabilities --json`

Stdout:
```json
{
  "protocol_version": 1,
  "host_tools_version": "2.1.0",
  "projects_root": "/home/remo/projects",
  "operations": ["capabilities", "sessions.list", "sessions.attach"],
  "zellij": true,
  "docker": true
}
```

## `remo-host sessions list --json`

Read-only (FR-010): MUST NOT start containers/sessions, MUST NOT `git fetch` or modify state.

Stdout:
```json
{
  "protocol_version": 1,
  "projects_root": "/home/remo/projects",
  "projects": [
    {
      "name": "my-api",
      "has_devcontainer": true,
      "zellij_state": "active",          // active | exited | absent
      "devcontainer_running": "running"  // running | stopped | unknown
    },
    {
      "name": "notes",
      "has_devcontainer": false,
      "zellij_state": "absent",
      "devcontainer_running": "unknown"
    }
  ]
}
```

Derivation (mirrors existing scripts): projects = `find $PROJECTS_DIR -maxdepth 1 -mindepth 1 -type d`;
`has_devcontainer` = `.devcontainer/` or `.devcontainer.json`; `zellij_state` from ANSI-stripped
`zellij list-sessions`; `devcontainer_running` from `docker ps --filter label=devcontainer.local_folder=$dir`
(`unknown` if docker absent).

## `remo-host sessions attach --project <name>`

Interactive (TTY required). Validates `<name>`:
- reject empty, absolute paths, `..`/traversal, control chars, or names not present under `$PROJECTS_DIR`
  (exit 3 with stderr diagnostic) — **before** any launch (FR-011).
On success, `exec ~/.local/bin/project-launch --project "<name>"` so the resulting Zellij/devcontainer
session is byte-for-byte the CLI's `remo shell -p <name>` path (SC-002). No JSON on this verb; it
becomes an interactive terminal stream.

## Forward compatibility (non-MVP, must not break v1)

Future verbs (`projects clone`, `projects delete`, `sessions stop`) are added as **explicit
subcommands** with their own validation and exit codes. The protocol MUST NOT gain an "arbitrary shell
command" operation (FR-014). Adding verbs bumps `operations[]` and may bump `protocol_version` only on
a breaking change.
