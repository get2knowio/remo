# Changelog

## [4.2.0](https://github.com/get2knowio/remo/compare/v4.1.0...v4.2.0) (2026-08-05)


### Features

* **web:** flatten a tiling from ⊞, and hide terminal headers wholesale ([#139](https://github.com/get2knowio/remo/issues/139)) ([e5890f0](https://github.com/get2knowio/remo/commit/e5890f031c214b835ec8eab9ff85e8e343c12832))

## [4.1.0](https://github.com/get2knowio/remo/compare/v4.0.1...v4.1.0) (2026-08-04)


### Features

* **web:** tile a terminal to a pane edge as a master area ([#137](https://github.com/get2knowio/remo/issues/137)) ([41802fb](https://github.com/get2knowio/remo/commit/41802fb17ae5741918a31ee9d662aa90808ebb9a))

## [4.0.1](https://github.com/get2knowio/remo/compare/v4.0.0...v4.0.1) (2026-08-03)


### Bug Fixes

* **web:** let the terminal-theme menu scroll instead of being clipped ([#134](https://github.com/get2knowio/remo/issues/134)) ([ffb34e7](https://github.com/get2knowio/remo/commit/ffb34e72cc9d1744130130934538d38af217ec73))
* **web:** make Remo Light's white family visible — `white` and `brightWhite` were 1.27:1 and 1.09:1 against the background, hiding dim and bold text ([#134](https://github.com/get2knowio/remo/issues/134)) ([c5bc07a](https://github.com/get2knowio/remo/commit/c5bc07a7))

## [4.0.0](https://github.com/get2knowio/remo/compare/v3.1.0...v4.0.0) (2026-08-03)


### ⚠ BREAKING CHANGES

* **web:** drop the ghostty-web renderer, leaving xterm.js as the engine ([#130](https://github.com/get2knowio/remo/issues/130))
* **providers:** `remo proxmox`'s `--node-user` flag is now `--host-user`, on every verb that took it (create, upgrade, resize, tag, destroy, sync, info, host bootstrap). Scripts passing `--node-user` will fail with a Click "no such option" error naming the new spelling. Separately, a container whose registry entry has no recorded host user is now reached via ssh_config rather than as root — matching how create reached it.

### Features

* **web:** give the console a light/dark mode and selectable terminal themes ([#129](https://github.com/get2knowio/remo/issues/129)) ([7905c4b](https://github.com/get2knowio/remo/commit/7905c4b4f4d94403cc4a48c7a9858776774bf47c))


### Bug Fixes

* **ansible:** stop provisioning failing when GitHub rate-limits the zellij lookup ([#132](https://github.com/get2knowio/remo/issues/132)) ([cda5d0b](https://github.com/get2knowio/remo/commit/cda5d0b748bc90bb685b28510645d4fe29c35061))
* **ansible:** survive the SSM reconnect after the UID-1000 swap ([#133](https://github.com/get2knowio/remo/issues/133)) ([1a76f92](https://github.com/get2knowio/remo/commit/1a76f92107e3bd6fb7bfce60e51a0d284a0f4dcf))
* **devcontainer:** point CLAUDE_CONFIG_DIR at the mounted ~/.claude ([#125](https://github.com/get2knowio/remo/issues/125)) ([b9da45d](https://github.com/get2knowio/remo/commit/b9da45ddc4ed6f513a895e54de9e2e0dd16fe436))
* **providers:** spell the Proxmox node login `--host-user`, and record it honestly ([#124](https://github.com/get2knowio/remo/issues/124)) ([830d4d6](https://github.com/get2knowio/remo/commit/830d4d65ecab5c9e78b019323b3ad8b9d926f474)), closes [#106](https://github.com/get2knowio/remo/issues/106) [#107](https://github.com/get2knowio/remo/issues/107)
* **release:** keep uv.lock's version in step with the release bump ([#126](https://github.com/get2knowio/remo/issues/126)) ([137fa55](https://github.com/get2knowio/remo/commit/137fa55dead73c2da07037d22480e788332f9bb3))
* unwedge apt, stop authorized_keys being clobbered, and self-heal `remo web push` ([#123](https://github.com/get2knowio/remo/issues/123)) ([679fab9](https://github.com/get2knowio/remo/commit/679fab9f84b90fa33d3ef6eb2f58e568430c9fd9))
* **web:** make the console's background poll actually re-run discovery ([#128](https://github.com/get2knowio/remo/issues/128)) ([6d18d37](https://github.com/get2knowio/remo/commit/6d18d374023b2d6f1dccf4535016d00bfde92b12))
* **web:** recover from an expired access-proxy session without a manual reload ([#131](https://github.com/get2knowio/remo/issues/131)) ([3ce3e8e](https://github.com/get2knowio/remo/commit/3ce3e8ea9c1ab3c568b128356e5884631465df9f))


### Code Refactoring

* **web:** drop the ghostty-web renderer, leaving xterm.js as the engine ([#130](https://github.com/get2knowio/remo/issues/130)) ([c3e9170](https://github.com/get2knowio/remo/commit/c3e917044c756e1138817b70380177d636824ab2))

## [3.1.0](https://github.com/get2knowio/remo/compare/v3.0.0...v3.1.0) (2026-07-29)


### Features

* **cli:** warn when fish won't autoload the completion we just wrote ([#118](https://github.com/get2knowio/remo/issues/118)) ([759780d](https://github.com/get2knowio/remo/commit/759780d1eab3b39409cd315e22bd16ad0ab126ab))

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
