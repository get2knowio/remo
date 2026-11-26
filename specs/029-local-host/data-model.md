# Data Model: Local Machine as a Remo Host

## Local host (registry entry)

Stored in `~/.config/remo/registry.json` (format v2, unchanged), one per
machine/user.

| Field | Value | Notes |
|---|---|---|
| `type` | `"local"` | `LOCAL_HOST_TYPE`; pseudo-type, never a registered provider |
| `name` | user-chosen, default `"local"` | validated like any host name; unique across the registry |
| `host` | `"localhost"` | not used for connection |
| `user` | the invoking user | informational |
| `access` | `"direct"` | `ssm` is rejected (not mode-field-aware) |
| `local.projects_dir` | absolute path, optional | serialized only when set; maps to `KnownHost.region`, read through `KnownHost.projects_dir` |

Rules:
- At most one `local` entry; `remo local setup` updates it in place.
- Never included in `remo web push` payloads or `remo web sync` merges; a
  received `local` entry is ignored with a warning (FR-004a).
- An older remo preserves the entry verbatim (unknown-type behaviour).

## Project index (host-side, per host)

File: `~/.local/state/remo/projects.tsv` (mode 0600, directory 0700).

```text
<name>\t<absolute-path>\n
```

| Field | Rule |
|---|---|
| `name` | passes `core/validation.validate_project_name` and its bash twin in `remo-host` (non-empty; no control characters — so never a tab or newline; not absolute; no `/`; no `..`; no leading dot); unique within the file |
| `path` | absolute, normalized (no trailing slash); stored as given, resolved when used |

Effective project list = explicit records ∪ implicit records (each direct,
non-hidden subdirectory of `PROJECTS_DIR`, when `PROJECTS_DIR` is set and
exists).

| Case | Result |
|---|---|
| explicit and implicit share a name | explicit wins; listed with `"shadowed": true` notice |
| explicit path does not exist | listed with `"status": "missing"` and a fix hint; never auto-removed |
| file missing or empty | no explicit projects |
| file malformed (a line without exactly one tab) | error naming the file and line; file never rewritten until fixed |
| a second name for an already-indexed path | `projects add` refused, naming the existing entry |

State transitions per record:

```text
(none) --projects add / projects clone--> present
present --path disappears--> missing --projects add (same name, new path)--> present
present|missing --projects remove--> (none)          [files untouched]
present --projects delete--> (none)                  [checkout deleted, existing behaviour]
```

## Prerequisite (workstation-side result)

| Field | Type | Notes |
|---|---|---|
| `name` | str | `bash`, `git`, `zellij`, `fzf`, `devcontainer-runtime`, `container-engine`, `gh` |
| `required` | bool | `gh` is the only optional one |
| `status` | `present` / `missing` / `not_running` | `not_running` only for the container engine |
| `detail` | str | resolved path / version, or why it failed |
| `install_hint` | str | platform command (Homebrew / apt / dnf / pacman / URL) |

Setup outcome: any required `missing` → fail, register nothing;
otherwise succeed, warning for optional `missing` and `not_running`.

## Host access (runtime, not persisted)

| Implementation | Selected when | Reaches the host via |
|---|---|---|
| SSH | every type except `local` | today's ssh argv, byte-identical |
| Local | `type == "local"` | `<bash≥4> -c <command>` with the user's TTY and env |
