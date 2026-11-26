# Contract: Host access (SSH and local)

Module: `src/remo_cli/core/host_access.py` (core layer; no Click, no provider
names). The one place that decides *how* remo reaches a host's scripts.

## Interface

```python
class HostAccess(Protocol):
    def interactive_argv(
        self, host: KnownHost, remote_cmd: str | None, *, tty: bool,
        extra_opts: Sequence[str] = (),
    ) -> list[str]: ...
    def command_prefix(self, host: KnownHost, *, batch: bool) -> list[str]: ...
    def env(self, host: KnownHost, *, tab_key: str | None) -> dict[str, str]: ...
    def supports_tunnels(self) -> bool: ...

def get_host_access(host: KnownHost) -> HostAccess: ...
```

- `interactive_argv(host, None, …)` opens the project menu (SSH: bare login,
  whose host-side `.bashrc` starts `project-menu`; local: runs `project-menu`).
- `interactive_argv(host, cmd, …)` runs a host-side command string such as the
  output of `build_project_launch_remote_cmd` (unchanged, host-agnostic).
- `command_prefix(host, batch=True)` is what `remo_host_client.run_remo_host_json`
  receives as `ssh_argv_prefix`: the prefix plus the remo-host words must form
  one shell command on the host.
- `env(host, tab_key=…)` returns the subprocess environment; `REMO_TAB_KEY` is
  present iff `tab_key` is not None.

## SSH implementation

Byte-identical to pre-029 behaviour:

| Call | Produces |
|---|---|
| `interactive_argv(h, None, tty=False, extra_opts=E)` | `build_ssh_base_cmd(h, tty=False, multiplex=True, extra_opts=E)` |
| `interactive_argv(h, cmd, tty=T, extra_opts=E)` | the same + `[cmd]` |
| `command_prefix(h, batch=True)` | today's `resume._ssh_prefix(h)` (`-o BatchMode=yes` inserted) |
| `env(h, tab_key=K)` | `{**os.environ, "REMO_TAB_KEY": K}` (and the caller adds `-o SendEnv=REMO_TAB_KEY` to `extra_opts`, as today) |
| `supports_tunnels()` | `True` |

## Local implementation

| Call | Produces |
|---|---|
| `interactive_argv(h, None, tty=…)` | `[BASH, "-c", 'exec "$HOME/.local/bin/project-menu"']` |
| `interactive_argv(h, cmd, tty=…)` | `[BASH, "-c", cmd]` (cmd's `~/.local/bin/project-launch …` expands under bash) |
| `command_prefix(h, batch=…)` | `[BASH, "-c", 'eval "$*"', "remo-local"]` — bash receives the remaining argv as `$1…$n`; `"$*"` joins them with single spaces exactly as `ssh` joins its command words, and `eval` runs the result as one shell command |
| `env(h, tab_key=K)` | `{**os.environ, "PATH": f"{HOME}/.local/bin:{PATH}", "REMO_TAB_KEY": K}` (key omitted when None) |
| `supports_tunnels()` | `False` |

`BASH` = the bash ≥ 4 recorded by `remo local setup` (read from the installed
scripts' environment file `~/.local/state/remo/local.env`); if absent, the
access layer raises `PreconditionError("local setup has not completed — run
remo local setup")`.

The joined-word rule is load-bearing: `remo_host_client` passes
`REMOTE_PATH_PREFIX` (`PATH="$HOME/.local/bin:$PATH"`) as a separate word that
only works when the words are joined into one shell command. The local runner
must join exactly like `ssh` does (single space, no extra quoting) so the same
argv tail behaves identically.

## Consumers

| Consumer | Before | After |
|---|---|---|
| `core/ssh.shell_connect` | builds ssh argv | `get_host_access(h).interactive_argv(…)` + `.env(…)`; `-L` with `supports_tunnels() is False` → `PreconditionError` |
| `core/ssh.check_remote_version` | ssh `cat ~/.remo-version` | `command_prefix(h, batch=True) + ["cat ~/.remo-version 2>/dev/null"]`; exit 255 only meaningful for SSH |
| `core/resume._ssh_prefix` | ssh prefix | `command_prefix(h, batch=True)` |
| `cli/shell.connect_to_host` upgrade | provider `update_entry` / `added.configure` | + `local` → `providers.local.setup(assume_yes=True)` |
| `cli/cp.py` | ssh opts | `local` → `PreconditionError` |
| `core/attach.py`, `web/*` | ssh | unchanged (SSH-only; `local` never reaches the web service — FR-004a) |

## Errors

- Local, scripts missing (`~/.local/bin/project-menu` absent):
  `PreconditionError("remo local setup has not completed on this machine — run: remo local setup")`.
- Local, `-L` given: `PreconditionError("port tunnels do not apply to the local host: its ports are already on this machine")`.
