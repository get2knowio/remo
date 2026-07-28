# Changelog

## [3.0.0](https://github.com/get2knowio/remo/compare/v2.2.0...v3.0.0) (2026-07-28)


### ⚠ BREAKING CHANGES

* **cli:** `remo <provider> update` no longer exists (replaced by `upgrade`/`resize`/`tag`); flat `remo incus bootstrap` and `remo proxmox bootstrap` no longer exist (moved to `remo <type> host bootstrap`, with the host now positional instead of `--host`); the incus `--user` flag is renamed `--host-user` and the proxmox `--user` flag is renamed `--node-user`. No shim or alias is provided for any of these.
* removed the --yes/-y flag on `remo <provider> create` for all four providers. It never had any effect — creation has no confirmation prompt to skip. Remove it from scripts; no replacement is needed. --yes continues to work on destroy, sync, snapshot restore, snapshot delete, and remo remove.

### Features

* /release skill + dev-build workflow (cross-machine RC/dev wheels, no PyPI) ([#79](https://github.com/get2knowio/remo/issues/79)) ([bb0dc89](https://github.com/get2knowio/remo/commit/bb0dc891c23fd4239260cae3bc13dafe44154fb2))
* **add:** provider-neutral `remo add`/`remo remove` for SSH-reachable hosts (014) ([#77](https://github.com/get2knowio/remo/issues/77)) ([14b06dd](https://github.com/get2knowio/remo/commit/14b06dd994e71df0fe4d407459116aba1eecc9bb))
* **ci:** use a GitHub App token for release-please (fallback to PAT/GITHUB_TOKEN) ([#83](https://github.com/get2knowio/remo/issues/83)) ([5eb6848](https://github.com/get2knowio/remo/commit/5eb6848d2e12b9e5412b3302b51df49bf3d225e9))
* **cli:** add `completion install`, harden the fish script and stamp its version ([#112](https://github.com/get2knowio/remo/issues/112)) ([ab9c800](https://github.com/get2knowio/remo/commit/ab9c800cfecf7b050026605422de7e9e74f83632))
* **cli:** split `update` into `upgrade`/`resize`/`tag`, move `bootstrap` under `host` ([#111](https://github.com/get2knowio/remo/issues/111)) ([852461c](https://github.com/get2knowio/remo/commit/852461cfe87719b90c7ca68e639d7c365089b42b))
* **incus/proxmox:** tag managed containers and filter sync by default ([#76](https://github.com/get2knowio/remo/issues/76)) ([bd45b0c](https://github.com/get2knowio/remo/commit/bd45b0c10ab9fb350b890fc3c4fb00f77e42adaa))
* **providers:** formal provider abstraction — descriptor + Protocol + CLI factory (018) ([#90](https://github.com/get2knowio/remo/issues/90)) ([11196dc](https://github.com/get2knowio/remo/commit/11196dc020668f79a4459f642d6af06fb1cef981)), closes [#87](https://github.com/get2knowio/remo/issues/87)
* **registry:** versioned structured host registry (registry v2) ([#85](https://github.com/get2knowio/remo/issues/85)) ([2a3d92e](https://github.com/get2knowio/remo/commit/2a3d92e1b24f99523223e6b5a5d8f611a38ea53a))
* **sync:** shared reconcile engine across all four providers (016) ([#88](https://github.com/get2knowio/remo/issues/88)) ([fb58da3](https://github.com/get2knowio/remo/commit/fb58da35fcfeb93bd03380a3b9d266e6001a0718))
* **web:** schema-derived frontend types + drift checks (020) ([#99](https://github.com/get2knowio/remo/issues/99)) ([f13d667](https://github.com/get2knowio/remo/commit/f13d6678afd35b7a952fc8ec55cd4fc15c2f252b))
* **web:** unify push flow, offline drift, revocation & flap detection (017) ([#89](https://github.com/get2knowio/remo/issues/89)) ([f9863c6](https://github.com/get2knowio/remo/commit/f9863c6780f16b166c0344e7bab740daa67b53ec))


### Bug Fixes

* **ansible:** retry Docker apt refresh + install to survive repo-index flakiness ([#91](https://github.com/get2knowio/remo/issues/91)) ([d0794f2](https://github.com/get2knowio/remo/commit/d0794f2ef4b9a65d3c0fff59e9a7586df801458b))
* **ansible:** vendor NodeSource signing key, retry remaining key downloads ([#110](https://github.com/get2knowio/remo/issues/110)) ([6d7ebbf](https://github.com/get2knowio/remo/commit/6d7ebbfaed2cab2d0baccc3dffae3a049a59a9b1)), closes [#109](https://github.com/get2knowio/remo/issues/109)
* **ci:** fall back to GITHUB_TOKEN in release-please so it works without the PAT ([#80](https://github.com/get2knowio/remo/issues/80)) ([212aa6d](https://github.com/get2knowio/remo/commit/212aa6d3a260be33020617fb2b1f0fafbecbf932))
* **ci:** read registry.json (v2) in aws smoke SSM step, not legacy known_hosts ([#92](https://github.com/get2knowio/remo/issues/92)) ([2c0c283](https://github.com/get2knowio/remo/commit/2c0c28391775be157320a8ea7dbbf3ea8ef0f05e))
* **ci:** scope release-please to pyproject.toml only (leave __init__.py sentinel) ([#82](https://github.com/get2knowio/remo/issues/82)) ([00e750a](https://github.com/get2knowio/remo/commit/00e750a8d4be516e533a51849d1eaa0ff612cc4f))
* **cli:** detect the shell you're typing into, not $SHELL ([#115](https://github.com/get2knowio/remo/issues/115)) ([cb6803f](https://github.com/get2knowio/remo/commit/cb6803febe87a345ecfb69f8a4d3af3d828cfd44))
* **providers:** stop `remo shell` from touching the hypervisor, and say which machine `--user` means ([#105](https://github.com/get2knowio/remo/issues/105)) ([35c06bd](https://github.com/get2knowio/remo/commit/35c06bdcb6c504db4306805b716be66d88a3e87b))
* **skill:** repair three broken commands in the release skill ([#104](https://github.com/get2knowio/remo/issues/104)) ([3f5aef2](https://github.com/get2knowio/remo/commit/3f5aef2ad45588952a421145d31ccb865a10e0f0))


### Miscellaneous Chores

* dependency, dead-code & documentation hygiene pass (019) ([#95](https://github.com/get2knowio/remo/issues/95)) ([866f413](https://github.com/get2knowio/remo/commit/866f413620c4309148e5c9f21251ce186f18343d))

## Changelog

All notable changes to this project are documented in this file.

From version 2.3.0 onward this file is maintained automatically by
[release-please](https://github.com/googleapis/release-please) from Conventional
Commit messages; see [CONTRIBUTING.md](./CONTRIBUTING.md#release-process). Entries
for 2.2.0 and earlier live in the [GitHub Releases](https://github.com/get2knowio/remo/releases).
