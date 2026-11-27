# Contract: bare `remo shell` over SSH

Applies only when: no `-p`, the access is remote (SSH), and the probe ran and
reported `menu_command=1`.

    ssh <MUX opts> -tt <host opts> [-L…] [-o SendEnv=REMO_TAB_KEY] <target> \
      REMO_MENU=1 "$HOME/.local/bin/project-menu" && exec env REMO_MENU=1 /bin/sh -c 'exec "${SHELL:-/bin/sh}" -l'

(the `${SHELL:-/bin/sh}` fallback runs under `/bin/sh`, single-quoted, because
the login shell — possibly fish — parses the outer command; the `-tt` sits where `build_ssh_base_cmd(tty=True)` puts it today for `-p`).

Otherwise (probe skipped by `--no-update-check`, failed, timed out, or
`menu_command` absent): the argv is byte-identical to today
(`["ssh", *MUX, *opts, target]`, plus SendEnv when a tab key is set).

Local host: unchanged (`[bash, "-c", 'exec "$HOME/.local/bin/project-menu"']`).

## `.bashrc` PROJECT MENU hook (remote role)

Condition becomes:

    if [[ $- == *i* ]] && [[ -n "$SSH_TTY" || -n "$SSH_CONNECTION" ]] && [[ -z "$ZELLIJ" ]] && [[ -z "$REMOTE_CONTAINERS" ]] && [[ -z "$REMO_MENU" ]]; then

Body unchanged.

| Menu exit | Result over SSH |
|---|---|
| 0 ("Exit to shell") | login shell with `REMO_MENU=1` (hook does not re-fire) |
| non-zero (Esc/Ctrl-C/no fzf) | connection ends (as the hook's `exit` does today) |
