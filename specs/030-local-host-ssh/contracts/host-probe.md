# Contract: pre-connect probe, host-info record, capabilities `host_kind`

## Probe command (both transports)

    cat ~/.remo-version 2>/dev/null && printf '\n#remo-host-info\n' && cat ~/.local/state/remo/host-info 2>/dev/null || test -f ~/.remo-version

The remote login shell parses it, so it avoids POSIX-only syntax (`{ …; }`,
`${VAR:-…}`) that fish rejects. Exit status: non-zero only when the marker is
missing.

SSH: `SshAccess.probe_argv(host, PROBE_COMMAND)` (BatchMode, ConnectTimeout=10,
not multiplexed — unchanged); local: `[bash, "-c", PROBE_COMMAND]`. Run with
`capture_output=True, text=True, timeout=15`.

| Outcome | Result |
|---|---|
| timeout | `version=None, error="SSH timed out after 15s"` |
| OSError | `version=None, error="SSH failed: …"` |
| remote and rc 255 | `version=None, error=stderr or "SSH connection failed (exit code 255)"` |
| rc 0, non-empty text before separator | `version=<stripped>`, `info=HostInfo.parse(text after separator line)` |
| anything else | `version=None, error=None, info=HostInfo()` |

A stdout with no separator (e.g. a shim) parses entirely as the version.
`check_remote_version(host)` returns `(probe.version, probe.error)`.

## host-info record

Rendered by `roles/user_setup/tasks/host_scripts.yml` to
`{{ remo_scripts_home }}/.local/state/remo/host-info` (dir created 0700 if
absent; file 0644; owner omitted when `remo_local_install`):

    host_kind={{ 'local' if remo_local_install | default(false) else 'remote' }}
    menu_command=1
    installed_by={{ remo_version | default('') }}

## `remo-host capabilities --json`

Adds `,"host_kind":"<v>"` after `"docker":…` when host-info has a
`host_kind` of `local` or `remote`; omitted otherwise. `protocol_version`
stays 1; all other keys byte-identical.
