# Research: Make deacon the Default Devcontainer Runtime

**Feature**: `026-deacon-default-runtime` | **Date**: 2026-09-29 | Grounded against `main` @ `d9c0b3e` (ansible/ unchanged since `680dbf7` for these files) and deacon v0.4.0 (binary run locally; source read at tag `v0.4.0`).

## R1. Pin: 0.2.0-rc.11 → 0.4.0 (verified)

- `gh release list -R get2knowio/deacon`: v0.4.0 (2026-08-16, Latest), v0.3.0, v0.2.0, then rc.15…rc.7. The role's musl asset name pattern `deacon-v{ver}-{arch}-unknown-linux-musl.tar.gz` exists for v0.4.0 (x86_64 and aarch64).
- `ansible/roles/deacon/tasks/main.yml` extracts `[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?` from `deacon --version` (`deacon 0.4.0` → `0.4.0`) and compares to `deacon_version` exactly, so the bump keeps idempotency: an installed 0.4.0 → no change; an installed rc → reinstall.
- **Decision**: `deacon_version: "0.4.0"`; defaults comment rewritten (stable release, no longer "no stable release exists").

## R2. Flags remo invokes, verified against the v0.4.0 binary (2026-09-29, `deacon-v0.4.0-aarch64-apple-darwin`)

| Call site | Invocation | Verified |
|-----------|-----------|----------|
| `devshell.sh.j2:87`, `project-launch.sh.j2:99`, project-menu heredoc in `user_setup/tasks/main.yml:396` | `up --workspace-folder DIR --trust-workspace-persist [--remove-existing-container]` | `up --help` lists `--workspace-folder <PATH>`, `--trust-workspace-persist` (persists to `trusted_workspaces.json`; conflicts with `--trust-workspace`), `--remove-existing-container` |
| `remo-host.sh.j2:801` (`projects rebuild`) | `up --workspace-folder DIR --remove-existing-container [--build-no-cache]` | `--build-no-cache` present |
| `devshell.sh.j2:92-96`, `project-launch.sh.j2:101`, `user_setup/tasks/main.yml:424` | `exec --workspace-folder DIR …` | `exec --help`: `--workspace-folder <PATH>` (config discovery and container identity anchored to it) |
| `remo-host.sh.j2` | `DEVCONTAINER_BIN` name only | n/a |

- deacon issues named by the prompt are all **closed**: #117 (2026-05-28, `--remove-existing-container` re-runs `onCreateCommand`), #371 (2026-08-07, previous container left running), #688 (2026-08-26, racing removal), #265 (2026-07-05, state isolation). v0.4.0 (2026-08-16) predates #688's close, so a rebuild racing a concurrent `down` may still exit 1 on 0.4.0; remo never runs `down` concurrently with `up` (rebuild kills the Zellij session, then execs `up`), so it is not on remo's path. Recorded, not worked around.

## R3. The rebuild guards are stale (decision)

`remo-host.sh.j2:92-98` (omit `projects.rebuild` unless `DEVCONTAINER_BIN == devcontainer`) and `:754-758` (`cmd_projects_rebuild` exits 4 for non-`devcontainer`). **Decision**: both removed. The omission mechanism is kept as a data-driven check: `REBUILD_UNSUPPORTED_RUNTIMES` (empty today) — hmm, no: keeping dead configuration invites drift. Instead the mechanism stays as code shape: `rebuild_supported=true` computed once from `DEVCONTAINER_BIN` against a case list (`devcontainer|deacon) true`, `*) false`), used by both the advertisement and the verb. A test renders with `devcontainer_cli_bin="nope"` and asserts omission + exit 4, and with `deacon` asserts presence + pass-through. Protocol version unchanged (`PROTOCOL_VERSION=1`, client range `(1, 1)`).

## R4. Nested overlayfs: what deacon does (source, v0.4.0)

- Image/Dockerfile builds: `crates/deacon/src/commands/build/mod.rs:2070` builds `docker buildx build …`; `--buildkit auto|never` (`:1453-1466`) reads `DOCKER_BUILDKIT`, defaulting to BuildKit; `cmd.env("DOCKER_BUILDKIT", "1"|"0")` (`:2463-2465`). `docker buildx build` honours `BUILDX_BUILDER`, exactly like the reference CLI's path.
- Compose builds: `execute_compose_build`/`execute_compose_build_with_features` → `compose_manager.build_service` (`docker compose build`); the reference CLI's Compose fix (`DOCKER_BUILDKIT=1 COMPOSE_BAKE=1`) targets the same `docker compose build`.
- updateUID: `crates/core/src/user_mapping.rs` — "Docker-based implementation … executes commands via `docker exec`" (`:531`), i.e. deacon remaps the user inside the running container rather than running a plain `docker build`; the reference CLI's `DOCKER_BUILDKIT=0`-for-updateUID problem therefore does not apply to deacon.
- **So**: on an affected host deacon would need `BUILDX_BUILDER=remo-native` for image builds and `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` for Compose builds, with no updateUID caveat — a *simpler* shim than the reference CLI's. Not run on any real OrbStack host in this run.
- **Decision (outcome 3, conditional flip)**: `auto` → `devcontainer` when `docker_nested_overlayfs` is true; forcing deacon there is allowed and warned. A tracked issue carries the candidate environment and the proof-or-shim work; `docs/nested-overlayfs.md` says "not verified".

## R5. `auto` and the marker (decision)

- Values: `auto` (new default), `deacon`, `devcontainer`. CLI: `DEVCONTAINER_RUNTIMES = ("auto", "deacon", "devcontainer")`, `DEFAULT_DEVCONTAINER_RUNTIME = "auto"`; `resolve_devcontainer_runtime` unchanged in shape; the proxmox provider still passes `-e devcontainer_runtime=<value>` (now `auto` by default). Registry stores nothing about the runtime (verified: no `devcontainer_runtime` in `core/registry.py`/`models/host.py`).
- Ansible: `ansible/tasks/resolve_devcontainer_runtime.yml`, included at the top of `tasks/configure_dev_tools.yml` (so all nine configure/site playbooks inherit it), computes `devcontainer_runtime_effective`:
  1. `devcontainer_runtime` in `['deacon','devcontainer']` → that value.
  2. else marker `/home/{{ remo_user }}/.remo-devcontainer-runtime` exists and its trimmed content is `deacon`/`devcontainer` → that value (source `marker`).
  3. else the reference CLI is present (`npm prefix -g` rc 0 and `<prefix>/bin/devcontainer` exists — the same probe `nested_docker` uses) → `devcontainer` (source `legacy`).
  4. else `docker_nested_overlayfs | default(false) | bool` → `devcontainer` (source `nested-overlayfs`).
  5. else `deacon` (source `default`).
  A task named `"Devcontainer runtime: {{ effective }} ({{ source }})"` makes the decision visible in the filtered runner output. Because `remo_user`'s home may not exist yet on a fresh host, the marker/legacy probes are `stat`/`command` with `failed_when: false` and `| default()`.
- Marker write: after `include_role: user_setup` (which creates the account), `ansible.builtin.copy` `content: "{{ devcontainer_runtime_effective }}\n"` to the marker, `owner/group: remo_user`, `mode: '0644'` — content-compare keeps the second run at "ok". Written only when `configure_devcontainers` is on (a `--skip devcontainers` run installs nothing and records nothing).
- Consumers: `configure_dev_tools.yml`'s two `include_role` conditions, and `user_setup/defaults/main.yml`'s `devcontainer_cli_bin`/`devcontainer_up_extra_args` now key on `devcontainer_runtime_effective | default(devcontainer_runtime)`; with `auto` unresolved (a role run outside the shared task file) the fallback maps to the reference CLI, which is the safe choice.
- `nested_docker`'s "shim SKIPPED" message and `docs/nested-overlayfs.md`'s deacon section are updated for the conditional default.

## R6. Existing hosts and mixed runtimes (decision)

- Stickiness (R5 rules 2–3) means `upgrade`/`configure` never flips a legacy host. An explicit flag rewrites the marker.
- deacon #265 (closed): deacon stamps `devcontainer.source=deacon` and only ever sweeps containers carrying that label (`container.rs:1490,1909`); its images are `deacon-devcontainer-features:<hash>`, the reference CLI's are `vsc-*`. So after a switch the previous runtime's containers are neither adopted nor removed. `docs/proxmox.md` already prescribes `touch ~/projects/<p>/.devcontainer-rebuild` for a fresh rebuild on first launch; the docs now also say: stop projects first (`project-launch` stops the container on exit; or `docker stop`), the old containers/images are left for the operator (`docker ps -a --filter label=devcontainer.local_folder=…`), and two runtimes on one host is unsupported (configure wires one; the other binary is inert).

## R7. Tests

- `tests/unit/test_ansible_templates.py`: replace the two "deacon omits/refuses rebuild" tests with: deacon advertises + proceeds (exit 2 on missing `--json` after the runtime check, proving the check passed); `nope` omits + exits 4.
- New `tests/ansible/test_devcontainer_runtime_resolution.py`: parse `tasks/resolve_devcontainer_runtime.yml` and `configure_dev_tools.yml` structurally (resolver included first; both runtime roles gated on the effective value and the toggle; marker task after `user_setup`; warning task gated on deacon + nested overlayfs; every registered access `| default()`), and render the resolver's Jinja expressions with jinja2 for the shapes in spec US1/US3 (explicit, marker, legacy, nested, fresh, invalid marker).
- `tests/ansible/test_deacon_role.py`: pin is stable semver; the download URL renders for both arches; the regex extracts `0.4.0` from `deacon 0.4.0` and the equality check is exact (rc.1 vs rc.11 case retained).
- CLI: `tests/unit/providers/test_proxmox_devcontainer_runtime.py` default → `auto`; `auto` accepted explicitly; env `auto`; help text.
- Docs tests: `tests/unit/test_docs_structure.py` (diagram), plus a small grep test that `docs/nested-overlayfs.md` states "not verified" for deacon and the conditional rule.

## R8. Constitution IX

Ansible-only + a CLI default change: no packaging surface (no entry points, package data, extras or build config). Tier 1 (`uv tool install git+…@026-deacon-default-runtime`) suffices for any live check; stated in the PR.

## R9. Deferred → issues

1. Prove deacon on a nested-overlayfs (OrbStack) host with `BUILDX_BUILDER=remo-native` (+ `DOCKER_BUILDKIT=1 COMPOSE_BAKE=1` for Compose), or build the deacon shim; then drop the conditional.
2. Manual gate SC-005 (fresh host with the default; `capabilities`; project launch; `remo web` attach; configure twice; nested-overlayfs Compose build with deacon forced).
3. Upstream: deacon v0.4.0 predates #688's fix; bump the pin when the next stable ships.
