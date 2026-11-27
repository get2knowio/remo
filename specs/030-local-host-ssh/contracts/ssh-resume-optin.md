# Contract: `remo local setup --allow-ssh-resume / --no-allow-ssh-resume`

Tri-state option, default: SSH configuration is not read or touched.
Applied after the playbook and registration succeed.

| State / input | Behaviour |
|---|---|
| unsupported (no readable `/etc/ssh/sshd_config` with an `Include` covering `/etc/ssh/sshd_config.d/*`) | warn + print the drop-in path and content to add by hand; setup exit 0 |
| supported config, but no sshd found (`/usr/sbin/sshd`, then `shutil.which("sshd")`, then `/usr/local/sbin/sshd`, `/opt/homebrew/sbin/sshd`) | warn naming the places searched + print the same manual steps as the unsupported case; no sudo; setup exit 0 (#285) |
| enable, drop-in already has exact content | "Resume over SSH already enabled"; no sudo |
| disable, drop-in absent | "Resume over SSH already disabled"; no sudo |
| change needed, non-interactive (`not stdin.isatty()`) and no `--yes` | print manual steps; no sudo; exit 0 with warning |
| change needed, prompt declined | "Left SSH configuration unchanged"; exit 0 |
| change needed, consent | `sudo install -m 0644 TMP DROPIN` / `sudo rm -f DROPIN`; `sudo SSHD -t` with the resolved sshd path; Linux `sudo systemctl reload ssh` (fallback `sshd`); macOS no reload |
| `sshd -t` fails | revert (remove a newly written file / restore removed content), raise `OperationFailedError` with sshd's message |

Explanation text before the prompt names the file, the line, and that sudo
is needed because the SSH server's configuration is system-wide.
