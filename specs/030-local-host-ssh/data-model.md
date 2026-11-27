# Data Model: Reach a Local Host over SSH Too

## HostInfo (host side `~/.local/state/remo/host-info`; client `models/host_info.py`)

| Key | Values | Meaning |
|---|---|---|
| `host_kind` | `local` \| `remote` | written from `remo_local_install` |
| `menu_command` | `1` | host scripts support the explicit menu start + guarded hook |
| `installed_by` | remo version or empty | version that rendered the scripts |

- File mode 0644; one `key=value` per line; unknown keys ignored; malformed
  lines skipped. Missing file ⇒ `HostInfo()` (`host_kind=None`,
  `menu_command=False`).
- Client: `HostInfo.parse(text) -> HostInfo` (pure, never raises);
  `is_local_managed` ⇔ `host_kind == "local"`.

## HostToolsProbe (`core/ssh.py`)

`version: str | None`, `error: str | None`, `info: HostInfo`.
Same error semantics as today's `check_remote_version`.

## RemoteCapability (+ field)

`host_kind: str | None = None` — from capabilities JSON `host_kind`, absent on
older hosts.

## local.env (+ line)

`REMO_PATH=<colon-separated absolute dirs>` (may be empty). Existing
`REMO_BASH`, `REMO_PROJECTS_DIR` unchanged.

## Session directory (macOS)

`$HOME/.local/state/remo/zellij`, dir 0700 owned by the user. Valid ⇔ exists,
owned by `id -u`, no group/other bits. Linux: not used.

## SSH resume drop-in

`/etc/ssh/sshd_config.d/remo-accept-env.conf`, 0644 root, exact content:

    # Added by remo local setup --allow-ssh-resume
    AcceptEnv TZ REMO_TAB_KEY

States: absent ⇄ present (only via the opt-in/opt-out).
