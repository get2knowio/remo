# Contract: Release manifest (`manifest.json`)

Attached to every GitHub release (stable `v<ver>`, RC `rc-<ver>`) beside the wheels.
Produced and checked only by `scripts/release_manifest.py`; fields in [data-model.md](../data-model.md).

## Layout (normative — install.sh parses it line by line)

```json
{"format":1,"release":"5.0.0rc1","tag":"rc-5.0.0rc1","channel":"prerelease","repo":"get2knowio/remo","components":[
{"name":"remo-cli","dist":"remo-cli","version":"5.0.0rc1","kind":"cli","file":"remo_cli-5.0.0rc1-py3-none-any.whl","url":"https://github.com/get2knowio/remo/releases/download/rc-5.0.0rc1/remo_cli-5.0.0rc1-py3-none-any.whl","sha256":"<64 hex>"},
{"name":"ec2","dist":"remo-provider-ec2","version":"0.1.0","kind":"plugin","file":"remo_provider_ec2-0.1.0-py3-none-any.whl","url":"https://github.com/get2knowio/remo/releases/download/rc-5.0.0rc1/remo_provider_ec2-0.1.0-py3-none-any.whl","sha256":"<64 hex>"},
{"name":"hetzner","dist":"remo-provider-hetzner","version":"0.1.0","kind":"plugin","file":"…","url":"…","sha256":"<64 hex>"}
]}
```

- Line 1: header object opening, ending in `"components":[`. Exactly the key order shown, no spaces.
- One component per line, key order `name,dist,version,kind,file,url,sha256`, separated by `,` at line end except the last.
- Final line: `]}`. Trailing newline.
- Components ordered: `remo-cli` first, then plugins by name.
- The whole file is valid JSON (`json.load` round-trips; tests assert it).

## `scripts/release_manifest.py`

```
release_manifest.py generate --dist DIR --tag TAG --channel {stable,prerelease} --repo OWNER/NAME [--out FILE]
release_manifest.py verify   --dist DIR --manifest FILE [--tag TAG]
```

- `generate` reads every `*.whl` in DIR (sdists ignored), takes `Name`/`Version` from each wheel's METADATA, classifies `remo-cli` as `cli` and every `remo-provider-*` as `plugin`, refuses any other wheel, and writes the layout above. Exit 0; exit 2 on refusal (message names the file).
- `verify` exits non-zero, naming the first problem, when: layout/JSON invalid; channel disagrees with tag prefix (`v` ↔ stable, `rc-` ↔ prerelease); tag is not exactly `v<release>` (stable) or `rc-<release>` (prerelease) — `generate` refuses the same mismatch against the remo-cli wheel's version; cli version ≠ `release`; a wheel in DIR missing from the manifest or vice versa; sha256 mismatch; any url not exactly `https://github.com/<repo>/releases/download/<tag>/<file>`; duplicate `name`; a plugin named `web`.
- Stdlib only. Runs in CI (`wheel-install` job, for a stable-shaped and an RC-shaped tag), `release.yml` build job, `dev-build.yml` prerelease job.
