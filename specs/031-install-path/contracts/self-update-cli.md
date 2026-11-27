# Contract: `remo self-update` and the update notice

```
remo self-update [--check] [--yes]
```

| Origin | `--check` | default |
|---|---|---|
| recorded, newer exists | prints `remo <cur> → <new> (<channel>)`, exit 0 | shows the exact command, asks to proceed (skip with `--yes`), fetches `https://raw.githubusercontent.com/<repo>/<tag>/install.sh`, runs `bash <tmp> --version <new>` or `--prerelease <new>` + recorded `--with`/`--with-pkg`/`--with-url`; exit = installer's (0 or 1) |
| recorded, up to date | `remo <cur> is the newest <channel> release`, exit 0 | same, nothing run |
| pypi | prints the one-time switch one-liner, exit 0 | same guidance, nothing run, exit 0 |
| source | `Running from a source checkout: update it with git pull && uv sync`, exit 0 | same |
| unknown / invalid record | prints the reason and both commands, exit 1 (`PreconditionError`) | same |

Release lookup (`core/version.get_latest_github_release(repo, channel)`, shared by self-update and the passive notice): one call to `https://api.github.com/repos/<repo>/releases?per_page=20` for both channels (not `releases/latest`, which can name a release whose assets are not attached yet, #290). The answer is the newest release on the channel that carries a `manifest.json` asset: stable means not a draft, not a pre-release, a `v` tag; prerelease means not a draft, flagged as a pre-release, an `rc-` tag. A release without the asset is skipped. None found → `ReleaseLookupError` (naming the newest release still being published, when there is one).

Failures: release lookup error (network, rate limit — named), installer download failure, installer non-zero exit → `OperationFailedError`, exit 1; declining the prompt → `UserAbortedError`, exit 3. Mapped by `@provider_command`.

Switch one-liner (written in full everywhere):

```
curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash -s -- --with <recorded-or-detected extras/plugins>
```

(For a PyPI install the `--with` list is derived from installed plugin distributions + whether the `web` extra's FastAPI is importable; omitted when empty.)

## Passive notice (`core/version.check_for_updates_passive`)

- recorded: `Update available: v<cur> → v<new>. Run 'remo self-update' to upgrade.` — lookup via GitHub on the record's channel.
- pypi: `Update available: v<cur> → v<new>. Switch to the installer once: curl -fsSL https://raw.githubusercontent.com/get2knowio/remo/main/install.sh | bash` — lookup via PyPI (today's source).
- source / unknown: today's text unchanged.
- Hard-coded hints elsewhere (`cli/shell.py` "Consider: uv tool upgrade remo-cli", `core/platform.py`, `cli/web.py`, `core/provider_registry.py`) name the installer (`--with <extra>`) instead of PyPI commands.
