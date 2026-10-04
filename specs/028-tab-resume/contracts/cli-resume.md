# Contract: `remo resume` CLI and `remo shell` recording

## `remo resume`

```
remo resume [NAME] [-L LOCAL:REMOTE ...] [--no-open] [--no-update-check]
remo resume --forget
remo resume --forget-all
```

| Option | Meaning |
|--------|---------|
| `NAME` | Optional. Resume this tab only if its record is for `NAME`; otherwise behave as `remo shell NAME`. |
| `-L`, `--no-open`, `--no-update-check` | Same semantics as `remo shell` (FR-014). |
| `--forget` | Delete this tab's workstation record. Prints one line; exit 0 even if none existed. No connection. |
| `--forget-all` | Delete every workstation record and regenerate the workstation secret. Exit 0. No connection. |

`--forget`/`--forget-all` are mutually exclusive with each other and with
`NAME`/`-L`; combining them is a usage error (exit 2, Click).

### Behaviour

Follows the decision table in `data-model.md`. Exactly one line is printed
before connecting:

| Outcome | Line (stdout via `core/output`) |
|---------|------|
| attach | `Resuming <project> on <host>` |
| NO_IDENTITY | `This terminal exposes no tab identity — opening remo shell` |
| NO_RECORD | `Nothing recorded for this tab — opening remo shell` / `…on <host> — opening its project menu` |
| NAME_MISMATCH | `This tab last used <recorded>, not <NAME> — opening remo shell <NAME>` |
| HOST_GONE | `<recorded> is no longer registered — opening remo shell` |
| HOST_NOT_UPGRADED | `<host> can't resume a tab's project yet — run '<upgrade command>' — opening its project menu` |
| SESSION_NOT_LIVE | `<project> is no longer running on <host> — opening its project menu` |
| LOOKUP_FAILED | `Couldn't ask <host> which project this tab used — opening its project menu` |

`<upgrade command>` is `upgrade_command_hint(host)` (`remo configure NAME`
for added hosts, `remo <type> upgrade …` for provider hosts) — no type
literal in `core/`.

### Exit codes

Exactly those of `remo shell` for the connection it ends up making (the ssh
exit status propagates as today). `--forget*`: 0. Store write failures never
change the exit code.

## `remo shell` (changed behaviour)

- From a tab with an identity and without `--detach`: record
  `{host: host.name, project: <-p value or null>, recorded_at: now}` and
  forward `REMO_TAB_KEY` on the interactive connection.
- No identity, or `--detach`: no record, no forwarding — byte-identical ssh
  argv to today.
- Never resumes by itself (FR-012a). Output unchanged.
