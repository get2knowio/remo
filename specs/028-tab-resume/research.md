# Research: `remo resume` (028-tab-resume)

Every open technical choice, resolved unattended with the most defensible
option. Code anchors were verified against `main` @ `18d0e13` (2026-10-04).

## R1 — Where the logic lives (Constitution I)

- **Decision**: Resume is not a provider operation, so its business logic goes
  in `core/`, split three ways, plus a thin `cli/resume.py`:
  - `core/tab_identity.py` — pure: read the tab identity from an environment
    mapping, derive the tab key. No I/O except through injected values.
  - `core/tab_records.py` — the workstation store (records + secret): load,
    record, forget, forget-all, prune, atomic + locked writes.
  - `core/resume.py` — the pure decision function (`decide_resume`) plus the
    host lookup wrapper with the 5 s budget.
  - `cli/resume.py` — Click wiring; reuses `remo shell`'s connect flow.
- **Rationale**: Matches the three-layer rule — `core/` holds shared,
  provider-agnostic utilities; the decision function is pure and table-testable.
  Nothing here varies by provider, so no descriptor field is needed (II).
- **Alternatives**: one `core/resume.py` module (rejected: mixes a pure
  decision table with filesystem state and env parsing, harder to test);
  putting it in `providers/` (rejected: not a provider concern).

## R2 — Sharing `remo shell`'s connect flow

- **Decision**: Extract the body of `cli/shell.py::shell` after host
  resolution (AWS auto-start, version check / upgrade offer, `shell_connect`)
  into a public `connect_to_host(host, *, tunnels, no_open, no_update_check,
  project, exec_cmd, detach, tab_key=None)` function in `cli/shell.py`, pinned first by the
  existing `tests/unit/cli/test_shell.py` suite plus characterization tests
  for anything it does not cover. `cli/resume.py` calls it (cli → cli is
  allowed). `_upgrade_command_hint` is promoted to the public
  `upgrade_command_hint` in the same module for FR-013's host-needs-upgrade
  line.
- **Rationale**: FR-014 requires the *same* pre-connect checks; one code path
  guarantees it. The upgrade hint is the most correct of the three existing
  copies (it handles host-scoped `--host`/`--host-user`) and has no provider
  literals beyond the `ssh` pseudo-type.
- **Alternatives**: duplicate the flow in `cli/resume.py` (rejected: drift);
  move the hint to `core/known_hosts.py` (deferred: consolidating the three
  copies is a separate cleanup — filed as a follow-up issue, not done here).

## R3 — Tab identity and precedence

- **Decision**: First set of `TMUX_PANE`, `WEZTERM_PANE`, `KITTY_WINDOW_ID`,
  `ITERM_SESSION_ID`, `TERM_SESSION_ID`, `WT_SESSION`. The identity string is
  `"<VAR>=<value>"`; for tmux it is `"TMUX_PANE=<socket>:<pane>"` where
  `<socket>` is the first comma-separated field of `$TMUX` (the server pid,
  field 2, is excluded). Empty values count as unset.
- **Rationale**: Innermost multiplexer first (what the user perceives as "this
  tab"); the variable name in the identity prevents cross-terminal collisions;
  tmux `%N` ids repeat across servers, the socket path does not.

## R4 — Tab key derivation

- **Decision**: `key = HMAC-SHA256(secret, identity).hexdigest()[:32]` —
  32 lowercase hex characters. `secret` is 32 random bytes from
  `secrets.token_bytes`, stored hex-encoded in `<REMO_HOME>/tab-secret`,
  created `0600` on first use.
- **Rationale**: One-way (FR-002), workstation-unique, and the fixed
  `^[0-9a-f]{32}$` shape is the host's entire input validation (FR-008) —
  safe as a filename with no escaping. 128 bits is ample for a namespace of
  ≤500 entries per host.
- **Alternatives**: plain SHA-256 of identity (rejected: two laptops' "kitty
  window 1" collide); UUID per tab stored by the client (rejected: needs
  somewhere to keep it per tab — that is exactly what the env var already is).

## R5 — Workstation storage

- **Decision**: `<REMO_HOME>/tab-records.json` (`{"version": 1, "records":
  {key: {"host": name, "project": str|null, "recorded_at": ISO-8601 UTC}}}`),
  `<REMO_HOME>/tab-secret`, and a `<REMO_HOME>/tab-records.lock` sidecar.
  Writes: `fcntl.flock` on the sidecar (bounded wait, as `registry_lock`
  does), write to a temp file in the same dir, `os.replace`. Prune on every
  write: drop entries older than 30 days, then keep the newest 500. A missing,
  unreadable, or malformed records file reads as empty (never an error); a
  write failure is reported as one warning line and never blocks the
  connection.
- **Rationale**: `REMO_HOME` already resolves `$REMO_HOME` →
  `$XDG_CONFIG_HOME/remo` → `~/.config/remo` (`core/config.py:36-56`), so
  overrides and tests work unchanged. Same atomic/lock pattern as
  `core/registry.py:581-591, 715-756`, kept private here because those helpers
  are registry-specific; there is no shared atomic-JSON helper yet (five
  private copies exist). Consolidation is a follow-up issue, not this
  feature's scope.
- **Alternatives**: XDG state dir (`~/.local/state/remo`) — more "correct" for
  ephemeral state but introduces a second root that `REMO_HOME` does not
  govern; rejected for consistency. Storing in `registry.json` — rejected:
  the registry is a versioned schema for hosts; tab records are not hosts.

## R6 — Forwarding the key to the host

- **Decision**: `shell_connect` gains `tab_key: str | None`. When set, it adds
  `-o SendEnv=REMO_TAB_KEY` to the ssh options for that connection and runs
  ssh with `env={**os.environ, "REMO_TAB_KEY": key}` (no global `os.environ`
  mutation). The host side: the existing sshd drop-in written by
  `ansible/tasks/configure_dev_tools.yml:32-39`
  (`/etc/ssh/sshd_config.d/accept-tz.conf`) becomes `AcceptEnv TZ
  REMO_TAB_KEY`; the content change triggers the existing restart task
  (`:41-45`) once, and a second configure is a no-op (VII). The file name is
  kept so hosts do not accumulate a stale second drop-in.
- **Rationale**: Mirrors the TZ precedent (`core/ssh.py:186-189`). A host
  without the `AcceptEnv` line silently drops the variable (FR-005). Every
  configure/site playbook includes `configure_dev_tools.yml`, so one change
  reaches every provider and added host (FR-006, FR-019). OpenSSH multiplexed
  sessions forward the *mux client's* `SendEnv` variables per session, so
  `ControlMaster=auto` does not break this.
- **Alternatives**: put the key in the remote command (rejected: the no-`-p`
  path has no remote command); `SetEnv` (rejected: needs OpenSSH ≥ 7.8 on the
  client and still needs `AcceptEnv`).

## R7 — Host-side recording

- **Decision**: New internal `remo-host sessions record --key K --project P`
  (not advertised in `operations[]`; it is called only by remo's own scripts).
  `project-menu`'s `launch_session` (just before `zellij attach --create`) and
  `project-launch` (just before the final `exec zellij attach --create`) call
  `"$HOME/.local/bin/remo-host" sessions record ... >/dev/null 2>&1 || true`
  **only when `REMO_TAB_KEY` is non-empty**. The detach and no-zellij branches
  of `project-launch` record nothing. Store: one file per key in
  `${REMO_HOST_TABS_DIR:-$HOME/.local/state/remo/tabs}/<key>` containing
  `<project>\t<epoch>`, written via `mktemp` in the same dir + `mv`. On each
  record: `find -mtime +30 -delete`, then keep the newest 500 files.
- **Rationale**: One implementation of validation (key regex + the existing
  `validate_project_name`), atomicity and pruning, instead of copies in two
  scripts. File-per-key makes concurrent writers from several tabs trivially
  safe (FR-017). `|| true` because both scripts run under `set -e`. Web
  console / SSM attaches never carry the variable, so they record nothing
  (FR-007).

## R8 — Host lookup and the "not upgraded" signal

- **Decision**: New advertised operation `sessions.lookup`: `remo-host
  sessions lookup --key K --json` →
  `{"protocol_version":1,"key":K,"project":P|null,"recorded_at":epoch|null,
  "zellij_state":"active"|"exited"|"absent"|null}`. The liveness logic is
  factored out of `cmd_sessions_list` so both use one definition. The client
  calls it **directly** (no capabilities round-trip): exit 4 (`unsupported
  subcommand`) or exit 2 from an old host ⇒ "host not upgraded". Protocol
  version stays 1 (additive op, per the forward-compatibility rule in
  `specs/010-web-session-interface/contracts/remo-host-protocol.md`).
- **Rationale**: SC-004 allows at most one extra round-trip over `remo shell
  -p`; a capabilities probe would make it two. `operations[]` still
  advertises `sessions.lookup` for other consumers (the console).
- **Alternatives**: bump protocol to 2 (rejected: purely additive change).

## R9 — Bounding the lookup (FR-015, SC-006)

- **Decision**: `run_remo_host_json(..., timeout=5.0)` with the ssh prefix
  from `build_ssh_base_cmd(host, multiplex=True)` plus `-o BatchMode=yes`
  (mirroring `core/attach.py`). Every `RemoHostClientError` subclass and
  `subprocess.TimeoutExpired` (surfaced as `SshTransportError`) degrades to
  the "no host entry" path. The interactive connection that follows reuses the
  ControlMaster the lookup opened. (amended: #247) An `SshTransportError`
  additionally skips the rows 7–8 `sessions list` liveness call, so an
  unreachable host costs one 5 s budget rather than two.

## R10 — Resolving the recorded host

- **Decision**: Records store `KnownHost.name` (the registry's canonical
  name). Resume checks existence with an exact-name scan of
  `get_known_hosts()` **before** resolving, because
  `resolve_remo_host_by_name` raises `SystemExit` on a miss
  (`core/known_hosts.py:285-310`) and FR-012 requires a fallback, not an exit.

## R11 — What `remo shell` records

- **Decision**: Every `remo shell` invocation from a tab with an identity
  records `{host, project (if -p), now}` and forwards the key — except
  `--detach`, which is not an interactive attach. Recording happens after host
  resolution and before connecting, so a failed connection still leaves the
  right host recorded (harmless).

## R12 — Messages (FR-013)

- **Decision**: One `core/output` info/warn line per fallback, fixed wording
  defined once in `core/resume.py` as an enum of reasons:
  `NO_IDENTITY`, `NO_RECORD`, `HOST_GONE`, `NAME_MISMATCH`,
  `HOST_NOT_UPGRADED` (appends the exact upgrade command from
  `upgrade_command_hint`), `SESSION_NOT_LIVE` (names the project),
  `LOOKUP_FAILED`. Success prints one line naming host and project.

## R13 — Testing approach (Constitution VI)

- Pure units: identity precedence/tmux composite/empty values; key shape and
  secret dependence; the full `decide_resume` decision table (every reason).
- Store: round-trip, forget, forget-all rotates the secret, prune by age and
  count, malformed file reads empty, 0600 secret, concurrent writers
  (multiprocessing) never corrupt the file.
- Client: `lookup_tab` argv, parse, exit 4/2 ⇒ unsupported, timeout ⇒ error.
- Host scripts, executed (`tests/unit/test_ansible_templates.py` harness:
  rendered template + fake `zellij`): `sessions record` valid/invalid keys
  (traversal, uppercase, short, empty), invalid project, prune; `sessions
  lookup` hit/miss/exited/absent; `capabilities` lists `sessions.lookup`.
  `project-launch` executed with a fake `zellij` and fake `remo-host`: records
  only with `REMO_TAB_KEY` set, never in `--detach`.
- CLI: `remo resume` wiring and each fallback; `remo shell` records +
  forwards; `--detach` does not; `EXPECTED_COMMANDS` gains `resume`.
- Ansible: `configure_dev_tools.yml` drop-in content includes `REMO_TAB_KEY`.
