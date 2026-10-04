# Data Model: `remo resume` (028-tab-resume)

## TabIdentity (workstation, in memory only)

| Field | Type | Rule |
|-------|------|------|
| `variable` | str | One of `TMUX_PANE`, `WEZTERM_PANE`, `KITTY_WINDOW_ID`, `ITERM_SESSION_ID`, `TERM_SESSION_ID`, `WT_SESSION` — first non-empty in that order |
| `value` | str | Raw value; for tmux `"<socket>:<pane>"` (socket = `$TMUX` field 1) |
| `identity` | str | `f"{variable}={value}"` — never persisted, never sent |

Absent when no variable is set (FR-003).

## WorkstationSecret

- File `<REMO_HOME>/tab-secret`, mode `0600`, 64 hex chars (32 random bytes).
- Created on first need; replaced by `--forget-all`.

## TabKey

- `HMAC-SHA256(secret_bytes, identity.encode()).hexdigest()[:32]`.
- Shape `^[0-9a-f]{32}$` — the only tab value that leaves the workstation,
  forwarded as `REMO_TAB_KEY`.

## WorkstationTabRecord — `<REMO_HOME>/tab-records.json`

```json
{
  "version": 1,
  "records": {
    "<tab key>": {
      "host": "<KnownHost.name>",
      "project": "<name> | null",
      "recorded_at": "2026-10-04T12:00:00+00:00"
    }
  }
}
```

- Written by every non-`--detach` `remo shell` from a tab with an identity
  (`project` set only for `-p`). Overwrites the tab's previous record.
- Refreshed by `remo resume` when it attaches (project := the attached one) or
  opens the menu (project kept), so a tab that only ever resumes is not pruned.
  Best-effort and silent.
- Prune on write: drop `recorded_at` older than 30 days, then keep newest 500.
- Unknown `version`, unparseable JSON, or wrong shapes ⇒ treated as empty and
  rewritten on the next record.
- Locked by `<REMO_HOME>/tab-records.lock` (`fcntl.flock`), written by temp
  file + `os.replace`.

## HostTabRecord — `$HOME/.local/state/remo/tabs/<tab key>` on each host

- Override dir: `REMO_HOST_TABS_DIR` (tests).
- Content: one line `<project>\t<epoch seconds>`.
- Written by `remo-host sessions record` (called from `project-menu` /
  `project-launch` when `REMO_TAB_KEY` is set). Key must match
  `^[0-9a-f]{32}$`; project must pass `validate_project_name`. Otherwise
  exit 3, nothing written.
- Prune on write: files older than 30 days deleted, then newest 500 kept.

## ResumeLookup (host → client, `remo-host sessions lookup --key K --json`)

| Field | Type | Notes |
|-------|------|-------|
| `protocol_version` | int | `1` |
| `key` | str | Echo of the requested key |
| `project` | str \| null | null when no record |
| `recorded_at` | int \| null | epoch seconds |
| `zellij_state` | `"active"` \| `"exited"` \| `"absent"` \| null | null when no record; same definition as `sessions list` |

Client model: `TabLookup` (frozen dataclass) in `core/remo_host_client.py`.

## ResumeDecision (client, `core/resume.py`)

| Field | Type | Notes |
|-------|------|-------|
| `action` | `"attach"` \| `"menu"` \| `"shell"` | attach = `-p project`; menu = plain connect to the recorded host; shell = plain `remo shell [NAME]` flow (picker if needed) |
| `host_name` | str \| None | target for attach/menu |
| `project` | str \| None | for attach |
| `reason` | `ResumeReason` \| None | the fallback reason, None on full success |

`ResumeReason` (enum): `NO_IDENTITY`, `NO_RECORD`, `HOST_GONE`,
`NAME_MISMATCH`, `HOST_NOT_UPGRADED`, `SESSION_NOT_LIVE`, `LOOKUP_FAILED`.

### Decision table

| # | Condition (evaluated in order) | Action | Reason |
|---|------|--------|--------|
| 1 | no identity | shell(NAME) | NO_IDENTITY |
| 2 | no workstation record | shell(NAME) | NO_RECORD |
| 3 | NAME given and ≠ record.host | shell(NAME) | NAME_MISMATCH |
| 4 | record.host not registered | shell(NAME) | HOST_GONE |
| 5 | lookup returned an entry, state active | attach(entry.project) | — |
| 6 | lookup returned an entry, state not active | menu | SESSION_NOT_LIVE |
| 7 | lookup unsupported / no entry / failed, and record.project set, sessions list says active | attach(record.project) | — (success line only; FR-013 allows exactly one line) |
| 8 | as 7 but record.project not active | menu | SESSION_NOT_LIVE |
| 9 | lookup unsupported, no record.project | menu | HOST_NOT_UPGRADED |
| 10 | lookup failed, no record.project | menu | LOOKUP_FAILED |
| 11 | lookup supported, no entry, no record.project | menu | NO_RECORD (host has none for this tab) |

Rows 7–8: if `sessions list` itself fails, treat the project as not active
(row 8) — never attach blind, never create a session.
