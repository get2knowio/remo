# Contract: Install record (`<REMO_HOME>/install.json`)

`REMO_HOME` resolves as in `core/config._resolve_remo_home` and install.sh `CONFIG_DIR`: `$REMO_HOME`, else `$XDG_CONFIG_HOME/remo`, else `~/.config/remo`. Fields: [data-model.md](../data-model.md).

## Layout (normative — written by bash, read by bash and Python)

```json
{"format":1,
"repo":"get2knowio/remo",
"channel":"prerelease",
"version":"5.0.0rc1",
"installer":"uv",
"extras":["web"],
"plugins":["ec2","hetzner"],
"with_pkg":[],
"with_url":[],
"updated_at":"2026-11-27T15:00:00Z"}
```

One key per line in this order. Every value matches `^[A-Za-z0-9._:/@+=,~%-]+$` (validated before writing; a `--with-pkg`/`--with-url` value outside it is refused). Lists sorted and de-duplicated.

## Rules

- Written only after a successful install (never on `--dry-run`/failure), via `install.json.tmp.$$` + `mv`, mode 0644, directory created 0700 if absent.
- Readers: install.sh (grep/sed per line); `remo_cli.core.install_record.load_record()` (`json.load` + validation → `InstallRecord` or `None` plus a reason).
- Invalid JSON, unknown `format`, or failing validation → readers treat it as absent and warn once (installer) / report the reason (`remo self-update`).
