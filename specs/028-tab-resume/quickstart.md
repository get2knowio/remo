# Quickstart: validating `remo resume`

## Automated (no host needed)

```bash
uv run pytest tests/unit/core/test_tab_identity.py tests/unit/core/test_tab_records.py \
              tests/unit/core/test_resume.py tests/unit/cli/test_resume.py
uv run pytest tests/unit/cli/test_shell.py tests/unit/core/test_ssh.py \
              tests/unit/core/test_remo_host_client.py tests/unit/test_ansible_templates.py
uv run pytest tests/unit/test_architecture.py tests/unit/test_docs_structure.py tests/unit/cli/test_main.py
uv run pytest                                   # full gate
uv run ruff check src/remo_cli && uv run mypy src/remo_cli
```

Expected: all green; the decision table in `data-model.md` is covered row by
row in `test_resume.py`.

## Manual end-to-end (needs a configured host — the real proof)

Prerequisites: remo from this branch on the workstation
(`uv tool install --force git+https://github.com/get2knowio/remo@028-tab-resume`),
a terminal from the recognised list, and a registered host `H` with two
projects `A` and `B`.

1. **Upgrade the host**: `remo configure H` (or `remo <type> upgrade H`).
   Confirm `ssh H 'remo-host capabilities --json'` lists `sessions.lookup`
   and `/etc/ssh/sshd_config.d/accept-tz.conf` contains `REMO_TAB_KEY`.
2. **Two tabs**: in tab 1 run `remo shell H`, pick `A` in the menu; in tab 2
   run `remo shell H`, pick `B`. Start something visible in each (e.g. `top`).
3. **Drop the connections**: close the laptop lid until ssh times out, or
   `pkill -f 'ssh .*H'` on the workstation.
4. **Resume**: run `remo resume` in each tab.
   - Expected: tab 1 prints `Resuming A on H` and shows A's `top`; tab 2 shows
     B's. No prompt in either (SC-001).
5. **Fallbacks** (SC-002):
   - `env -u ITERM_SESSION_ID -u TERM_SESSION_ID … remo resume` (strip the
     tab var) ⇒ `This terminal exposes no tab identity — opening remo shell`.
   - New tab ⇒ `Nothing recorded for this tab`.
   - In tab 1, kill A's session on the host (`zellij kill-session A`), drop,
     resume ⇒ `A is no longer running on H — opening its project menu`, and
     **no** new A session is created.
   - Against a host still on the previous release: resume reaches its menu
     with the `can't resume a tab's project yet — run 'remo configure H'` line.
6. **Housekeeping**: `remo resume --forget` then `remo resume` ⇒ `Nothing
   recorded for this tab`. `remo resume --forget-all` rotates
   `<REMO_HOME>/tab-secret`.
7. **Unchanged paths** (SC-005): the web console attach and `remo shell` with
   no tab variable behave exactly as before; `ssh H 'ls
   ~/.local/state/remo/tabs'` gains no entries from them.
