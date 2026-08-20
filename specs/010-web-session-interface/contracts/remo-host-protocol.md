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
| 2 | Usage error (bad/missing flags) — also **unrecognized top-level verb** |
| 3 | Invalid/nonexistent/rejected project, repo, or job id |
| 4 | Unsupported **sub**-verb under a recognized group (`sessions stop`, `host bogus`, …) |
| 5 | Internal error / operation failure (e.g. `projects delete` could not remove the directory) |

The 2-vs-4 split matters for rollout: a host with **older** tools answers a whole new verb group
(`host …`, `projects …`, `jobs …`) with exit **2** (unrecognized top-level verb), while an
**up-to-date** host answers an unknown sub-verb with exit **4**. Clients must gate new verbs on
`capabilities.operations[]` and treat exit 2 *and* 4 on a new verb as "host tools outdated".

(SSH-layer failures surface as ssh exit 255, distinct from the above — client classifies separately.)

## Environment overrides (test-only)

The script reads three environment variables so the template tests can point it at fixture trees;
production hosts never set them:

| Variable | Default | Used by |
|---|---|---|
| `REMO_HOST_PROC_ROOT` | `/proc` | `host stats` |
| `REMO_HOST_SYS_ROOT` | `/sys` | `host stats` (hwmon/thermal) |
| `REMO_HOST_JOBS_DIR` | `~/.local/state/remo/jobs` | detached jobs (`projects clone`/`rebuild`, `jobs status`) |

## `remo-host capabilities --json`

Stdout:
```json
{
  "protocol_version": 1,
  "host_tools_version": "2.1.0",
  "projects_root": "/home/remo/projects",
  "operations": ["capabilities", "sessions.list", "sessions.attach", "host.stats",
                 "projects.clone", "projects.delete", "projects.rebuild", "jobs.status"],
  "zellij": true,
  "docker": true
}
```

`operations[]` is the feature-detection surface: clients MUST check it before invoking a verb.
`projects.rebuild` is advertised **only when the host was provisioned with the reference
`devcontainer` CLI** — deacon lacks `--remove-existing-container`, so a deacon host omits the
operation and answers `projects rebuild` with exit 4. All other operations are unconditional.

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
      "devcontainer_running": "running", // running | stopped | unknown
      "git_tracked": true,               // is a git work tree
      "git_dirty": true,                 // uncommitted changes present
      "git_ahead": 2,                    // commits ahead of upstream (0 if none)
      "git_behind": 0                    // commits behind upstream (0 if none)
    },
    {
      "name": "notes",
      "has_devcontainer": false,
      "zellij_state": "absent",
      "devcontainer_running": "unknown",
      "git_tracked": false,
      "git_dirty": false,
      "git_ahead": 0,
      "git_behind": 0
    }
  ]
}
```

Derivation (mirrors existing scripts): projects = `find $PROJECTS_DIR -maxdepth 1 -mindepth 1 -type d`;
`has_devcontainer` = `.devcontainer/` or `.devcontainer.json`; `zellij_state` from ANSI-stripped
`zellij list-sessions`; `devcontainer_running` from `docker ps --filter label=devcontainer.local_folder=$dir`
(`unknown` if docker absent).

Git status is **read-only** (FR-010): `git_tracked` from `git rev-parse --is-inside-work-tree`,
`git_dirty` from a non-empty `git status --porcelain`, and `git_ahead`/`git_behind` from
`git rev-list --left-right --count @{upstream}...HEAD`. `remo-host` **never runs `git fetch`**, so
`git_ahead`/`git_behind` reflect the last-known upstream and may be stale. The `git_*` keys are an
additive, backward-compatible addition to protocol version 1: an older host omits them and the
client defaults each to `false`/`0` (no git glyphs shown), so no version bump is required.

## `remo-host sessions attach --project <name>`

Interactive (TTY required). Validates `<name>`:
- reject empty, absolute paths, `..`/traversal, control chars, leading-dot (hidden) names, or names
  not present under `$PROJECTS_DIR` (exit 3 with stderr diagnostic) — **before** any launch (FR-011).
  Hidden names are reserved for the host's own clone-staging dirs and never enumerated.
On success, `exec ~/.local/bin/project-launch --project "<name>"` so the resulting Zellij/devcontainer
session is byte-for-byte the CLI's `remo shell -p <name>` path (SC-002). No JSON on this verb; it
becomes an interactive terminal stream.

## `remo-host host stats --json`

Read-only live snapshot of host resource usage — pure coreutils/awk, no jq. `cpu_used_pct` is
derived from two `/proc/stat` samples 0.25s apart, so the verb takes ~0.3s. Memory values are
converted from `/proc/meminfo` kB to **bytes**.

Stdout:
```json
{
  "protocol_version": 1,
  "uptime_s": 12345,
  "load_1": 0.52, "load_5": 0.58, "load_15": 0.59,
  "cpu_count": 8,
  "cpu_used_pct": 12.5,
  "mem_total": 16777216000, "mem_used": 8388608000, "mem_available": 8388608000,
  "swap_total": 2097152000, "swap_used": 1048576000,
  "disks": [
    {"mount": "/", "size_bytes": 1000, "used_bytes": 400, "avail_bytes": 600}
  ],
  "temps": [
    {"name": "k10temp", "label": "Tctl", "temp_c": 45.5}
  ]
}
```

Derivation: `uptime_s` from `/proc/uptime`; `load_*` from `/proc/loadavg`; `cpu_count` via `nproc`;
`disks[]` from `df -B1 -P` on the projects root and `/`, **deduped by mount point** (frequently the
same filesystem); `temps[]` from `/sys/class/hwmon/*/temp*_input` (millidegrees ÷ 1000, hwmon `name`
plus per-sensor `label` when present, `""` otherwise), capped at 16 readings, falling back to
`/sys/class/thermal/thermal_zone*/temp` (zone `type` as `name`) when hwmon yields nothing, and `[]`
when neither interface exists (typical VM/container — clients hide the temperatures card). Every
source is guarded: a missing interface degrades that field to its zero value, never an error. On an
LXC guest without lxcfs the values are whatever the guest kernel reports (i.e. host-wide).

## Detached jobs (`projects clone`, `projects rebuild` → `jobs status`)

Clone and rebuild can run for many minutes, so both return **immediately** with a job reference
(202-style) and run detached via `nohup setsid` (the `project-launch --detach` idiom) — the job
survives the SSH connection that started it.

Job reference (stdout of the starting verb):
```json
{"protocol_version": 1, "job_id": "clone-20260820135558-3d7f2364", "kind": "clone", "project": "widget"}
```

Job state lives in flat files under `$REMO_HOST_JOBS_DIR` (default `~/.local/state/remo/jobs`):

| File | Written | Content |
|---|---|---|
| `<job_id>.json` | at start | metadata: `job_id`, `kind`, `project`, `started_at`, `pid` |
| `<job_id>.log` | during | the command's combined stdout+stderr |
| `<job_id>.exit` | on completion | the command's exit code |

`job_id` is unique and shell-safe: `<kind>-<timestamp>-<random hex>`. Job files older than 24h are
pruned on each job start.

### `remo-host jobs status --job ID --json`

Stdout:
```json
{
  "protocol_version": 1,
  "job_id": "clone-20260820135558-3d7f2364",
  "state": "succeeded",
  "exit_code": 0,
  "started_at": "2026-08-20T13:55:58+00:00",
  "finished_at": "2026-08-20T13:55:58+00:00",
  "log_tail": "Cloning into 'widget'...\n"
}
```

`state` is `running | succeeded | failed`: from the `.exit` file when present (`0` → succeeded, else
failed), otherwise `kill -0 <pid>` — a dead pid with **no** `.exit` file means the job was
interrupted (reboot, kill) and reports `failed` with `exit_code: null`. `finished_at` is the `.exit`
file's mtime (`null` while running/interrupted). `log_tail` is the last 8192 bytes of the `.log`,
JSON-escaped with unrepresentable control characters (ANSI escapes) stripped. Unknown job id → exit 3.

## `remo-host projects clone --repo VALUE [--name NAME] --json`

Registers a detached clone job and prints the job reference. `--repo` MUST be either `OWNER/REPO`
shorthand or an `https://github.com/OWNER/REPO(.git)` URL, each path segment matching
`[A-Za-z0-9_.-]+` and **not** starting with `-` (option-injection defense, FR-014); `.`/`..`
segments are rejected. Anything else — ssh URLs, other hosts, shell metacharacters — is exit 3.
`--name` defaults to the repo basename minus `.git`; it must match the same safe charset, start
with an alphanumeric or `_` (never `-` — option injection — nor `.` — hidden names are reserved for
staging, so a repo named `.github` needs an explicit `--name`), and **must not already exist** under
the projects root (exit 3).

The job prefers `gh repo clone` when `gh` exists **and** `gh auth status` succeeds (private repos
need a prior `gh auth login` on the host), falling back to anonymous `git clone -- <url>` (`--`
guards argv even though the shape check already forbids option-lookalikes). It clones into a hidden
staging dir **inside the projects root** (`.remo-clone.XXXXXXXX` — same filesystem, so the final
rename is atomic and never a cross-device copy that could expose a partial project or exhaust a
tmpfs `/tmp`) and renames the result into place; hidden dirs are excluded from `sessions list`, so a
half-finished clone is never visible.

## `remo-host projects delete --project NAME --json`

Synchronous. Validates NAME exactly like `sessions attach` (exit 3 on unknown/unsafe names), then:
kills and deletes the project's zellij session (when zellij exists), force-removes containers
matching `label=devcontainer.local_folder=<project dir>` (when docker exists — images are kept as
build cache), and removes the project directory. Directory-removal failure → exit 5.

Stdout: `{"protocol_version": 1, "deleted": "NAME"}`

## `remo-host projects rebuild --project NAME [--no-cache] --json`

Registers a detached rebuild job and prints the job reference. Exit 3 when the project has no
`.devcontainer` directory / `.devcontainer.json` file; exit 4 on deacon hosts (see `operations[]`).
The job kills the project's zellij session, then runs
`devcontainer up --workspace-folder <dir> --remove-existing-container` (plus `--build-no-cache`
with `--no-cache`). The rebuild inherits the nested-overlayfs shim (docs/nested-overlayfs.md) for
free via PATH.

## Forward compatibility (non-MVP, must not break v1)

Future verbs (e.g. `sessions stop`) are added as **explicit subcommands** with their own validation
and exit codes — as `host stats`, `projects clone/delete/rebuild`, and `jobs status` were: additive
within protocol version 1, feature-detected via `operations[]`. The protocol MUST NOT gain an
"arbitrary shell command" operation (FR-014). Adding verbs bumps `operations[]` and may bump
`protocol_version` only on a breaking change.
