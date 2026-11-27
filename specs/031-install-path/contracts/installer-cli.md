# Contract: `install.sh`

Served from `https://raw.githubusercontent.com/get2knowio/remo/main/install.sh` (and from every release tag).

## Flags (flag beats env twin)

| Flag | Env twin | Meaning |
|---|---|---|
| `--version VER` | `REMO_VERSION` | a specific release; a pre-release-shaped VER implies `--prerelease` (existing routing) |
| `--prerelease [VER]` / `--pre-release` | `REMO_PRERELEASE=true` | pre-release channel; bare → newest `rc-` pre-release via the API |
| `--with LIST` (repeatable, comma-separated) | `REMO_WITH` | `web` and/or any plugin name in the manifest |
| `--without LIST` (repeatable) | `REMO_WITHOUT` | remove from the recorded set |
| `--with-pkg SPEC` (repeatable) | — | third-party PEP 508 spec, from an index |
| `--with-url URL` (repeatable) | — | third-party wheel URL (https only) |
| `--dry-run` | — | print the command(s); download/verify nothing, write nothing |
| `--help` | — | usage, every URL in full |
| — | `REMO_REPO_SLUG` | `owner/name` (default `get2knowio/remo`) |

## Resolution

- stable, no version: `https://github.com/<repo>/releases/latest/download/manifest.json`. If that download fails (#290: release-please publishes `vX.Y.Z` before release.yml attaches its assets, and never attaches them if release.yml fails), fall back to `https://api.github.com/repos/<repo>/releases?per_page=20`: the newest release that is not a draft, not a pre-release, has a `v` tag, and carries a `manifest.json` asset; then fetch `https://github.com/<repo>/releases/download/<tag>/manifest.json`, with the tag check in force. When a newer stable release is listed without a manifest, a note on stderr names it as still being published and names the version installed instead. No such release → exit 1, naming the pending release when there is one; an exhausted rate limit is named, with `--version` as the next step.
- stable `VER`: `https://github.com/<repo>/releases/download/v<VER>/manifest.json`
- prerelease `VER`: `https://github.com/<repo>/releases/download/rc-<VER>/manifest.json`
- A named version (`--version VER`, `--prerelease VER`) is never substituted: if its manifest is missing, the installer exits 1 naming its tag, and no API call is made.
- bare `--prerelease`: from `https://api.github.com/repos/<repo>/releases?per_page=20`, the newest `rc-` release flagged as a pre-release, not a draft, that carries a `manifest.json` asset (the same skip and note as stable), then as above. Never falls back to stable. `--dry-run` still resolves (read-only) so the printed command is exact.
- The API listing is parsed with grep/sed/awk only (no `jq`, no `python3`): keys are matched only at the start of a comma-separated fragment, so text in release notes cannot pass for one, and a manifest is recognised by its `browser_download_url` (`…/releases/download/<tag>/manifest.json`).
- release.yml uploads `manifest.json` in its own step after every wheel, so a manifest's presence means the release is complete.

## Set computation

`requested = recorded(extras,plugins,with_pkg,with_url) ∪ --with ∪ --with-pkg ∪ --with-url − --without`. A recorded record with a different `installer` than the one chosen is still honoured for its set. Names in `--without` that are not in the set: info line, not an error.

## Steps (non-dry-run)

1. Validate flags; refuse Windows shells (unchanged).
2. Choose installer (unchanged: uv, else pipx, else bootstrap uv).
3. Fetch manifest; refuse if missing (HTTP error) naming the release and suggesting a newer version; refuse tag/URL mismatch, and a manifest whose `tag` is not exactly `v<release>`/`rc-<release>` or whose remo-cli version is not its `release`.
4. Validate names against manifest (unknown → exit 1 before any download).
5. Download needed wheels into a `mktemp -d`; verify sha256 (`sha256sum` or `shasum -a 256`; neither → exit 1); mismatch → exit 1, nothing installed.
6. Install: uv — one `uv tool install --force "remo-cli[EXTRAS] @ file://…" [--with "<dist> @ file://…"]… [--with "<pkg>"]… [--with "<url>"]…`; pipx — `pipx install --force "remo-cli[EXTRAS] @ file://…"` then `pipx inject remo-cli …` per addition.
7. On success: uninstall a copy under the other tool manager if present; write the record atomically; completion + PATH hints (unchanged).

## Dry-run stdout

Exactly the install command line(s), one per line, with `<file://…>` paths shown as the manifest URLs (no download happens). Diagnostics on stderr.

## Exit codes

0 success / dry-run; 1 any refusal or failure (message on stderr names the cause and a next step).
