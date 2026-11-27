# Data Model: One Install Path for Every Release

## Release manifest (`manifest.json`, one per GitHub release)

| Field | Type | Rule |
|---|---|---|
| `format` | int | `1` |
| `release` | str | remo-cli version (PEP 440), e.g. `5.0.0`, `5.0.0rc1` |
| `tag` | str | `v<release>` (stable) or `rc-<release>` (prerelease) |
| `channel` | str | `stable` \| `prerelease`; must agree with `tag` prefix |
| `repo` | str | `owner/name` |
| `components[]` | list | exactly one `kind: cli` entry named `remo-cli`; ≥0 `kind: plugin` |

Component:

| Field | Type | Rule |
|---|---|---|
| `name` | str | install name: `remo-cli`, or the plugin's short name = dist minus `remo-provider-` (`ec2`, `hetzner`); unique; `[a-z0-9-]+`; never `web` |
| `dist` | str | distribution name from wheel METADATA |
| `version` | str | from wheel METADATA (plugins keep their own versions) |
| `kind` | str | `cli` \| `plugin` |
| `file` | str | wheel file name |
| `url` | str | `https://github.com/<repo>/releases/download/<tag>/<file>` exactly |
| `sha256` | str | 64 lowercase hex |

Invariants (checked by `release_manifest.py verify` and, for URL/tag, by install.sh): every wheel in the dist dir is listed; each listed file exists with matching sha256; the cli component's version equals `release`.

## Install record (`<REMO_HOME>/install.json`)

| Field | Type | Rule |
|---|---|---|
| `format` | int | `1`; other values → warn + ignore |
| `repo` | str | `owner/name` installed from |
| `channel` | str | `stable` \| `prerelease` |
| `version` | str | installed remo-cli version |
| `installer` | str | `uv` \| `pipx` |
| `extras` | list[str] | subset of `{web}` |
| `plugins` | list[str] | plugin install names, sorted, unique |
| `with_pkg` | list[str] | third-party PEP 508 specs (`NAME[==VER]`) |
| `with_url` | list[str] | third-party wheel URLs |
| `updated_at` | str | UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` |

State transitions: absent → written after first successful install; each later successful install rewrites it with `(recorded ∪ requested) − without`, channel/version of the new install; `--dry-run` and failures never write.

## Install origin (derived, not stored)

`recorded` (valid record) · `source` (no record; PEP 610 `dir_info`) · `pypi` (no record; no direct URL) · `unknown`.

`pypi` stays after the 2026-11-27 amendment (remo is no longer uploaded to PyPI): it describes existing installs of the frozen PyPI versions, which are steered to the installer. No origin's update lookup consults PyPI any more — `recorded` follows its record's repo and channel, every other origin the newest GitHub stable release of `get2knowio/remo`. The passive-notice cache file is `latest_version_cache.<channel>` (`stable` for an origin without a record); the pre-031 un-suffixed `latest_version_cache`, which held PyPI lookups, is no longer read.
