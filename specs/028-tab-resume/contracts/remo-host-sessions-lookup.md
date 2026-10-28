# Contract: `remo-host sessions record` / `sessions lookup` (protocol v1, additive)

Extends `specs/010-web-session-interface/contracts/remo-host-protocol.md`.
`PROTOCOL_VERSION` stays `1`; the change is additive under that contract's
forward-compatibility rule.

## Capabilities

`operations[]` gains `"sessions.lookup"`. `sessions record` is internal (used
only by remo's own host scripts) and is **not** advertised.

## `remo-host sessions record --key KEY --project NAME`

- `KEY` MUST match `^[0-9a-f]{32}$`; otherwise exit 3, nothing written.
- `NAME` MUST pass `validate_project_name` (existing directory under
  `projects_root`, no traversal); otherwise exit 3.
- Writes `${REMO_HOST_TABS_DIR:-$HOME/.local/state/remo/tabs}/KEY` with one
  line `NAME<TAB><epoch seconds>`, via a temp file in the same directory and
  `mv` (atomic). Creates the directory `0700`.
- After writing: delete files in that directory older than 30 days, then
  delete all but the newest 500.
- Output: none. Exit 0 on success. Missing/duplicate flags: exit 2.

## `remo-host sessions lookup --key KEY --json`

- `KEY` invalid ⇒ exit 3. `--json` required ⇒ else exit 2.
- Reads the record file for `KEY` (a missing or unparseable file is "no
  record").
- stdout, single line:

```json
{"protocol_version":1,"key":"<KEY>","project":"<NAME>"|null,"recorded_at":<int>|null,"zellij_state":"active"|"exited"|"absent"|null}
```

- `key` echoes the requested `KEY`; a client MUST treat an answer whose
  `key` differs (or is missing) as a malformed response — a failed lookup,
  never a resume target (SC-003).
- `zellij_state` uses exactly the definition `sessions list` uses (live =
  listed and not EXITED, in the pinned `XDG_RUNTIME_DIR` namespace); `null`
  iff `project` is `null`. A recorded project whose directory no longer
  exists is reported with `zellij_state` from zellij only (the client then
  attaches only if `active`).

## Old hosts

A host without this change answers `sessions lookup` with exit 4
(`unsupported subcommand`); a host with no `remo-host` at all makes the remote
shell exit 127. Clients MUST treat exit 4 (and exit 2 and 127) as "not
upgraded", not as an error.

## Callers

`project-menu` (`launch_session`, before `zellij attach --create`) and
`project-launch` (before the final `exec zellij attach --create`) run, only
when `REMO_TAB_KEY` is non-empty:

```bash
"$HOME/.local/bin/remo-host" sessions record --key "$REMO_TAB_KEY" --project "<name>" >/dev/null 2>&1 || true
```

The `--detach` and non-zellij branches of `project-launch` do not record.
