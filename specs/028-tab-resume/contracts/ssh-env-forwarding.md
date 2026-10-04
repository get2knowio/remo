# Contract: forwarding the tab key over SSH

## Client (`core/ssh.py::shell_connect`)

- New keyword `tab_key: str | None = None`.
- When `tab_key` is set: the ssh argv gains `-o SendEnv=REMO_TAB_KEY`, and
  ssh runs with `env={**os.environ, "REMO_TAB_KEY": tab_key}`. The process
  environment of remo itself is not mutated.
- When `tab_key` is `None`: argv and environment are exactly as today.
- Callers MUST pass only keys matching `^[0-9a-f]{32}$`.

## Host (`ansible/tasks/configure_dev_tools.yml`)

- `/etc/ssh/sshd_config.d/accept-tz.conf` content becomes:

```
# Allow clients to propagate their timezone and remo's per-tab resume key
AcceptEnv TZ REMO_TAB_KEY
```

- The existing `register: sshd_tz_config` + "Restart sshd" task restarts sshd
  only when the content changed; a second run is a no-op.
- Reaches every host type: the file is included by every
  `*_site.yml` / `*_configure.yml` play, including `ssh_configure.yml`.

## Compatibility

| Client | Host | Result |
|--------|------|--------|
| new | new | key forwarded, host records/looks up |
| new | old | sshd drops the variable silently; nothing recorded host-side; lookup → exit 4 ⇒ "not upgraded" |
| old | new | no variable sent; scripts record nothing; behaviour as today |
