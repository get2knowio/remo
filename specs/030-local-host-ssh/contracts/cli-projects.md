# Contract: `remo projects`

    remo projects list HOST
    remo projects add HOST PATH [--name NAME]
    remo projects remove HOST NAME

- HOST resolves like `remo shell HOST` (incl. the `local` host and
  host-scoped short names). Unknown host → `PreconditionError`.
- Transport: `get_host_access(host).command_prefix(host, batch=True)` +
  `run_remo_host_json(prefix, "projects.add|remove|list", …)`.
- `add`: for the local host PATH is `abspath(expanduser(PATH))` client-side
  (as `remo local add`); for any other host PATH is sent verbatim and the host
  expands a leading `~/`.
- Errors: remo-host exit 3 → `PreconditionError(stderr)`; exit 4 / 127, or exit 2
  with "unrecognized subcommand" (a remo-host from before the `projects` verb)
  (verb or remo-host missing) → `PreconditionError("<host>'s host tools are too
  old for project management — run: <upgrade_command_for(host)>")`; others →
  `OperationFailedError`.
- Output: same table/lines as `remo local list|add|remove`, hints naming
  `remo projects add HOST …` for non-local hosts.
- `remo local add|remove|list` unchanged (now thin wrappers).
