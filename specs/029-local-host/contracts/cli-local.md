# Contract: `remo local` command group

Wiring: `src/remo_cli/cli/local.py` (Click only). Logic:
`src/remo_cli/providers/local.py` (no Click, no `sys.exit`; raises
`core/errors.py`). Exit codes follow the existing boundary: 0 success,
1 failure, 3 user-aborted.

## `remo local setup [--name NAME] [--projects-dir DIR] [--yes] [-v]`

1. Detect prerequisites (see `check`). Any required one missing → print the
   table, exit 1, change nothing.
2. Run `ansible/local_setup.yml` (local connection, become false,
   `ansible_python_interpreter=<sys.executable>`), installing remo's scripts to
   `~/.local/bin`, the version marker `~/.remo-version`, state dirs under
   `~/.local/state/remo/`, and `~/.local/state/remo/local.env`
   (`REMO_BASH=<path>`).
3. Create or update the single `local` registry entry (default name `local`;
   `--name` renames an existing one; `--projects-dir` sets/clears
   `projects_dir`; `--projects-dir ""` clears it).
4. Print what changed (or "Already up to date" on a no-op run).

Guarantees: no sudo/become, no user creation, no edits to `~/.bashrc`,
`~/.zshrc`, `~/.profile` or any other rc file; idempotent.

Errors: name collides with a non-local entry → `PreconditionError` naming it;
playbook failure → `OperationFailedError` with the play's output path.

## `remo local check`

Read-only. Prints one row per prerequisite:

```text
TOOL                 STATUS        DETAIL / INSTALL
bash >= 4            present       /opt/homebrew/bin/bash (5.2.37)
git                  present       /usr/bin/git
zellij               missing       brew install zellij
fzf                  present       /opt/homebrew/bin/fzf
devcontainer runtime present       deacon (/opt/homebrew/bin/deacon)
container engine     not running   installed (docker) but not running — start Docker Desktop / OrbStack / colima start
gh (optional)        present       /opt/homebrew/bin/gh

Local host: local (setup complete, scripts 5.0.0)
```

Exit 0 when every required tool is present (warnings allowed), else 1.

## `remo local add PATH [--name NAME]`

Runs `remo-host projects add` through host access. Prints
`Added 'a' → /Users/me/code/a`. Errors from remo-host (exit 3) surface as
`PreconditionError` with remo-host's message. Requires local setup to have run.

## `remo local remove NAME`

Runs `remo-host projects remove`. Prints `Removed 'a' (files untouched)`.

## `remo local list`

Runs `remo-host projects list --json` and renders a table:

```text
NAME   PATH                 SOURCE  STATUS
a      /Users/me/code/a     added   ok
b      /Users/me/work/b     added   missing — re-add: remo local add <new-path> --name b
```

## Interactions with existing commands

| Command | Against the local host |
|---|---|
| `remo shell [local]` | opens `project-menu` locally (no SSH) |
| `remo shell local -p N [--exec C] [--detach]` | runs `project-launch` locally |
| `remo resume [local]` | lookup + attach locally |
| `remo shell local -L …` | refused: ports are already local |
| `remo cp … local:…` | refused: copy files directly |
| `remo configure local` | refused: use `remo local setup` |
| `remo add local …` | refused if `local` is already the local host's name (existing name-conflict check) |
| `remo remove local` | removes the local host entry (scripts and index stay; re-run setup to restore) |
| `remo web push` / `remo web sync` | the local host is never sent or accepted |
